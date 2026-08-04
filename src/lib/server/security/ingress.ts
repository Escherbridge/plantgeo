import { timingSafeEqual } from "crypto";

export const MAX_INGRESS_BODY_BYTES = 1_048_576;

type AuthorizationResult =
  | { authorized: true }
  | { authorized: false; status: 401 | 503; error: string };

type JsonBodyResult =
  | { ok: true; data: unknown }
  | { ok: false; status: 400 | 413; error: string };

type BodyBytesResult =
  | { ok: true; bytes: Uint8Array }
  | { ok: false; status: 400 | 413; error: string };

interface ServiceCredentialOptions {
  secretEnvironmentVariable: "CRON_SECRET" | "INGEST_SECRET";
  headerName: "x-cron-secret" | "x-ingest-secret";
}

function credentialsMatch(expected: string, provided: string): boolean {
  const expectedBuffer = Buffer.from(expected);
  const providedBuffer = Buffer.from(provided);

  return (
    expectedBuffer.length === providedBuffer.length &&
    timingSafeEqual(expectedBuffer, providedBuffer)
  );
}

function getProvidedCredential(request: Request, headerName: string): string | null {
  const headerCredential = request.headers.get(headerName);
  if (headerCredential) return headerCredential;

  const authorization = request.headers.get("authorization");
  const bearerMatch = authorization?.match(/^Bearer ([^\s]+)$/i);
  return bearerMatch?.[1] ?? null;
}

/** Fails closed in every environment; a missing secret is a 503. */
function authorizeServiceRequest(
  request: Request,
  options: ServiceCredentialOptions
): AuthorizationResult {
  const expectedCredential = process.env[options.secretEnvironmentVariable]?.trim();

  if (!expectedCredential) {
    return {
      authorized: false,
      status: 503,
      error: `${options.secretEnvironmentVariable} is not configured`,
    };
  }

  const providedCredential = getProvidedCredential(request, options.headerName);
  if (!providedCredential || !credentialsMatch(expectedCredential, providedCredential)) {
    return { authorized: false, status: 401, error: "Unauthorized" };
  }

  return { authorized: true };
}

/** Authorize a non-interactive data producer. */
export function authorizeIngressRequest(request: Request): AuthorizationResult {
  return authorizeServiceRequest(request, {
    secretEnvironmentVariable: "INGEST_SECRET",
    headerName: "x-ingest-secret",
  });
}

/** Authorize tracking producers; same INGEST_SECRET credential as above. */
export const authorizeTrackingIngressRequest = authorizeIngressRequest;

/** Authorize the scheduled ingestion runner. */
export function authorizeCronRequest(request: Request): AuthorizationResult {
  return authorizeServiceRequest(request, {
    secretEnvironmentVariable: "CRON_SECRET",
    headerName: "x-cron-secret",
  });
}

/**
 * Read a request body into memory while refusing to allocate past `maxBytes`.
 *
 * A declared Content-Length is rejected before a single byte is read; a chunked
 * body is cancelled the moment the running total crosses the ceiling, so an
 * omitted or lying header cannot buy an attacker an unbounded allocation.
 */
export async function readBoundedBody(
  request: Request,
  maxBytes = MAX_INGRESS_BODY_BYTES
): Promise<BodyBytesResult> {
  const contentLength = request.headers.get("content-length");
  if (contentLength) {
    const parsedContentLength = Number(contentLength);
    if (!Number.isSafeInteger(parsedContentLength) || parsedContentLength < 0) {
      return { ok: false, status: 400, error: "Invalid Content-Length header" };
    }
    if (parsedContentLength > maxBytes) {
      return { ok: false, status: 413, error: "Request body is too large" };
    }
  }

  if (!request.body) {
    return { ok: false, status: 400, error: "Request body is required" };
  }

  const reader = request.body.getReader();
  const chunks: Uint8Array[] = [];
  let totalBytes = 0;

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      totalBytes += value.byteLength;
      if (totalBytes > maxBytes) {
        await reader.cancel();
        return { ok: false, status: 413, error: "Request body is too large" };
      }
      chunks.push(value);
    }
  } catch {
    return { ok: false, status: 400, error: "Unable to read request body" };
  } finally {
    reader.releaseLock();
  }

  if (totalBytes === 0) {
    return { ok: false, status: 400, error: "Request body is required" };
  }

  const buffer = new Uint8Array(totalBytes);
  let offset = 0;
  for (const chunk of chunks) {
    buffer.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return { ok: true, bytes: buffer };
}

/** Parse a request body without allowing an unbounded JSON allocation. */
export async function parseBoundedJson(
  request: Request,
  maxBytes = MAX_INGRESS_BODY_BYTES
): Promise<JsonBodyResult> {
  const body = await readBoundedBody(request, maxBytes);
  if (!body.ok) return body;

  try {
    return { ok: true, data: JSON.parse(new TextDecoder().decode(body.bytes)) };
  } catch {
    return { ok: false, status: 400, error: "Invalid JSON body" };
  }
}
