import NextAuth from "next-auth";
import { NextResponse, type NextRequest } from "next/server";
import { authOptions } from "@/lib/server/auth-options";
import { checkRateLimit } from "@/lib/server/middleware/api-auth";
import {
  CREDENTIAL_SIGNIN_PER_ACCOUNT_PER_MINUTE,
  CREDENTIAL_SIGNIN_PER_IP_PER_MINUTE,
  credentialSignInAccountRateLimitKey,
  credentialSignInIpRateLimitKey,
} from "@/lib/server/security/registration";

const handler = NextAuth(authOptions);

/** The one unauthenticated NextAuth action that runs a bcrypt compare. */
const CREDENTIALS_CALLBACK_PATH = "/api/auth/callback/credentials";

/** Bounds the body we will read to find the submitted address. */
const MAX_SIGNIN_BODY_BYTES = 4_096;

/**
 * `signIn()` reads `data.url` off the response and parses it, so a limiter
 * rejection has to keep NextAuth's error-redirect shape or the client throws
 * instead of rendering a message.
 */
function signInRejection(
  request: NextRequest,
  errorCode: string,
  status: number,
  retryAfter?: number
): NextResponse {
  const url = new URL("/login", request.nextUrl.origin);
  url.searchParams.set("error", errorCode);
  return NextResponse.json(
    { url: url.toString() },
    {
      status,
      headers: retryAfter ? { "Retry-After": String(retryAfter) } : undefined,
    }
  );
}

/**
 * Reads the submitted address from a clone so the original body stays intact
 * for NextAuth. `signIn()` posts form-urlencoded; direct API clients post JSON.
 */
async function readSubmittedEmail(request: NextRequest): Promise<string | null> {
  try {
    const raw = await request.clone().text();
    if (raw.length > MAX_SIGNIN_BODY_BYTES) return null;

    const contentType = request.headers.get("content-type") ?? "";
    if (contentType.includes("application/json")) {
      const parsed: unknown = JSON.parse(raw);
      const email = (parsed as { email?: unknown } | null)?.email;
      return typeof email === "string" ? email : null;
    }
    const email = new URLSearchParams(raw).get("email");
    return email && email.length > 0 ? email : null;
  } catch {
    return null;
  }
}

/**
 * Applies a per-IP and a per-account budget before the bcrypt compare in
 * `authorize()`. Returns null when the request may proceed.
 *
 * `authorize()` never sees the raw Request, so the IP half can only be done
 * here; keeping both halves together also keeps the fail-closed behaviour
 * identical to the other unauthenticated auth routes.
 */
async function enforceCredentialSignInLimits(
  request: NextRequest
): Promise<NextResponse | null> {
  const email = await readSubmittedEmail(request);
  const keys: Array<{ key: string; limit: number }> = [
    {
      key: credentialSignInIpRateLimitKey(request),
      limit: CREDENTIAL_SIGNIN_PER_IP_PER_MINUTE,
    },
  ];
  if (email) {
    keys.push({
      key: credentialSignInAccountRateLimitKey(email),
      limit: CREDENTIAL_SIGNIN_PER_ACCOUNT_PER_MINUTE,
    });
  }

  for (const { key, limit } of keys) {
    const result = await checkRateLimit(key, limit);
    if (result.available && result.limited) {
      return signInRejection(request, "RateLimited", 429, result.retryAfter);
    }
    if (!result.available && process.env.NODE_ENV === "production") {
      return signInRejection(request, "SignInUnavailable", 503, 30);
    }
  }

  return null;
}

type NextAuthRouteContext = { params: Promise<{ nextauth: string[] }> };

export const GET = handler;

export async function POST(
  request: NextRequest,
  context: NextAuthRouteContext
): Promise<Response> {
  if (request.nextUrl.pathname === CREDENTIALS_CALLBACK_PATH) {
    const rejection = await enforceCredentialSignInLimits(request);
    if (rejection) return rejection;
  }
  return handler(request, context);
}
