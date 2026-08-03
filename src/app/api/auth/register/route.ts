import { NextResponse } from "next/server";
import { eq } from "drizzle-orm";
import { db } from "@/lib/server/db";
import { emailVerificationTokens, users } from "@/lib/server/db/schema";
import { hashPassword } from "@/lib/server/password";
import { checkRateLimit } from "@/lib/server/middleware/api-auth";
import { parseBoundedJson } from "@/lib/server/security/ingress";
import {
  isUniqueConstraintViolation,
  MAX_REGISTRATION_BODY_BYTES,
  REGISTRATIONS_PER_MINUTE,
  registrationRateLimitKey,
  registrationSchema,
} from "@/lib/server/security/registration";
import {
  generateToken,
  TOKEN_TTL,
  tokenExpiry,
} from "@/lib/server/security/tokens";
import {
  appUrl,
  sendEmailVerification,
  sendExistingAccountNotice,
} from "@/lib/server/services/transactional-email";

/**
 * The only success shape this route ever produces. A new account and an
 * address that is already registered return exactly these bytes, so the
 * endpoint cannot be used to enumerate accounts — the same guarantee
 * forgot-password makes.
 */
function acknowledged() {
  return NextResponse.json(
    {
      ok: true,
      message: "Check your email to finish setting up your account.",
    },
    { status: 201 }
  );
}

/** Tells the address owner about the collision; never surfaces to the caller. */
async function noticeExistingAccount(email: string): Promise<void> {
  try {
    await sendExistingAccountNotice(email);
  } catch (error) {
    console.error("[auth] Failed to send existing-account notice", error);
  }
}

/** Issue and deliver a verification link; never blocks account creation. */
async function issueEmailVerification(
  userId: string,
  email: string
): Promise<void> {
  try {
    const { token, tokenHash } = generateToken();
    await db.insert(emailVerificationTokens).values({
      userId,
      tokenHash,
      expiresAt: tokenExpiry(TOKEN_TTL.emailVerification),
    });
    const verifyUrl = `${appUrl()}/verify-email?token=${encodeURIComponent(token)}`;
    await sendEmailVerification(email, verifyUrl);
  } catch (error) {
    console.error("[auth] Failed to issue email verification", error);
  }
}

export async function POST(request: Request) {
  const rateLimit = await checkRateLimit(
    registrationRateLimitKey(request),
    REGISTRATIONS_PER_MINUTE
  );
  if (rateLimit.available && rateLimit.limited) {
    return NextResponse.json(
      { error: "Too many registration attempts" },
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
      { error: "Registration is temporarily unavailable" },
      { status: 503 }
    );
  }

  const body = await parseBoundedJson(request, MAX_REGISTRATION_BODY_BYTES);
  if (!body.ok) {
    return NextResponse.json({ error: body.error }, { status: body.status });
  }
  const parsed = registrationSchema.safeParse(body.data);
  if (!parsed.success) {
    return NextResponse.json(
      { error: "Invalid registration data" },
      { status: 400 }
    );
  }

  const { name, email, password } = parsed.data;
  const existing = await db
    .select({ id: users.id })
    .from(users)
    .where(eq(users.email, email))
    .limit(1);

  // Hashing on both branches keeps the dominant cost — bcrypt — on the taken
  // path either way, so the collision is not visible as a response-time delta.
  const passwordHash = await hashPassword(password);

  if (existing.length > 0) {
    await noticeExistingAccount(email);
    return acknowledged();
  }

  try {
    const [user] = await db
      .insert(users)
      .values({
        name: name ?? null,
        email,
        passwordHash,
        platformRole: "contributor",
      })
      .returning({ id: users.id, email: users.email });
    await issueEmailVerification(user.id, user.email);
    return acknowledged();
  } catch (error) {
    // Backstop for the race between the SELECT above and this INSERT; it must
    // answer identically to the branch that saw the collision up front.
    if (isUniqueConstraintViolation(error)) {
      await noticeExistingAccount(email);
      return acknowledged();
    }
    throw error;
  }
}
