import { sql } from "drizzle-orm";
import { NextResponse } from "next/server";
import { db } from "@/lib/server/db";
import { EXPECTED_DRIZZLE_MIGRATION } from "@/lib/server/db/migration-contract";
import { WEATHER_LAYER_ID } from "@/lib/server/layer-ids";
import { probeRedis } from "@/lib/server/redis";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

const PROBE_TIMEOUT_MS = 2_000;

interface DatabaseReadinessRow {
  [key: string]: unknown;
  extensionsReady: boolean;
  migrationReady: boolean;
  schemasReady: boolean;
  constraintsReady: boolean;
  layersReady: boolean;
  dataFloorReady: boolean;
}

async function withTimeout<T>(operation: Promise<T>, timeoutMs: number): Promise<T> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    return await Promise.race([
      operation,
      new Promise<never>((_, reject) => {
        timer = setTimeout(() => reject(new Error("readiness timeout")), timeoutMs);
      }),
    ]);
  } finally {
    if (timer) clearTimeout(timer);
  }
}

async function probeDatabase(): Promise<boolean> {
  const rows = await db.execute<DatabaseReadinessRow>(sql`
    SELECT
      (
        SELECT count(*) = 4
        FROM pg_extension
        WHERE extname IN ('postgis', 'timescaledb', 'vector', 'pgcrypto')
      ) AS "extensionsReady",
      EXISTS (
        SELECT 1
        FROM drizzle.__drizzle_migrations
        WHERE created_at = ${EXPECTED_DRIZZLE_MIGRATION.createdAt}
          AND hash = ${EXPECTED_DRIZZLE_MIGRATION.sha256}
      ) AS "migrationReady",
      to_regclass('public.users') IS NOT NULL
        AND to_regclass('public.accounts') IS NOT NULL
        AND to_regclass('public.sessions') IS NOT NULL
        AND to_regclass('public.verification_tokens') IS NOT NULL
        AND to_regclass('geo.layers') IS NOT NULL
        AND to_regclass('geo.features') IS NOT NULL
        AND to_regclass('public.environmental_alerts') IS NOT NULL
        AND to_regclass('tracking.assets') IS NOT NULL
        AND to_regclass('tracking.positions') IS NOT NULL
        AS "schemasReady",
      to_regclass('geo.features_layer_external_id_unique') IS NOT NULL
        AND to_regclass('tracking.positions_asset_time_unique') IS NOT NULL
        AND EXISTS (
          SELECT 1
          FROM pg_constraint
          WHERE conname = 'environmental_alerts_dedupe_key_unique'
            AND conrelid = 'public.environmental_alerts'::regclass
        )
        AND EXISTS (
          SELECT 1
          FROM pg_constraint
          WHERE conname = 'positions_asset_id_assets_id_fk'
            AND conrelid = 'tracking.positions'::regclass
        )
        AS "constraintsReady",
      (
        SELECT count(DISTINCT name) = 8
        FROM geo.layers
        WHERE name IN (
          'fire-perimeters',
          'fire-detections',
          'water-gauges',
          ${WEATHER_LAYER_ID},
          'evacuation-zones',
          'sensors',
          'vegetation',
          'interventions'
        )
      ) AS "layersReady",
      (
        SELECT EXISTS (SELECT 1 FROM geo.features)
      ) AS "dataFloorReady"
  `);
  const row = rows[0];
  return Boolean(
    row?.extensionsReady &&
      row.migrationReady &&
      row.schemasReady &&
      row.constraintsReady &&
      row.layersReady &&
      row.dataFloorReady
  );
}

function probeConfiguration(): boolean {
  const secret = process.env.NEXTAUTH_SECRET;
  if (
    !secret ||
    secret.length < 32 ||
    secret !== secret.trim() ||
    new Set(secret).size < 10
  ) {
    return false;
  }

  const canonicalUrl = process.env.NEXTAUTH_URL;
  if (!canonicalUrl) return process.env.NODE_ENV !== "production";
  try {
    const parsed = new URL(canonicalUrl);
    return (
      !parsed.username &&
      !parsed.password &&
      !parsed.search &&
      !parsed.hash &&
      (process.env.NODE_ENV !== "production" || parsed.protocol === "https:")
    );
  } catch {
    return false;
  }
}

export async function GET() {
  const [database, redis] = await Promise.all([
    withTimeout(probeDatabase(), PROBE_TIMEOUT_MS)
      .catch(() => false),
    probeRedis(PROBE_TIMEOUT_MS),
  ]);
  const configuration = probeConfiguration();
  const ready = database && redis && configuration;

  return NextResponse.json(
    {
      status: ready ? "ready" : "not_ready",
      checks: { configuration, database, redis },
      timestamp: new Date().toISOString(),
    },
    {
      status: ready ? 200 : 503,
      headers: { "Cache-Control": "no-store" },
    }
  );
}
