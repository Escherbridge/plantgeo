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

/** One-click invitation entry point: the URL that ships in invitation email. */
export async function GET(
  request: Request,
  { params }: { params: Promise<{ token: string }> }
) {
  const { token } = await params;
  const origin = new URL(request.url).origin;
  const encoded = encodeURIComponent(token);
  const invitePage = (error?: string) =>
    NextResponse.redirect(
      new URL(
        `/invite/${encoded}${error ? `?error=${encodeURIComponent(error)}` : ""}`,
        origin
      ),
      { status: 303 }
    );

  const rateLimit = await checkRateLimit(
    ipRateLimitKey(request, "org:invitation-redeem"),
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
      { error: "Invitations are temporarily unavailable" },
      { status: 503 }
    );
  }

  const session = await getServerSession();
  const caller = teamsRouter.createCaller({ db, session });

  let preview;
  try {
    preview = await caller.previewInvitation({ token });
  } catch {
    return invitePage("invalid");
  }
  if (!preview.valid) return invitePage("expired");

  if (!session?.user) {
    // The invite landing page owns the logged-out flow (sign-in/register CTAs that preserve the token).
    return NextResponse.redirect(
      new URL(`/invite/${encoded}`, origin),
      { status: 303 }
    );
  }

  try {
    await caller.acceptInvitation({ token });
  } catch (error) {
    if (error instanceof TRPCError) {
      return invitePage(
        error.code === "FORBIDDEN" ? "email_mismatch" : "expired"
      );
    }
    throw error;
  }
  return NextResponse.redirect(new URL("/dashboard/org", origin), {
    status: 303,
  });
}
