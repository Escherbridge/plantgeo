import { sql } from "drizzle-orm";
import { db } from "@/lib/server/db";
import {
  fetchLatestUsdmDroughtRelease,
  fetchUsdmDroughtRelease,
  type UsdmDroughtRelease,
} from "@/lib/server/services/usdm-drought";

/** Weekly releases retained before the oldest are pruned (~19 MB each). */
const DEFAULT_RETAINED_RELEASES = 8;
const MIN_RETAINED_RELEASES = 2;
const MAX_RETAINED_RELEASES = 52;

export interface DroughtIngestionOutcome {
  validDate: string | null;
  sourceUrl: string | null;
  areasSeen: number;
  areasWritten: number;
  releasesPruned: number;
  reason?: string;
}

/** Reads DROUGHT_RETAINED_RELEASES at call time, clamped to a sane window. */
function resolveRetainedReleases(): number {
  const raw = process.env.DROUGHT_RETAINED_RELEASES?.trim();
  if (!raw) return DEFAULT_RETAINED_RELEASES;
  const parsed = Number(raw);
  if (!Number.isFinite(parsed)) return DEFAULT_RETAINED_RELEASES;
  return Math.min(
    MAX_RETAINED_RELEASES,
    Math.max(MIN_RETAINED_RELEASES, Math.trunc(parsed))
  );
}

/**
 * Writes one USDM release as PostGIS geometry, atomically.
 *
 * Rings are repaired in the database (ST_MakeValid, then polygon-only
 * extraction) because USDM ships self-intersecting rings that would make
 * ST_Intersects unreliable at read time. That repairs topology only -- no
 * classification is invented, and a class the release did not contain stays
 * absent rather than being written as an empty geometry.
 *
 * Re-running a release is a no-op unless `replace` is set, so the weekly cron is
 * idempotent while an operator can still re-pull a corrected upstream file.
 */
export async function storeDroughtRelease(
  release: UsdmDroughtRelease,
  replace = false
): Promise<number> {
  return db.transaction(async (tx) => {
    let written = 0;
    for (const area of release.areas) {
      const geometryJson = JSON.stringify(area.geometry);
      const rows = await tx.execute<{ id: string }>(sql`
        INSERT INTO geo.drought_areas (valid_date, dm_category, geom, source_url)
        VALUES (
          ${release.validDate},
          ${area.dmCategory},
          ST_Multi(
            ST_CollectionExtract(
              ST_MakeValid(ST_SetSRID(ST_GeomFromGeoJSON(${geometryJson}), 4326)),
              3
            )
          ),
          ${release.sourceUrl}
        )
        ON CONFLICT (valid_date, dm_category) DO UPDATE
          SET geom = EXCLUDED.geom,
              source_url = EXCLUDED.source_url,
              ingested_at = now()
          WHERE ${sql.raw(replace ? "true" : "false")}
        RETURNING id
      `);
      written += rows.length;
    }
    return written;
  });
}

/** Drops all but the newest retained releases; returns the release count removed. */
export async function pruneDroughtReleases(
  retain = resolveRetainedReleases()
): Promise<number> {
  const rows = await db.execute<{ valid_date: string }>(sql`
    DELETE FROM geo.drought_areas
    WHERE valid_date NOT IN (
      SELECT valid_date
      FROM geo.drought_areas
      GROUP BY valid_date
      ORDER BY valid_date DESC
      LIMIT ${retain}
    )
    RETURNING valid_date
  `);
  return new Set(rows.map((row) => row.valid_date)).size;
}

/**
 * Fetches and stores a USDM release, then prunes old ones.
 * @param validDate an explicit USDM Tuesday, or omit for the newest published.
 */
export async function ingestDroughtRelease(options?: {
  validDate?: string;
  replace?: boolean;
}): Promise<DroughtIngestionOutcome> {
  const release = options?.validDate
    ? await fetchUsdmDroughtRelease(options.validDate)
    : await fetchLatestUsdmDroughtRelease();

  if (!release) {
    return {
      validDate: options?.validDate ?? null,
      sourceUrl: null,
      areasSeen: 0,
      areasWritten: 0,
      releasesPruned: 0,
      reason: "USDM has not published a release for the requested dates",
    };
  }

  const areasWritten = await storeDroughtRelease(release, options?.replace ?? false);
  return {
    validDate: release.validDate,
    sourceUrl: release.sourceUrl,
    areasSeen: release.areas.length,
    areasWritten,
    releasesPruned: await pruneDroughtReleases(),
  };
}
