import { NextResponse } from "next/server";
import { and, eq, isNull } from "drizzle-orm";
import { db } from "@/lib/server/db";
import { emailVerificationTokens, users } from "@/lib/server/db/schema";
import { checkRateLimit } from "@/lib/server/middleware/api-auth";
import { parseBoundedJson } from "@/lib/server/security/ingress";
import {
  emailVerificationRateLimitKey,
  emailVerificationSchema,
  MAX_VERIFY_EMAIL_BODY_BYTES,
  VERIFY_EMAIL_PER_MINUTE,
} from "@/lib/server/security/registration";
import { hashToken, timingSafeTokenEqual } from "@/lib/server/security/tokens";

/** One message for every failure mode: unknown, expired, and already-used tokens. */
const INVALID_TOKEN = "This verification link is invalid or has expired.";

export async function POST(request: Request) {
  const rateLimit = await checkRateLimit(
    emailVerificationRateLimitKey(request),
    VERIFY_EMAIL_PER_MINUTE
  );
  if (rateLimit.available && rateLimit.limited) {
    return NextResponse.json(
      { error: "Too many verification attempts" },
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
      { error: "Email verification is temporarily unavailable" },
      { status: 503 }
    );
  }

  const body = await parseBoundedJson(request, MAX_VERIFY_EMAIL_BODY_BYTES);
  if (!body.ok) {
    return NextResponse.json({ error: body.error }, { status: body.status });
  }
  const parsed = emailVerificationSchema.safeParse(body.data);
  if (!parsed.success) {
    return NextResponse.json({ error: INVALID_TOKEN }, { status: 400 });
  }

  const tokenHash = hashToken(parsed.data.token);
  const now = new Date();

  const verified = await db.transaction(async (tx) => {
    // FOR UPDATE: without the row lock two concurrent redemptions of the same
    // token both read usedAt as null and both succeed.
    const [record] = await tx
      .select({
        userId: emailVerificationTokens.userId,
        tokenHash: emailVerificationTokens.tokenHash,
        expiresAt: emailVerificationTokens.expiresAt,
        usedAt: emailVerificationTokens.usedAt,
      })
      .from(emailVerificationTokens)
      .where(eq(emailVerificationTokens.tokenHash, tokenHash))
      .limit(1)
      .for("update");

    if (!record) return false;
    if (!timingSafeTokenEqual(record.tokenHash, tokenHash)) return false;
    if (record.usedAt) return false;
    if (!record.expiresAt || new Date(record.expiresAt).getTime() <= now.getTime()) {
      return false;
    }

    // Single use, and any older outstanding link for this account is burned
    // too. The conditional UPDATE decides redemption, so a loser of the race
    // matches no rows and verifies nothing.
    const burned = await tx
      .update(emailVerificationTokens)
      .set({ usedAt: now })
      .where(
        and(
          eq(emailVerificationTokens.userId, record.userId),
          isNull(emailVerificationTokens.usedAt)
        )
      )
      .returning({ id: emailVerificationTokens.id });
    if (burned.length === 0) return false;

    await tx
      .update(users)
      .set({ emailVerified: now })
      .where(eq(users.id, record.userId));

    return true;
  });

  if (!verified) {
    return NextResponse.json({ error: INVALID_TOKEN }, { status: 400 });
  }

  return NextResponse.json({ ok: true }, { status: 200 });
}
