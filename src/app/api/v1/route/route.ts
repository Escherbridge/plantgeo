import { NextRequest, NextResponse } from "next/server";
import { db } from "@/lib/server/db";
import { apiKeys } from "@/lib/server/db/schema";
import { verifyPassword } from "@/lib/server/auth";
import { getRedis } from "@/lib/server/redis";

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

export async function POST(request: NextRequest) {
  const keyRecord = await validateApiKey(request);
  if (!keyRecord) {
    return NextResponse.json({ error: "Invalid or missing API key" }, { status: 401 });
  }

  // Rate limiting: check Redis counter key rl:{apiKeyId}:{minute}
  const minute = Math.floor(Date.now() / 60000);
  const rlKey = `rl:${keyRecord.id}:${minute}`;
  try {
    const redis = getRedis();
    const count = await redis.incr(rlKey);
    if (count === 1) {
      await redis.expire(rlKey, 60);
    }
    const limit = keyRecord.rateLimit ?? 1000;
    if (count > limit) {
      return NextResponse.json(
        { error: "Rate limit exceeded" },
        { status: 429, headers: { "Retry-After": "60" } }
      );
    }
  } catch {
    // Redis unavailable — allow request through
  }

  const valhallaUrl = process.env.VALHALLA_URL;
  if (!valhallaUrl) {
    return NextResponse.json({ error: "Routing service not configured" }, { status: 503 });
  }

  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: "Invalid JSON body" }, { status: 400 });
  }

  try {
    const upstream = await fetch(`${valhallaUrl}/route`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });

    const data = await upstream.json();
    return NextResponse.json(data, { status: upstream.status });
  } catch {
    return NextResponse.json({ error: "Routing service unavailable" }, { status: 502 });
  }
}
