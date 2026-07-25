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

/** Fetch and parse a JSON response without allowing unbounded buffering. */
export async function fetchBoundedJson(
  url: URL,
  init: RequestInit,
  options: BoundedJsonOptions
): Promise<unknown> {
  let response: Response;
  try {
    response = await fetch(url, {
      ...init,
      cache: "no-store",
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

  if (!response.ok) throw new UpstreamHttpError(response.status);
  const contentType = response.headers.get("content-type");
  if (contentType && !contentType.toLowerCase().includes("json")) {
    throw new UpstreamPayloadError("Upstream response was not JSON");
  }

  const bytes = await readBoundedBytes(response, options.maxBytes);
  try {
    return JSON.parse(new TextDecoder().decode(bytes));
  } catch {
    throw new UpstreamPayloadError("Upstream response contained invalid JSON");
  }
}
