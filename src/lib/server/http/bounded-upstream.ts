export class UpstreamConfigurationError extends Error {}

/** `bodyText` is capped to this many characters before retention; see `UpstreamHttpError`. */
const MAX_RETAINED_BODY_TEXT_CHARACTERS = 4_096;

export class UpstreamHttpError extends Error {
  /**
   * The upstream's raw response body, read through the same byte/time bound as a successful call
   * and capped to `MAX_RETAINED_BODY_TEXT_CHARACTERS` on the way in -- enough for a structured
   * argument-refusal body, never enough to flood a log with an upstream's multi-hundred-KB error
   * page. Present only for `fetchBoundedJson` failures where the body was actually readable
   * (absent on an oversized/unreadable/empty body) -- callers must not assume it is set just
   * because the status is non-2xx. Lets a caller that recognises the upstream's own error shape
   * (e.g. a 400 argument-validation refusal) surface a bounded explanation instead of this
   * class's generic message, without a second network round-trip.
   *
   * Declared as a `get` accessor (own property on the prototype, not the instance) rather than a
   * constructor-assigned field so it stays non-enumerable: every `console.error(..., err)` /
   * `JSON.stringify(err)` call site across the ~15 `fetchBoundedJson` callers must not dump this
   * body into logs just because it rode along on the error object.
   */
  get bodyText(): string | undefined {
    return this.#bodyText;
  }

  readonly #bodyText?: string;

  constructor(public readonly status: number, bodyText?: string) {
    super(`Upstream request failed with status ${status}`);
    this.#bodyText = bodyText === undefined ? undefined : bodyText.slice(0, MAX_RETAINED_BODY_TEXT_CHARACTERS);
  }
}

export class UpstreamPayloadError extends Error {}

export class UpstreamTimeoutError extends Error {}

/** The CALLER walked away (client disconnect, superseded viewport); the upstream is not at fault. */
export class UpstreamAbortedError extends Error {}

interface BoundedJsonOptions {
  maxBytes: number;
  timeoutMs: number;
  /**
   * Next.js data-cache lifetime in seconds. Omit for `cache: "no-store"`.
   * See `src/lib/server/AGENTS.md` — upstream providers with per-key quotas
   * (NASA FIRMS) depend on this being honoured.
   */
  revalidateSeconds?: number;
  /**
   * The caller's cancellation, combined with (never replacing) this request's timeout.
   * See `src/lib/server/services/AGENTS.md` §request-cancellation.
   */
  signal?: AbortSignal;
}

/**
 * The caller's cancellation and this request's timeout as one signal.
 *
 * `AbortSignal.any` rather than a hand-rolled listener pair: the runtime this ships on
 * (`node:22.16` in `Dockerfile:1`) and the jsdom the suite runs under both implement it, and a
 * manual combiner has to own listener teardown that `any` does for free. The timeout is always
 * present, so dropping a caller signal can only ever make a request live LONGER than asked --
 * never longer than the bound.
 */
function boundedSignal(options: BoundedJsonOptions): AbortSignal {
  const timeout = AbortSignal.timeout(options.timeoutMs);
  return options.signal === undefined ? timeout : AbortSignal.any([options.signal, timeout]);
}

/** Build the caching half of a `RequestInit`, defaulting to no-store. */
function cachePolicyInit(options: BoundedJsonOptions): RequestInit {
  return options.revalidateSeconds === undefined
    ? { cache: "no-store" }
    : { next: { revalidate: options.revalidateSeconds } };
}

export function providerUrl(environmentName: string, developmentDefault: string): URL {
  const configured = process.env[environmentName]?.trim();
  if (!configured && process.env.NODE_ENV === "production") {
    throw new UpstreamConfigurationError(`${environmentName} is not configured`);
  }

  let url: URL;
  try {
    url = new URL(configured || developmentDefault);
  } catch {
    throw new UpstreamConfigurationError(`${environmentName} is invalid`);
  }
  if (url.protocol !== "http:" && url.protocol !== "https:") {
    throw new UpstreamConfigurationError(`${environmentName} must use HTTP or HTTPS`);
  }
  if (url.username || url.password || url.search || url.hash) {
    throw new UpstreamConfigurationError(`${environmentName} must be a service base URL`);
  }
  return url;
}

async function readBoundedBytes(response: Response, maxBytes: number): Promise<Uint8Array> {
  const contentLength = response.headers.get("content-length");
  if (contentLength) {
    const declaredBytes = Number(contentLength);
    if (!Number.isSafeInteger(declaredBytes) || declaredBytes < 0 || declaredBytes > maxBytes) {
      throw new UpstreamPayloadError("Upstream response exceeded the byte limit");
    }
  }

  const reader = response.body?.getReader();
  if (!reader) throw new UpstreamPayloadError("Upstream response was empty");

  const chunks: Uint8Array[] = [];
  let totalBytes = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      totalBytes += value.byteLength;
      if (totalBytes > maxBytes) {
        await reader.cancel();
        throw new UpstreamPayloadError("Upstream response exceeded the byte limit");
      }
      chunks.push(value);
    }
  } finally {
    reader.releaseLock();
  }

  const bytes = new Uint8Array(totalBytes);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return bytes;
}

interface BoundedFetchResult {
  ok: boolean;
  status: number;
  headers: Headers;
  bytes: Uint8Array;
  text: string;
  /**
   * Set when the body could not be read within the byte cap (or was absent, as
   * on 204/304). `ok`/`status`/`headers` are still accurate; `bytes`/`text` are
   * empty. Callers must branch on status before treating this as fatal.
   */
  bodyError: UpstreamPayloadError | null;
}

/**
 * Core bounded fetch: enforces a timeout and a response-size cap. Never throws
 * on a non-2xx status, and never throws on an unreadable/oversized/absent body
 * either — a body failure is reported as `bodyError` so that status-based
 * handling (429 backoff, 5xx "temporarily unavailable") stays reachable even
 * when the upstream returns a huge HTML error page. Callers decide how to
 * interpret the result. This is the escape hatch other helpers (and hand-rolled
 * call sites that need raw status/text access, e.g. relaying an upstream error
 * body) build on.
 *
 * A caller-cancelled request throws `UpstreamAbortedError`, never `UpstreamTimeoutError`: the
 * two look identical on the wire and mean opposite things. A timeout is a statement about the
 * upstream that a retry may plausibly beat; an abort is a statement about the CALLER, and
 * relabelling one as the other would report a client that navigated away as a service outage.
 * The caller's own signal decides, not the DOMException name, so a custom abort reason still
 * classifies correctly.
 */
export async function fetchBounded(
  url: string | URL,
  init: RequestInit,
  options: BoundedJsonOptions
): Promise<BoundedFetchResult> {
  let response: Response;
  try {
    response = await fetch(url, {
      ...init,
      signal: boundedSignal(options),
    });
  } catch (error) {
    if (options.signal?.aborted === true) {
      throw new UpstreamAbortedError("Upstream request was cancelled by its caller");
    }
    if (
      error instanceof DOMException &&
      (error.name === "AbortError" || error.name === "TimeoutError")
    ) {
      throw new UpstreamTimeoutError("Upstream request timed out");
    }
    throw error;
  }

  let bytes: Uint8Array = new Uint8Array(0);
  let bodyError: UpstreamPayloadError | null = null;
  try {
    bytes = await readBoundedBytes(response, options.maxBytes);
  } catch (error) {
    // Normalize post-header interruptions too; see ../AGENTS.md section upstream body failures.
    if (options.signal?.aborted === true) {
      throw new UpstreamAbortedError("Upstream request was cancelled by its caller");
    }
    if (
      error instanceof DOMException &&
      (error.name === "AbortError" || error.name === "TimeoutError")
    ) {
      throw new UpstreamTimeoutError("Upstream response body timed out");
    }
    if (error instanceof TypeError && (error.message === "terminated" || error.message === "fetch failed")) {
      bodyError = new UpstreamPayloadError("Upstream response body transport failed");
    } else if (error instanceof UpstreamPayloadError) {
      bodyError = error;
    } else {
      throw error;
    }
  }

  return {
    ok: response.ok,
    status: response.status,
    headers: response.headers,
    bytes,
    text: new TextDecoder().decode(bytes),
    bodyError,
  };
}

/** Fetch and parse a JSON response without allowing unbounded buffering. */
export async function fetchBoundedJson(
  url: string | URL,
  init: RequestInit,
  options: BoundedJsonOptions
): Promise<unknown> {
  const result = await fetchBounded(url, { ...init, ...cachePolicyInit(options) }, options);

  // Status first: a 429/5xx with an oversized or absent body must still surface
  // as an UpstreamHttpError so retry/backoff signalling survives. The body text rides along
  // (when it was actually readable) so a caller can inspect a structured error shape of its own.
  if (!result.ok) throw new UpstreamHttpError(result.status, result.bodyError ? undefined : result.text);
  if (result.bodyError) throw result.bodyError;
  const contentType = result.headers.get("content-type");
  if (contentType && !contentType.toLowerCase().includes("json")) {
    throw new UpstreamPayloadError("Upstream response was not JSON");
  }

  try {
    return JSON.parse(result.text);
  } catch {
    throw new UpstreamPayloadError("Upstream response contained invalid JSON");
  }
}

/** Fetch a text/CSV response without allowing unbounded buffering. */
export async function fetchBoundedText(
  url: string | URL,
  init: RequestInit,
  options: BoundedJsonOptions
): Promise<string> {
  const result = await fetchBounded(url, { ...init, ...cachePolicyInit(options) }, options);
  if (!result.ok) throw new UpstreamHttpError(result.status);
  if (result.bodyError) throw result.bodyError;
  return result.text;
}
