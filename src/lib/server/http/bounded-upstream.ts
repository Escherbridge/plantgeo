export class UpstreamConfigurationError extends Error {}

export class UpstreamHttpError extends Error {
  constructor(public readonly status: number) {
    super(`Upstream request failed with status ${status}`);
  }
}

export class UpstreamPayloadError extends Error {}

export class UpstreamTimeoutError extends Error {}

interface BoundedJsonOptions {
  maxBytes: number;
  timeoutMs: number;
  /**
   * Next.js data-cache lifetime in seconds. Omit for `cache: "no-store"`.
   * See `src/lib/server/AGENTS.md` — upstream providers with per-key quotas
   * (NASA FIRMS) depend on this being honoured.
   */
  revalidateSeconds?: number;
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
      signal: AbortSignal.timeout(options.timeoutMs),
    });
  } catch (error) {
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
    if (!(error instanceof UpstreamPayloadError)) throw error;
    bodyError = error;
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
  // as an UpstreamHttpError so retry/backoff signalling survives.
  if (!result.ok) throw new UpstreamHttpError(result.status);
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
