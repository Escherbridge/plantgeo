import { timingSafeEqual } from "crypto";

export const MAX_INGRESS_BODY_BYTES = 1_048_576;

type AuthorizationResult =
  | { authorized: true }
  | { authorized: false; status: 401 | 503; error: string };

type JsonBodyResult =
  | { ok: true; data: unknown }
  | { ok: false; status: 400 | 413; error: string };

interface ServiceCredentialOptions {
  secretEnvironmentVariable: "CRON_SECRET" | "INGEST_SECRET";
  headerName: "x-cron-secret" | "x-ingest-secret";
  requireConfigured?: boolean;
}

function isProduction(): boolean {
  return process.env.NODE_ENV === "production";
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

/**
 * Protect machine-to-machine ingress. A configured secret is always enforced;
 * production refuses to start an unauthenticated ingress boundary.
 */
function authorizeServiceRequest(
  request: Request,
  options: ServiceCredentialOptions
): AuthorizationResult {
  const expectedCredential = process.env[options.secretEnvironmentVariable]?.trim();

  if (!expectedCredential) {
    if (options.requireConfigured || isProduction()) {
      return {
        authorized: false,
        status: 503,
        error: `${options.secretEnvironmentVariable} is not configured`,
      };
    }

    return { authorized: true };
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

/** Authorize tracking producers without a development-mode bypass. */
export function authorizeTrackingIngressRequest(request: Request): AuthorizationResult {
  return authorizeServiceRequest(request, {
    secretEnvironmentVariable: "INGEST_SECRET",
    headerName: "x-ingest-secret",
    requireConfigured: true,
  });
}

/** Authorize the scheduled ingestion runner. */
export function authorizeCronRequest(request: Request): AuthorizationResult {
  return authorizeServiceRequest(request, {
    secretEnvironmentVariable: "CRON_SECRET",
    headerName: "x-cron-secret",
  });
}

/** Parse a request body without allowing an unbounded JSON allocation. */
export async function parseBoundedJson(
  request: Request,
  maxBytes = MAX_INGRESS_BODY_BYTES
): Promise<JsonBodyResult> {
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

  try {
    return { ok: true, data: JSON.parse(new TextDecoder().decode(buffer)) };
  } catch {
    return { ok: false, status: 400, error: "Invalid JSON body" };
  }
}
