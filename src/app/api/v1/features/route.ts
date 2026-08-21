import { NextRequest, NextResponse } from "next/server";
import { z } from "zod";
import { and, asc, eq, sql, type SQL } from "drizzle-orm";
import { db } from "@/lib/server/db";
import { features, layers } from "@/lib/server/db/schema";
import {
  apiKeyAuthorizationErrorResponse,
  authorizeApiRequest,
} from "@/lib/server/middleware/api-auth";
import { parseBoundingBox } from "@/lib/server/security/bbox";
import { featureVisibilityCondition } from "@/lib/server/security/layer-access";

/** The most rows one page may ask for. */
const MAX_LIMIT = 1_000;

/**
 * The two page depths this route serves.
 *
 * Without `layer_id` the `ORDER BY id LIMIT/OFFSET` runs as a cross-partition merge sort over
 * every layer partition instead of one partition's index scan, so an unscoped crawl is bounded
 * to the shallower of the two. Both are enforced by the same parse that advertises them -- a
 * ceiling the parser accepts and a later check rejects is how a crawl gets truncated by a
 * number the caller was never told.
 */
const MAX_OFFSET_WITH_LAYER_ID = 1_000_000;
const MAX_OFFSET_WITHOUT_LAYER_ID = 10_000;

function parsePageValue(
  value: string | null,
  fallback: number,
  maximum: number
): number | null {
  if (value === null) return fallback;
  const parsed = Number(value);
  return Number.isSafeInteger(parsed) && parsed >= 0 && parsed <= maximum
    ? parsed
    : null;
}

export async function GET(request: NextRequest) {
  const authResult = await authorizeApiRequest(request, "read:features");
  if (!authResult.valid) {
    return apiKeyAuthorizationErrorResponse(authResult);
  }

  const params = request.nextUrl.searchParams;
  const limit = parsePageValue(params.get("limit"), 100, MAX_LIMIT);
  if (limit === null || limit === 0) {
    return NextResponse.json(
      { error: `limit must be an integer between 1 and ${MAX_LIMIT}` },
      { status: 400 }
    );
  }

  const layerId = params.get("layer_id");
  if (layerId && !z.string().uuid().safeParse(layerId).success) {
    return NextResponse.json({ error: "Invalid layer_id" }, { status: 400 });
  }

  const maximumOffset = layerId
    ? MAX_OFFSET_WITH_LAYER_ID
    : MAX_OFFSET_WITHOUT_LAYER_ID;
  const offset = parsePageValue(params.get("offset"), 0, maximumOffset);
  if (offset === null) {
    return NextResponse.json(
      {
        error: `offset must be an integer between 0 and ${maximumOffset}`,
        ...(layerId
          ? {}
          : {
              detail:
                "Paging deeper than this without layer_id would sort across every layer at once, " +
                `which this endpoint does not serve. Supply layer_id to page to ${MAX_OFFSET_WITH_LAYER_ID}, ` +
                "or narrow the crawl with bbox.",
            }),
      },
      { status: 400 }
    );
  }

  const conditions: SQL[] = [
    featureVisibilityCondition({
      userId: authResult.userId,
      teamId: authResult.teamId,
    }),
  ];
  if (layerId) conditions.push(eq(features.layerId, layerId));

  const bboxParam = params.get("bbox");
  if (bboxParam) {
    const bbox = parseBoundingBox(bboxParam);
    if (!bbox) {
      return NextResponse.json(
        { error: "Invalid bbox. Expected ordered west,south,east,north" },
        { status: 400 }
      );
    }
    const [west, south, east, north] = bbox;
    conditions.push(
      sql<boolean>`ST_Intersects(
        ${features.geom},
        ST_MakeEnvelope(${west}, ${south}, ${east}, ${north}, 4326)
      )`
    );
  }

  const rows = await db
    .select({
      id: features.id,
      layerId: features.layerId,
      geometry:
        sql<Record<string, unknown> | null>`ST_AsGeoJSON(${features.geom})::jsonb`.as(
          "geometry"
        ),
      properties: features.properties,
      status: features.status,
      createdAt: features.createdAt,
      updatedAt: features.updatedAt,
    })
    .from(features)
    .innerJoin(layers, eq(features.layerId, layers.id))
    .where(and(...conditions))
    .orderBy(asc(features.id))
    .limit(limit)
    .offset(offset);

  const featureCollection = {
    type: "FeatureCollection",
    features: rows.map((row) => ({
      type: "Feature",
      id: row.id,
      geometry: row.geometry,
      properties: {
        ...(row.properties as Record<string, unknown>),
        layer_id: row.layerId,
        status: row.status,
        created_at: row.createdAt,
        updated_at: row.updatedAt,
      },
    })),
    numberReturned: rows.length,
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
      "Cache-Control": "private, no-store",
    },
  });
}
