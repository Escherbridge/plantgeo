import { NextResponse } from "next/server";
import {
  UpstreamConfigurationError,
  UpstreamHttpError,
  UpstreamPayloadError,
  UpstreamTimeoutError,
} from "@/lib/server/http/bounded-upstream";

export const PRIVATE_EPHEMERAL_HEADERS = {
  "Cache-Control": "private, no-store",
  "Referrer-Policy": "no-referrer",
  "X-Content-Type-Options": "nosniff",
} as const;

export function providerFailureResponse(error: unknown, serviceName: string): NextResponse {
  if (error instanceof UpstreamConfigurationError) {
    return NextResponse.json(
      { error: `${serviceName} is not configured` },
      { status: 503, headers: { ...PRIVATE_EPHEMERAL_HEADERS, "Retry-After": "60" } }
    );
  }
  if (error instanceof UpstreamTimeoutError) {
    return NextResponse.json(
      { error: `${serviceName} timed out` },
      { status: 504, headers: PRIVATE_EPHEMERAL_HEADERS }
    );
  }
  if (error instanceof UpstreamHttpError) {
    return NextResponse.json(
      { error: `${serviceName} is temporarily unavailable` },
      {
        status: 502,
        headers: {
          ...PRIVATE_EPHEMERAL_HEADERS,
          ...(error.status === 429 ? { "Retry-After": "30" } : {}),
        },
      }
    );
  }
  if (error instanceof UpstreamPayloadError) {
    return NextResponse.json(
      { error: `${serviceName} returned an invalid response` },
      { status: 502, headers: PRIVATE_EPHEMERAL_HEADERS }
    );
  }
  return NextResponse.json(
    { error: `${serviceName} is unavailable` },
    { status: 502, headers: PRIVATE_EPHEMERAL_HEADERS }
  );
}

export function publicRateLimitFailureResponse(
  result: { status: 429 | 503; retryAfter: number }
): NextResponse {
  return NextResponse.json(
    {
      error:
        result.status === 429
          ? "Too many provider requests"
          : "Provider request limiter is unavailable",
    },
    {
      status: result.status,
      headers: {
        ...PRIVATE_EPHEMERAL_HEADERS,
        "Retry-After": String(result.retryAfter),
      },
    }
  );
}
