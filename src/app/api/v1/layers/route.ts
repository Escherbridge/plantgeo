import { NextRequest, NextResponse } from "next/server";
import { db } from "@/lib/server/db";
import { apiKeys, layers } from "@/lib/server/db/schema";
import { eq } from "drizzle-orm";
import { verifyPassword } from "@/lib/server/auth";

async function validateApiKey(request: NextRequest) {
  const key = request.headers.get("x-api-key");
  if (!key) return null;

  const allKeys = await db
    .select({ id: apiKeys.id, keyHash: apiKeys.keyHash, rateLimit: apiKeys.rateLimit })
    .from(apiKeys);

  for (const record of allKeys) {
    const valid = await verifyPassword(key, record.keyHash);
    if (valid) return record;
  }
  return null;
}

export async function GET(request: NextRequest) {
  const keyRecord = await validateApiKey(request);
  if (!keyRecord) {
    return NextResponse.json({ error: "Invalid or missing API key" }, { status: 401 });
  }

  const publicLayers = await db
    .select({
      id: layers.id,
      name: layers.name,
      type: layers.type,
      description: layers.description,
    })
    .from(layers)
    .where(eq(layers.isPublic, true));

  return NextResponse.json(publicLayers);
}
