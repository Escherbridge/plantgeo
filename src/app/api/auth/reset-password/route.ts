import { NextResponse } from "next/server";
import { and, eq, isNull } from "drizzle-orm";
import { db } from "@/lib/server/db";
import { passwordResetTokens, users } from "@/lib/server/db/schema";
import { hashPassword } from "@/lib/server/password";
import { checkRateLimit } from "@/lib/server/middleware/api-auth";
import { parseBoundedJson } from "@/lib/server/security/ingress";
import {
  MAX_RESET_PASSWORD_BODY_BYTES,
  passwordResetRateLimitKey,
  passwordResetSchema,
  RESET_PASSWORD_PER_MINUTE,
} from "@/lib/server/security/registration";
import { hashToken, timingSafeTokenEqual } from "@/lib/server/security/tokens";

/**
 * One message for unknown, expired, already-used and federated-only cases, so a
 * reset attempt never reveals which of them applies.
 */
const INVALID_TOKEN = "This password reset link is invalid or has expired.";

export async function POST(request: Request) {
  const rateLimit = await checkRateLimit(
    passwordResetRateLimitKey(request),
    RESET_PASSWORD_PER_MINUTE
  );
  if (rateLimit.available && rateLimit.limited) {
    return NextResponse.json(
      { error: "Too many password reset attempts" },
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

  const body = await parseBoundedJson(request, MAX_RESET_PASSWORD_BODY_BYTES);
  if (!body.ok) {
    return NextResponse.json({ error: body.error }, { status: body.status });
  }
  const parsed = passwordResetSchema.safeParse(body.data);
  if (!parsed.success) {
    return NextResponse.json(
      { error: "Invalid password reset request" },
      { status: 400 }
    );
  }

  const tokenHash = hashToken(parsed.data.token);
  // bcrypt is deliberately slow; keep it outside the transaction.
  const passwordHash = await hashPassword(parsed.data.password);
  const now = new Date();

  const reset = await db.transaction(async (tx) => {
    // FOR UPDATE: without the row lock two concurrent redemptions of the same
    // token both read usedAt as null and both succeed.
    const [record] = await tx
      .select({
        userId: passwordResetTokens.userId,
        tokenHash: passwordResetTokens.tokenHash,
        expiresAt: passwordResetTokens.expiresAt,
        usedAt: passwordResetTokens.usedAt,
      })
      .from(passwordResetTokens)
      .where(eq(passwordResetTokens.tokenHash, tokenHash))
      .limit(1)
      .for("update");

    if (!record) return false;
    if (!timingSafeTokenEqual(record.tokenHash, tokenHash)) return false;
    if (record.usedAt) return false;
    if (!record.expiresAt || new Date(record.expiresAt).getTime() <= now.getTime()) {
      return false;
    }

    const [account] = await tx
      .select({ id: users.id, passwordHash: users.passwordHash })
      .from(users)
      .where(eq(users.id, record.userId))
      .limit(1);
    // A federated-only account has no password to reset.
    if (!account?.passwordHash) return false;

    // Burn before writing: the conditional UPDATE is the single point that
    // decides redemption, so a loser of the race changes no password at all.
    // Burns this token and every other outstanding one for the account.
    const burned = await tx
      .update(passwordResetTokens)
      .set({ usedAt: now })
      .where(
        and(
          eq(passwordResetTokens.userId, record.userId),
          isNull(passwordResetTokens.usedAt)
        )
      )
      .returning({ id: passwordResetTokens.id });
    if (burned.length === 0) return false;

    await tx
      .update(users)
      .set({ passwordHash })
      .where(eq(users.id, record.userId));

    return true;
  });

  if (!reset) {
    return NextResponse.json({ error: INVALID_TOKEN }, { status: 400 });
  }

  return NextResponse.json({ ok: true }, { status: 200 });
}
