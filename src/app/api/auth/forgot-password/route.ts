import { NextResponse } from "next/server";
import { and, eq, isNull } from "drizzle-orm";
import { db } from "@/lib/server/db";
import { passwordResetTokens, users } from "@/lib/server/db/schema";
import { checkRateLimit } from "@/lib/server/middleware/api-auth";
import { parseBoundedJson } from "@/lib/server/security/ingress";
import {
  forgotPasswordRateLimitKey,
  forgotPasswordSchema,
  FORGOT_PASSWORD_MIN_DURATION_MS,
  FORGOT_PASSWORD_PER_MINUTE,
  MAX_FORGOT_PASSWORD_BODY_BYTES,
  padToMinimumDuration,
} from "@/lib/server/security/registration";
import {
  generateToken,
  TOKEN_TTL,
  tokenExpiry,
} from "@/lib/server/security/tokens";
import { appUrl, sendPasswordReset } from "@/lib/server/services/transactional-email";

/**
 * The only success shape this route ever produces. Unknown addresses,
 * OAuth-only accounts and delivery failures all return exactly these bytes so
 * the endpoint cannot be used to enumerate accounts.
 *
 * Identical bytes are not enough on their own: issuing a real reset costs an
 * UPDATE, an INSERT and a live provider round trip, while an unknown address
 * costs one SELECT. Delivery therefore runs off the request path and the
 * response is held to a fixed floor, so all three outcomes share a timing class.
 */
function acknowledged() {
  return NextResponse.json(
    { ok: true, message: "If that email has an account, a reset link is on its way." },
    { status: 200 }
  );
}

async function issuePasswordReset(userId: string, email: string): Promise<void> {
  const now = new Date();
  // Only one live reset link per account.
  await db
    .update(passwordResetTokens)
    .set({ usedAt: now })
    .where(
      and(
        eq(passwordResetTokens.userId, userId),
        isNull(passwordResetTokens.usedAt)
      )
    );

  const { token, tokenHash } = generateToken();
  await db.insert(passwordResetTokens).values({
    userId,
    tokenHash,
    expiresAt: tokenExpiry(TOKEN_TTL.passwordReset),
  });

  const resetUrl = `${appUrl()}/reset-password?token=${encodeURIComponent(token)}`;
  await sendPasswordReset(email, resetUrl);
}

export async function POST(request: Request) {
  const startedAt = Date.now();
  const rateLimit = await checkRateLimit(
    forgotPasswordRateLimitKey(request),
    FORGOT_PASSWORD_PER_MINUTE
  );
  if (rateLimit.available && rateLimit.limited) {
    return NextResponse.json(
      { error: "Too many password reset requests" },
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
      { error: "Password reset is temporarily unavailable" },
      { status: 503 }
    );
  }

  const body = await parseBoundedJson(request, MAX_FORGOT_PASSWORD_BODY_BYTES);
  if (!body.ok) {
    return NextResponse.json({ error: body.error }, { status: body.status });
  }
  const parsed = forgotPasswordSchema.safeParse(body.data);
  if (!parsed.success) {
    return NextResponse.json({ error: "Invalid email address" }, { status: 400 });
  }

  const [account] = await db
    .select({
      id: users.id,
      email: users.email,
      passwordHash: users.passwordHash,
    })
    .from(users)
    .where(eq(users.email, parsed.data.email))
    .limit(1);

  // A missing account and a federated-only account behave identically here.
  if (account?.passwordHash) {
    void issuePasswordReset(account.id, account.email).catch((error: unknown) => {
      console.error("[auth] Failed to issue password reset", error);
    });
  }

  await padToMinimumDuration(startedAt, FORGOT_PASSWORD_MIN_DURATION_MS);
  return acknowledged();
}
