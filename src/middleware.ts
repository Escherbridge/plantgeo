import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";
import { getToken } from "next-auth/jwt";

/** Pages that require a session. */
const PROTECTED_PATHS = ["/dashboard", "/onboarding"];

/**
 * Invitation and join-link landing pages must render for logged-out visitors so
 * they can register first; the matcher below already excludes them, and this
 * list keeps the guarantee explicit if the matcher ever widens.
 */
const PUBLIC_PATHS = ["/invite", "/join"];

/** Where an authenticated user without an organization is sent. */
const ONBOARDING_PATH = "/onboarding";

function isUnder(pathname: string, base: string): boolean {
  return pathname === base || pathname.startsWith(`${base}/`);
}

export async function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;

  if (PUBLIC_PATHS.some((p) => isUnder(pathname, p))) {
    return NextResponse.next();
  }

  const isProtectedPage = PROTECTED_PATHS.some((p) => isUnder(pathname, p));
  if (!isProtectedPage) return NextResponse.next();

  const token = await getToken({ req: request });
  if (!token) {
    const loginUrl = new URL("/login", request.url);
    loginUrl.searchParams.set("callbackUrl", pathname);
    return NextResponse.redirect(loginUrl);
  }

  // A session without an organization cannot use the dashboard yet, unless
  // they explicitly opted out via "Skip for now" (avoids a redirect loop).
  if (
    isUnder(pathname, "/dashboard") &&
    token.activeTeamId == null &&
    !request.cookies.get("pg_onboarding_skipped")
  ) {
    return NextResponse.redirect(new URL(ONBOARDING_PATH, request.url));
  }

  return NextResponse.next();
}

export const config = {
  matcher: ["/dashboard/:path*", "/onboarding/:path*"],
};
