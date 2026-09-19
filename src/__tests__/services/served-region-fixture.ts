import { getParquetWarehouseCoverage } from "@/lib/server/services/parquet-plane-client";
import { getRegion } from "@/lib/region/region";

/**
 * Priming the served region identity every row read now consults (style review W9, S4).
 *
 * `assertServedRegionMatchesBundle()` runs ahead of every Parquet row read and ahead of the
 * botanical plane's own read. It compares the region the NEWEST decoded census stated with the one
 * this bundle compiled for, and when this process has never decoded a census it reads one first.
 * That first read is the problem for any suite that stubs `fetchBoundedJson` with a queue: the
 * census read would consume the row answer the test queued, and the test would fail describing
 * something else entirely.
 *
 * So a suite that stubs the transport primes the identity once per test instead. After priming,
 * the guard costs nothing: the identity is module state beside the lane cache and outlives it.
 *
 * Shared rather than copied into each suite so the three that need it cannot drift on what a
 * census has to look like for the guard to read it.
 */

/** The smallest census body that decodes, stating `regionSlug` -- or omitting the key when null. */
export function censusStatingRegion(regionSlug: string | null): Record<string, unknown> {
  return {
    coverage_schema_version: 3,
    // A day no suite's clock -- real or faked -- is ever set to, so `isReusableSliderCoverage`
    // ALWAYS rejects the lane cache this leaves behind and a coverage-caching test still fetches
    // exactly as it did before priming. Sharing the suite's own census day made the primed entry
    // same-day reusable under `vi.setSystemTime`, and the first read of such a test fetched
    // nothing at all (verifier W10).
    generated_at: "2020-01-01T00:00:00+00:00",
    evaluated_through_day: "2020-01-01",
    lanes: [],
    ...(regionSlug === null ? {} : { region_slug: regionSlug }),
  };
}

/** The two `fetchBoundedJson` stub methods this fixture drives, named structurally. */
export interface QueueableFetchStub {
  mockResolvedValueOnce(value: unknown): unknown;
  mockReset(): unknown;
}

/**
 * Learns a served region identity through the REAL decode path, then clears the stub's record.
 *
 * Deliberately not a direct write to module state: priming through `getParquetWarehouseCoverage`
 * means a change to how the census states its region breaks these suites at the fixture rather than
 * leaving them asserting against an identity no wire body could produce.
 *
 * `evaluated_through_day` is a fixed day far outside every suite's clock, so the lane cache this
 * leaves behind is NOT reusable
 * and a suite that exercises coverage caching still fetches exactly as it did before.
 */
export async function primeServedRegion(
  fetchStub: QueueableFetchStub,
  regionSlug: string | null
): Promise<void> {
  fetchStub.mockResolvedValueOnce(censusStatingRegion(regionSlug));
  await getParquetWarehouseCoverage();
  fetchStub.mockReset();
}

/** The slug this bundle compiled for, read per call -- never snapshot at module scope. */
export function compiledRegionSlug(): string {
  return getRegion().slug;
}

/**
 * A slug this bundle was certainly NOT compiled for, resolved per call.
 *
 * A function and not a constant for the reason `federation.md` section 1 gives: a module-scope
 * `getRegion()` is an import-time region read, and a fixture is no place to reintroduce one.
 */
export function foreignRegionSlug(): string {
  return compiledRegionSlug() === "kenya-highlands" ? "pnw" : "kenya-highlands";
}
