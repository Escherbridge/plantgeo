import { NextResponse } from "next/server";
import { getServerSession } from "@/lib/server/auth";
import { db } from "@/lib/server/db";
import { apiKeys } from "@/lib/server/db/schema";
import { eq } from "drizzle-orm";
import crypto from "crypto";
import { hashPassword } from "@/lib/server/auth";

export async function GET() {
  const session = await getServerSession();
  if (!session?.user) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }
  const userId = (session.user as { id: string }).id;
  const keys = await db
    .select({
      id: apiKeys.id,
      name: apiKeys.name,
      teamId: apiKeys.teamId,
      permissions: apiKeys.permissions,
      rateLimit: apiKeys.rateLimit,
      lastUsed: apiKeys.lastUsed,
    })
    .from(apiKeys)
    .where(eq(apiKeys.userId, userId));
  return NextResponse.json(keys);
}

export async function POST(request: Request) {
  const session = await getServerSession();
  if (!session?.user) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }
  const userId = (session.user as { id: string }).id;
  const body = await request.json();
  const { name, teamId, permissions, rateLimit } = body as {
    name?: string;
    teamId?: string;
    permissions?: string[];
    rateLimit?: number;
  };

  const rawKey = `pg_${crypto.randomBytes(32).toString("hex")}`;
  const keyHash = await hashPassword(rawKey);

  const [key] = await db
    .insert(apiKeys)
    .values({
      keyHash,
      userId,
      teamId: teamId ?? null,
      name: name ?? null,
      permissions: permissions ?? [],
      rateLimit: rateLimit ?? 1000,
    })
    .returning({ id: apiKeys.id, name: apiKeys.name });

  return NextResponse.json({ id: key.id, name: key.name, key: rawKey }, { status: 201 });
}
