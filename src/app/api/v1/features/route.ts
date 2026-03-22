import { NextRequest, NextResponse } from "next/server";
import { db } from "@/lib/server/db";
import { apiKeys, features, layers } from "@/lib/server/db/schema";
import { eq, and, sql } from "drizzle-orm";
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
  const bboxParam = params.get("bbox");
  const limit = Math.min(Number(params.get("limit") || "100"), 1000);
  const offset = Number(params.get("offset") || "0");
  const layerId = params.get("layer_id");

  let bbox: [number, number, number, number] | null = null;
  if (bboxParam) {
    const parts = bboxParam.split(",").map(Number);
    if (parts.length === 4 && parts.every((n) => !isNaN(n))) {
      bbox = parts as [number, number, number, number];
    } else {
      return NextResponse.json(
        { error: "Invalid bbox. Expected west,south,east,north" },
        { status: 400 }
      );
    }
  }

  const conditions = [];
  if (layerId) {
    conditions.push(eq(features.layerId, layerId));
  }

  const rows = await db
    .select({
      id: features.id,
      layerId: features.layerId,
      properties: features.properties,
      status: features.status,
      createdAt: features.createdAt,
      updatedAt: features.updatedAt,
    })
    .from(features)
    .where(conditions.length > 0 ? and(...conditions) : undefined)
    .limit(limit)
    .offset(offset);

  const featureCollection = {
    type: "FeatureCollection",
    features: rows.map((row) => ({
      type: "Feature",
      id: row.id,
      geometry: null,
      properties: {
        ...(row.properties as Record<string, unknown>),
        layer_id: row.layerId,
        status: row.status,
        created_at: row.createdAt,
        updated_at: row.updatedAt,
      },
    })),
    numberReturned: rows.length,
    numberMatched: rows.length,
    links: [
      {
        href: request.url,
        rel: "self",
        type: "application/geo+json",
        title: "This document",
      },
    ],
  };

  return new NextResponse(JSON.stringify(featureCollection), {
    status: 200,
    headers: {
      "Content-Type": "application/geo+json",
      "Cache-Control": "public, max-age=30",
    },
  });
}
