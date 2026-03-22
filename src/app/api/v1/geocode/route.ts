import { NextRequest, NextResponse } from "next/server";
import { db } from "@/lib/server/db";
import { apiKeys } from "@/lib/server/db/schema";
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

  const params = request.nextUrl.searchParams;
  const query = params.get("q");
  if (!query) {
    return NextResponse.json({ error: "Missing required parameter: q" }, { status: 400 });
  }

  const limit = Math.min(Number(params.get("limit") || "5"), 50);
  const photonUrl = process.env.PHOTON_URL || "http://localhost:2322";

  const upstreamUrl = new URL(`${photonUrl}/api`);
  upstreamUrl.searchParams.set("q", query);
  upstreamUrl.searchParams.set("limit", String(limit));
  upstreamUrl.searchParams.set("lang", "en");

  try {
    const upstream = await fetch(upstreamUrl.toString());
    if (!upstream.ok) {
      return NextResponse.json({ error: "Geocoding service error" }, { status: upstream.status });
    }
    const data = await upstream.json();
    return NextResponse.json(data);
  } catch {
    return NextResponse.json({ error: "Geocoding service unavailable" }, { status: 502 });
  }
}
