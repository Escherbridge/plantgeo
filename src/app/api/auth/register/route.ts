import { NextResponse } from "next/server";
import { db } from "@/lib/server/db";
import { users } from "@/lib/server/db/schema";
import { eq } from "drizzle-orm";
import { hashPassword } from "@/lib/server/auth";

export async function POST(request: Request) {
  const body = await request.json();
  const { name, email, password } = body as {
    name?: string;
    email?: string;
    password?: string;
  };

  if (!email || !password) {
    return NextResponse.json({ error: "Email and password are required." }, { status: 400 });
  }

  if (password.length < 8) {
    return NextResponse.json({ error: "Password must be at least 8 characters." }, { status: 400 });
  }

  const existing = await db
    .select({ id: users.id })
    .from(users)
    .where(eq(users.email, email))
    .limit(1);

  if (existing.length > 0) {
    return NextResponse.json({ error: "An account with this email already exists." }, { status: 409 });
  }

  const passwordHash = await hashPassword(password);

  const [user] = await db
    .insert(users)
    .values({ name: name ?? null, email, passwordHash, platformRole: "contributor" })
    .returning({ id: users.id, email: users.email });

  return NextResponse.json({ id: user.id, email: user.email }, { status: 201 });
}
