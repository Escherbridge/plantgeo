import { NextResponse } from "next/server";
import { getServerSession } from "@/lib/server/auth";
import { db } from "@/lib/server/db";
import { apiKeys, teamMembers } from "@/lib/server/db/schema";
import { and, eq } from "drizzle-orm";
import {
  generateApiKey,
  hashApiKey,
  personalApiKeyIssuanceSchema,
} from "@/lib/server/api-keys";

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
  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: "Invalid JSON body" }, { status: 400 });
  }

  const parsed = personalApiKeyIssuanceSchema.safeParse(body);
  if (!parsed.success) {
    return NextResponse.json(
      { error: "Invalid API key configuration", details: parsed.error.flatten() },
      { status: 400 }
    );
  }

  const { name, teamId, permissions, rateLimit } = parsed.data;
  if (teamId) {
    const membership = await db
      .select({ teamId: teamMembers.teamId })
      .from(teamMembers)
      .where(and(eq(teamMembers.teamId, teamId), eq(teamMembers.userId, userId)))
      .limit(1);

    if (membership.length === 0) {
      return NextResponse.json(
        { error: "You must be a member of a team before assigning it to an API key" },
        { status: 403 }
      );
    }
  }

  const rawKey = generateApiKey();
  const keyHash = hashApiKey(rawKey);

  const [key] = await db
    .insert(apiKeys)
    .values({
      keyHash,
      userId,
      teamId: teamId ?? null,
      name,
      permissions,
      rateLimit,
    })
    .returning({ id: apiKeys.id, name: apiKeys.name });

  return NextResponse.json({ id: key.id, name: key.name, key: rawKey }, { status: 201 });
}
