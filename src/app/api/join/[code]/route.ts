import { NextResponse } from "next/server";
import { TRPCError } from "@trpc/server";
import { db } from "@/lib/server/db";
import { getServerSession } from "@/lib/server/auth";
import { checkRateLimit } from "@/lib/server/middleware/api-auth";
import { ipRateLimitKey } from "@/lib/server/security/registration";
import { REDEMPTIONS_PER_MINUTE } from "@/lib/server/security/invitations";
import { teamsRouter } from "@/lib/server/trpc/routers/teams";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

/** Shareable join-link entry point. */
export async function GET(
  request: Request,
  { params }: { params: Promise<{ code: string }> }
) {
  const { code } = await params;
  const origin = new URL(request.url).origin;
  const encoded = encodeURIComponent(code);
  const joinPage = (error?: string) =>
    NextResponse.redirect(
      new URL(
        `/join/${encoded}${error ? `?error=${encodeURIComponent(error)}` : ""}`,
        origin
      ),
      { status: 303 }
    );

  const rateLimit = await checkRateLimit(
    ipRateLimitKey(request, "org:join-redeem"),
    REDEMPTIONS_PER_MINUTE
  );
  if (rateLimit.available && rateLimit.limited) {
    return NextResponse.json(
      { error: "Too many attempts" },
      {
        status: 429,
        headers: rateLimit.retryAfter
          ? { "Retry-After": String(rateLimit.retryAfter) }
          : undefined,
      }
    );
  }
  if (!rateLimit.available && process.env.NODE_ENV === "production") {
    return NextResponse.json(
      { error: "Join links are temporarily unavailable" },
      { status: 503 }
    );
  }

  const session = await getServerSession();
  const caller = teamsRouter.createCaller({ db, session });

  let preview;
  try {
    preview = await caller.previewJoinLink({ code });
  } catch {
    return joinPage("invalid");
  }
  if (!preview.valid) return joinPage("expired");

  if (!session?.user) {
    // The join landing page owns the logged-out flow (sign-in/register CTAs that preserve the code).
    return NextResponse.redirect(new URL(`/join/${encoded}`, origin), {
      status: 303,
    });
  }

  try {
    await caller.redeemJoinLink({ code });
  } catch (error) {
    if (error instanceof TRPCError) {
      return joinPage(error.code === "FORBIDDEN" ? "not_allowed" : "expired");
    }
    throw error;
  }
  return NextResponse.redirect(new URL("/dashboard/org", origin), {
    status: 303,
  });
}
