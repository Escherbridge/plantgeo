import { NextResponse } from "next/server";
import { eq } from "drizzle-orm";
import { db } from "@/lib/server/db";
import { users } from "@/lib/server/db/schema";
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
  if (existing.length > 0) {
    return NextResponse.json(
      { error: "An account with this email already exists." },
      { status: 409 }
    );
  }

  const passwordHash = await hashPassword(password);
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
    return NextResponse.json({ id: user.id, email: user.email }, { status: 201 });
  } catch (error) {
    if (isUniqueConstraintViolation(error)) {
      return NextResponse.json(
        { error: "An account with this email already exists." },
        { status: 409 }
      );
    }
    throw error;
  }
}
