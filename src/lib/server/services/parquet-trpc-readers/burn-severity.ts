import { z } from "zod";
import type { MtbsSnapshotMetadata } from "@/lib/environmental/mtbs-snapshot";
import type { DayRange } from "@/types/time-slider";
import { resolveZoomTier } from "@/lib/map/zoom-tiers";
import { getRegion } from "@/lib/region/region";
import { getParquetLatestRelease, getParquetWarehouseCoverage } from "@/lib/server/services/parquet-plane-client";
import type { ParquetPlaneEnvelope } from "@/lib/server/services/parquet-envelope";
import {
  addUtcDays,
  boundedResult,
  contractError,
  daySchema,
  decodePolygonGeometry,
  finiteNumberSchema,
  instantSchema,
  mapEnvelope,
  parseRows,
  rejectFutureDay,
  selectedDay,
  type ParquetPolygonGeometry,
  type ParquetReaderResult,
  type ParquetViewportRead,
} from "./shared";

/** Shared ceiling for historical release reads and optional snapshot-absence discovery. */
const BURN_SEVERITY_MAX_RELEASES = 12;

/**
 * The one publication scope this reader accepts an MTBS snapshot for.
 *
 * `envelope` reads `getRegion().subEnvelopes.burn_severity` (federation.md §5 step 2); the covered
 * years remain a reader-owned constant, not a manifest field. Parameterised so a caller can state
 * the scope it expects.
 */
export interface BurnSnapshotPublicationScope {
  /** [west, south, east, north], WGS84. */
  envelope: readonly [number, number, number, number];
  coveredYears: { readonly from: number; readonly to: number };
}

function burnSeverityEnvelopeTuple(): readonly [number, number, number, number] {
  const { west, south, east, north } = getRegion().subEnvelopes.burn_severity;
  return [west, south, east, north];
}

const SUPPORTED_BURN_SNAPSHOT_SCOPE: BurnSnapshotPublicationScope = {
  envelope: burnSeverityEnvelopeTuple(),
  coveredYears: { from: 2018, to: 2026 },
};

const burnSeverityRowSchema = z
  .object({
    feature_id: z.string().min(1),
    fire_id: z.string().min(1),
    natural_key: z.string().min(1),
    release_identifier: z.string().min(1),
    mapping_revision: z.string().min(1),
    fire_year: z.number().int().nullable(),
    ignition_date: daySchema,
    observed_day: daySchema,
    data_available_at: instantSchema,
    fire_name: z.string().nullable(),
    fire_type: z.string().nullable(),
    assessment_type: z.string().nullable(),
    acres: finiteNumberSchema.nullable(),
    severity_class: z.string().nullable(),
    dnbr_offset: z.number().int().nullable(),
    dnbr_standard_deviation: z.number().int().nullable(),
    nodata_threshold: z.number().int().nullable(),
    greenness_threshold: z.number().int().nullable(),
    low_threshold: z.number().int().nullable(),
    moderate_threshold: z.number().int().nullable(),
    high_threshold: z.number().int().nullable(),
    allowed_client_exposure: z.boolean(),
    geom: z.string().min(1),
  })
  .strict();

export interface ParquetBurnScar {
  fireId: string;
  fireName: string | null;
  fireYear: number | null;
  fireType: string | null;
  assessmentType: string | null;
  ignitionDate: string;
  observedDay: string;
  acres: number | null;
  severityClass: string | null;
  dataAvailableAt: string;
  geometry: ParquetPolygonGeometry;
}

/** True when the viewport reaches outside the snapshot's published envelope, so the answer is partial. */
function validateBurnSnapshotScope(
  snapshot: MtbsSnapshotMetadata,
  request: { day: string; servedDay: string; bbox: string | undefined },
  scope: BurnSnapshotPublicationScope = SUPPORTED_BURN_SNAPSHOT_SCOPE
): boolean {
  if (snapshot.availableDay !== request.servedDay || snapshot.availableDay > request.day
    || snapshot.coveredYears.from !== scope.coveredYears.from
    || snapshot.coveredYears.to !== scope.coveredYears.to
    || snapshot.bbox.some((value, index) => value !== scope.envelope[index])) {
    throw contractError("burn-severity snapshot publication scope is unsupported");
  }
  if (request.bbox === undefined) return false;
  const bounds = request.bbox.split(",").map(Number);
  if (bounds.length !== 4 || !bounds.every(Number.isFinite)
    || bounds[0] >= bounds[2] || bounds[1] >= bounds[3]) {
    throw contractError("burn-severity viewport is malformed");
  }
  return bounds[0] < snapshot.bbox[0] || bounds[1] < snapshot.bbox[1]
    || bounds[2] > snapshot.bbox[2] || bounds[3] > snapshot.bbox[3];
}

/**
 * Every indexed release day at or before `day`, newest first, capped one past the read ceiling so
 * the caller can still see that it was capped.
 */
function indexedReleaseDays(publishedRanges: readonly DayRange[], day: string): string[] {
  const releaseDays = new Set<string>();
  for (const range of [...publishedRanges].sort((a, b) => b.to.localeCompare(a.to))) {
    let releaseDay = range.to < day ? range.to : day;
    while (releaseDay >= range.from && releaseDays.size <= BURN_SEVERITY_MAX_RELEASES) {
      releaseDays.add(releaseDay);
      releaseDay = addUtcDays(releaseDay, -1);
    }
    if (releaseDays.size > BURN_SEVERITY_MAX_RELEASES) break;
  }
  return [...releaseDays].sort().reverse();
}

/**
 * The union of MTBS releases standing at the requested day, or the one snapshot that replaces them.
 *
 * See `src/lib/server/services/AGENTS.md` §burn-severity.
 */
export async function getParquetBurnSeverity(
  input: ParquetViewportRead
): Promise<ParquetReaderResult<readonly ParquetBurnScar[]>> {
  const nowMs = input.nowMs ?? Date.now();
  const day = selectedDay(input.date, nowMs);
  rejectFutureDay(day, nowMs, "burn-severity");
  const zoomTier = resolveZoomTier(input.mapZoom);
  const releaseRequest = {
    layer: "burn-severity",
    zoomTier,
    ...(input.bbox === undefined ? {} : { bbox: input.bbox }),
    ...(input.signal === undefined ? {} : { signal: input.signal }),
  } as const;

  return boundedResult(async () => {
    const coverage = await getParquetWarehouseCoverage();
    const lane = coverage.lanes.find((entry) => entry.layer === "burn-severity"
      && entry.kind === "observed" && entry.zoomTier === zoomTier);
    if (!lane || lane.nature !== "release_series" || lane.withheldReason !== null
      || lane.coverageAuthority !== "availability" || !lane.availabilityGenerationSha256
      || !lane.availabilityPointerKey) {
      throw contractError("burn-severity requires authoritative publication coverage for the requested rung");
    }
    const indexedDays = indexedReleaseDays(lane.publishedRanges, day);
    const scars: ParquetBurnScar[] = [];
    let mtbsSnapshot: MtbsSnapshotMetadata | undefined;
    let newestServedDay: string | null = null;
    let truncated = indexedDays.length > BURN_SEVERITY_MAX_RELEASES
      || lane.gapRanges.some((range) => range.from <= day)
      || coverage.evaluatedThroughDay < day;

    const newerAbsence = lane.governedAbsenceRanges.some((range) => range.from <= day
      && (range.to < day ? range.to : day) > (indexedDays[0] ?? ""));
    let remainingReads = BURN_SEVERITY_MAX_RELEASES;
    let prefetchedRelease: ParquetPlaneEnvelope | undefined;
    if (newerAbsence) {
      const latest = await getParquetLatestRelease({ ...releaseRequest, asOfDay: day });
      remainingReads -= 1;
      if (latest.state === "day_not_written" || latest.state === "lane_never_written") {
        throw contractError("burn-severity release discovery disagrees with indexed availability");
      }
      if ("mtbsSnapshot" in latest && latest.mtbsSnapshot && "servedDay" in latest) {
        const outsideScope = validateBurnSnapshotScope(latest.mtbsSnapshot, { day, servedDay: latest.servedDay, bbox: input.bbox });
        if (latest.state === "governed_absence") {
          if (latest.mtbsSnapshot.sourceRowCount !== 0) {
            throw contractError("burn-severity snapshot absence disagrees with its captured source");
          }
          return { state: "ready", requestedDay: day, servedDay: latest.servedDay, data: [],
            truncated: outsideScope || coverage.evaluatedThroughDay < day,
            mtbsSnapshot: latest.mtbsSnapshot };
        }
        if (latest.state !== "published" || !indexedDays.includes(latest.servedDay)) {
          throw contractError("burn-severity snapshot disagrees with indexed publication");
        }
      }
      if (latest.state === "published" && latest.servedDay === indexedDays[0]) {
        prefetchedRelease = latest;
      }
    }
    const releaseLimit = remainingReads + (prefetchedRelease ? 1 : 0);
    truncated ||= indexedDays.length > releaseLimit;
    // MTBS published ranges are release dates, unlike carried drought coverage.
    for (const asOfDay of (indexedDays.length ? indexedDays.slice(0, releaseLimit) : [day])) {
      const envelope = prefetchedRelease?.state === "published" && prefetchedRelease.servedDay === asOfDay
        ? prefetchedRelease : await getParquetLatestRelease({ ...releaseRequest, asOfDay });
      const snapshot = "mtbsSnapshot" in envelope ? envelope.mtbsSnapshot : undefined;
      if (snapshot && "servedDay" in envelope) {
        validateBurnSnapshotScope(snapshot, { day, servedDay: envelope.servedDay, bbox: input.bbox });
      }
      const answer = mapEnvelope(envelope, (rows) => {
        const parsed = parseRows(rows, burnSeverityRowSchema, "burn-severity");
        if (snapshot) {
          const seenFireIds = new Set<string>();
          for (const row of parsed) {
            if (row.fire_year === null || row.fire_year < snapshot.coveredYears.from
              || row.fire_year > snapshot.coveredYears.to || seenFireIds.has(row.fire_id)
              || row.observed_day !== snapshot.availableDay
              || row.release_identifier !== `mtbs-current-snapshot:${snapshot.manifestSha256}`
              || Date.parse(row.data_available_at) !== Date.parse(`${snapshot.availableDay}T00:00:00Z`)) {
              throw contractError("burn-severity snapshot rows disagree with publication metadata");
            }
            seenFireIds.add(row.fire_id);
          }
          if (parsed.length > snapshot.sourceRowCount) {
            throw contractError("burn-severity snapshot exceeds its captured row count");
          }
        }
        return parsed.map((row) => ({
          fireId: row.fire_id, fireName: row.fire_name, fireYear: row.fire_year,
          fireType: row.fire_type, assessmentType: row.assessment_type,
          ignitionDate: row.ignition_date, observedDay: row.observed_day,
          acres: row.acres, severityClass: row.severity_class, dataAvailableAt: row.data_available_at,
          geometry: decodePolygonGeometry(row.geom, "burn-severity"),
        }));
      });
      if (!indexedDays.length) {
        if (answer.state === "ready") throw contractError("burn-severity publication disagrees with its coverage index");
        return answer;
      }
      if (answer.state !== "ready" || answer.servedDay !== asOfDay) {
        throw contractError("burn-severity indexed release did not serve its exact publication day");
      }
      if (snapshot) {
        // The supported snapshot covers every completed-cohort year; see services/AGENTS.md.
        if (scars.some((scar) => scar.fireYear === null
          || scar.fireYear < snapshot.coveredYears.from || scar.fireYear > snapshot.coveredYears.to)) {
          throw contractError("burn-severity snapshot cannot replace history outside its declared scope");
        }
        scars.length = 0;
        mtbsSnapshot = snapshot;
        newestServedDay = answer.servedDay;
        scars.push(...answer.data);
        truncated = answer.truncated || coverage.evaluatedThroughDay < day
          || validateBurnSnapshotScope(snapshot, { day, servedDay: answer.servedDay, bbox: input.bbox });
        break;
      }
      newestServedDay ??= answer.servedDay;
      scars.push(...answer.data);
      truncated ||= answer.truncated;
    }

    if (newestServedDay === null) {
      throw contractError("burn-severity indexed history returned no release");
    }
    // The served day is the NEWEST release in the union, which is the day the map is drawing: an
    // older member does not make the answer older than its freshest release.
    return { state: "ready", requestedDay: day, servedDay: newestServedDay, data: scars, truncated,
      ...(mtbsSnapshot ? { mtbsSnapshot } : {}) };
  });
}
