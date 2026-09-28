/**
 * The SSURGO admitted-release port's local-first caching (soil-survey S4, plan §1a row 73).
 *
 * SSURGO is `static_reference`, not `release_series` like the botanical plane
 * (`botanical-generation-pin.test.ts`), but shares its shape: `environmental.getSoilSurvey`
 * carries its generation as `revision` -- the admitted release's SHA-256 -- ON THE PAYLOAD rather
 * than in the query key, since two viewports either side of an admission share nothing that would
 * otherwise tell them apart. What is unique to this layer, and NOT shared with botanical, is that
 * its OWN `availability: "unavailable"` must never be persisted: every other layer's `unavailable`
 * is a governed, cacheable fact about a day; SSURGO's is pure serving/transport state -- an unbound
 * region, a below-native-rung zoom, or a release nobody has admitted yet -- and caching it would
 * let a stale refusal outlive the admission that made it wrong (`query-persister.ts`
 * §"SSURGO release pinning and the excluded unavailable answer").
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { QueryFunctionContext } from "@tanstack/react-query";
import { getAllEntries, getEntry } from "@/lib/cache/indexeddb-store";
import { resetLayerCachePolicyForTests } from "@/lib/cache/layer-cache-policy-store";
import {
  CACHE_SCHEMA_VERSION,
  STORE_CONFIG,
  createIndexedDbLayerQueryPersister,
  isPersistableQueryKey,
  resetCacheAccounting,
  resetGenerationPinsForTests,
  resolveEntryGeneration,
  type StoredLayerQueryEntry,
} from "@/lib/cache/query-persister";
import { useTimeSliderStore } from "@/stores/time-slider-store";
import { createFakeIndexedDb } from "./fake-indexeddb";

const persister = createIndexedDbLayerQueryPersister(() => null);

/** Mirrors `@trpc/react-query`'s `getQueryKeyInternal` shape for a `.useQuery(input)` call. */
function trpcQueryKey(path: string[], input?: Record<string, unknown>): readonly unknown[] {
  return input === undefined ? [path] : [path, { input, type: "query" }];
}

/** The persister only reads `.queryKey` and `.queryHash`. */
function makeQuery(queryKey: readonly unknown[], queryHash: string) {
  return { queryKey, queryHash } as unknown as Parameters<typeof persister>[2];
}

const SOIL_SURVEY_PATH = ["environmental", "getSoilSurvey"];

/** The adapted `ProxiedSoilSurveyCollection` shape `environmental.ts` actually returns. */
function unavailableAnswer(reason: string) {
  return {
    type: "FeatureCollection" as const,
    features: [] as unknown[],
    availability: "unavailable" as const,
    reason,
    truncated: false,
    unreadableGeometries: 0,
    observedAt: null,
    revision: null,
    granularity: "detail" as const,
    coverage: { cells: 0, covered: 0, ingested: 0 },
  };
}

function publishedAnswer(revision: string) {
  return {
    type: "FeatureCollection" as const,
    features: [{ type: "Feature" as const, id: `mu-${revision}`, properties: {} }],
    availability: "published" as const,
    reason: null,
    truncated: false,
    unreadableGeometries: 0,
    observedAt: "2026-09-13T00:00:00Z",
    revision,
    granularity: "detail" as const,
    coverage: { cells: 1, covered: 1, ingested: 1 },
  };
}

const originalIndexedDb = globalThis.indexedDB;
const initialTimeSliderState = useTimeSliderStore.getState();

beforeEach(() => {
  globalThis.indexedDB = createFakeIndexedDb() as unknown as IDBFactory;
  resetCacheAccounting();
  resetGenerationPinsForTests();
  useTimeSliderStore.setState(initialTimeSliderState, true);
});

afterEach(() => {
  globalThis.indexedDB = originalIndexedDb;
  resetGenerationPinsForTests();
  useTimeSliderStore.setState(initialTimeSliderState, true);
});

describe("SSURGO release pin: revision, not releaseSetId", () => {
  it("resolves the soil-survey layer's generation off `revision`", () => {
    expect(resolveEntryGeneration(publishedAnswer("a".repeat(64)), "soil-survey")).toBe(
      "a".repeat(64)
    );
    expect(resolveEntryGeneration(unavailableAnswer("soil_survey_zoom_in"), "soil-survey")).toBeNull();
  });

  // F13: a payload can carry a field NAMED `revision` for reasons that have nothing to do with
  // this layer. The field is read only once the caller has already attributed the entry to
  // soil-survey; it is never sniffed off the payload shape alone.
  it("does not pin a non-soil-survey layer on a coincidental `revision` field", () => {
    const lookalike = { availability: "published", features: [], revision: "c".repeat(64) };
    expect(resolveEntryGeneration(lookalike)).toBeNull(); // layerId omitted, same as `null`
    expect(resolveEntryGeneration(lookalike, "drought")).toBeNull();
    expect(resolveEntryGeneration(lookalike, "soil-survey")).toBe("c".repeat(64));
  });
});

describe("SSURGO release pin: the excluded unavailable answer", () => {
  it("does not persist an unavailable soil-survey answer", async () => {
    const key = trpcQueryKey(SOIL_SURVEY_PATH, { bbox: "0,0,1,1", zoom: 9 });
    const query = makeQuery(key, "ssurgo-unavailable");
    const queryFn = vi.fn().mockResolvedValue(unavailableAnswer("soil_survey_release_not_admitted"));

    const result = await persister(queryFn, {} as QueryFunctionContext, query);

    expect(result).toEqual(unavailableAnswer("soil_survey_release_not_admitted"));
    await expect(getEntry(STORE_CONFIG, "ssurgo-unavailable")).resolves.toBeNull();
  });

  it("still persists another layer's own unavailable answer (the SSURGO exclusion is not general)", async () => {
    const key = trpcQueryKey(["environmental", "getDroughtClassification"], { bbox: "0,0,1,1" });
    const query = makeQuery(key, "drought-unavailable");
    const queryFn = vi.fn().mockResolvedValue({
      type: "FeatureCollection",
      features: [],
      availability: "unavailable",
      reason: "stale",
    });

    await persister(queryFn, {} as QueryFunctionContext, query);

    const stored = await getEntry<StoredLayerQueryEntry>(STORE_CONFIG, "drought-unavailable");
    expect(stored?.value).toEqual({
      type: "FeatureCollection",
      features: [],
      availability: "unavailable",
      reason: "stale",
    });
  });

  it("rejects a stored unavailable soil-survey answer on read, even if it predates this rule", async () => {
    const key = trpcQueryKey(SOIL_SURVEY_PATH, { bbox: "0,0,1,1", zoom: 13 });
    const cacheKey = "ssurgo-stale-unavailable";
    const stale: StoredLayerQueryEntry = {
      key: cacheKey,
      schemaVersion: CACHE_SCHEMA_VERSION,
      createdAt: Date.now(),
      expiresAt: Date.now() + 24 * 60 * 60 * 1000, // well inside the 24h stale time
      lastAccessedAt: Date.now(),
      approxByteSize: 10,
      layerId: "soil-survey",
      value: unavailableAnswer("soil_survey_release_not_admitted"),
    };
    // Written directly rather than through the persister, to model an entry stored BEFORE this
    // rule existed (the write-path exclusion above means the persister itself can no longer
    // produce one) -- the read path must reject it independently.
    const { setEntry } = await import("@/lib/cache/indexeddb-store");
    await setEntry(STORE_CONFIG, cacheKey, stale);

    const freshPayload = publishedAnswer("a".repeat(64));
    const queryFn = vi.fn().mockResolvedValue(freshPayload);
    const result = await persister(queryFn, {} as QueryFunctionContext, makeQuery(key, cacheKey));

    expect(queryFn).toHaveBeenCalledTimes(1);
    expect(result).toEqual(freshPayload);
  });
});

describe("SSURGO release pin: a revision change supersedes", () => {
  it("stops serving a stored answer once a newer revision has been learned from another read", async () => {
    const oldRevision = "a".repeat(64);
    const newRevision = "b".repeat(64);
    const viewportA = trpcQueryKey(SOIL_SURVEY_PATH, { bbox: "-116,43,-115,44", zoom: 13 });
    const viewportB = trpcQueryKey(SOIL_SURVEY_PATH, { bbox: "-114,43,-113,44", zoom: 13 });

    // Viewport A is read and cached under the old admitted release.
    await persister(
      vi.fn().mockResolvedValue(publishedAnswer(oldRevision)),
      {} as QueryFunctionContext,
      makeQuery(viewportA, "ssurgo-a")
    );

    // A new release is admitted; a different viewport's read is the first to learn it.
    await persister(
      vi.fn().mockResolvedValue(publishedAnswer(newRevision)),
      {} as QueryFunctionContext,
      makeQuery(viewportB, "ssurgo-b")
    );

    // Panning back to viewport A must not replay the old-revision answer, even though its entry
    // is well inside SOIL_SURVEY_STALE_TIME_MS (24h).
    const refetch = vi.fn().mockResolvedValue(publishedAnswer(newRevision));
    const served = await persister(refetch, {} as QueryFunctionContext, makeQuery(viewportA, "ssurgo-a"));

    expect(refetch).toHaveBeenCalledTimes(1);
    expect((served as { revision: string }).revision).toBe(newRevision);
  });

  it("still serves an entry whose revision is the current one", async () => {
    const revision = "a".repeat(64);
    const viewportA = trpcQueryKey(SOIL_SURVEY_PATH, { bbox: "-116,43,-115,44", zoom: 13 });
    const viewportB = trpcQueryKey(SOIL_SURVEY_PATH, { bbox: "-114,43,-113,44", zoom: 13 });

    await persister(
      vi.fn().mockResolvedValue(publishedAnswer(revision)),
      {} as QueryFunctionContext,
      makeQuery(viewportA, "ssurgo-a2")
    );
    await persister(
      vi.fn().mockResolvedValue(publishedAnswer(revision)),
      {} as QueryFunctionContext,
      makeQuery(viewportB, "ssurgo-b2")
    );

    const shouldNotRun = vi.fn().mockResolvedValue(publishedAnswer(revision));
    await persister(shouldNotRun, {} as QueryFunctionContext, makeQuery(viewportA, "ssurgo-a2"));
    expect(shouldNotRun).not.toHaveBeenCalled();
  });

  it("does not let a soil-survey revision invalidate another layer's entries", async () => {
    const droughtKey = trpcQueryKey(["environmental", "getDroughtClassification"], {
      bbox: "0,0,1,1",
    });
    const soilSurveyKey = trpcQueryKey(SOIL_SURVEY_PATH, { bbox: "-116,43,-115,44", zoom: 13 });

    await persister(
      vi.fn().mockResolvedValue({ type: "FeatureCollection", features: [], availability: "published", reason: null }),
      {} as QueryFunctionContext,
      makeQuery(droughtKey, "drought-published")
    );
    await persister(
      vi.fn().mockResolvedValue(publishedAnswer("b".repeat(64))),
      {} as QueryFunctionContext,
      makeQuery(soilSurveyKey, "ssurgo-c")
    );

    const shouldNotRun = vi.fn();
    await persister(shouldNotRun, {} as QueryFunctionContext, makeQuery(droughtKey, "drought-published"));
    expect(shouldNotRun).not.toHaveBeenCalled();
  });
});

describe("SSURGO release pin: a rollback invalidates every cached viewport (S4 review finding 4)", () => {
  // `isSupersededByRefreshRequest` compares `createdAt < requestedAt` at millisecond resolution
  // (`layer-cache-policy-store.ts`). Real traffic always has network round trips between the
  // viewport-A write and the viewport-B rollback read, but three synchronous `await`s in a unit
  // test can land in the SAME millisecond and make that strict `<` false by coincidence -- a
  // monotonic `Date.now` here is what makes these three tests deterministic rather than flaky.
  // `useLayerCachePolicyStore` (the store `requestLayerRefresh` writes to) is a module-level
  // singleton the file's outer `beforeEach` never touches -- without resetting it here, a stamp
  // left by one test in this block outlives it and, worse, compares as a "future" refresh
  // request against the NEXT test's freshly-reset mock clock (below), false-invalidating an
  // entry that test never asked to invalidate.
  let clock = 0;
  beforeEach(() => {
    resetLayerCachePolicyForTests();
    clock = 1_700_000_000_000;
    vi.spyOn(Date, "now").mockImplementation(() => (clock += 1));
  });
  afterEach(() => {
    vi.restoreAllMocks();
    resetLayerCachePolicyForTests();
  });

  it("stops serving a cached published answer once another viewport reports the release withdrawn", async () => {
    const viewportA = trpcQueryKey(SOIL_SURVEY_PATH, { bbox: "-116,43,-115,44", zoom: 13 });
    const viewportB = trpcQueryKey(SOIL_SURVEY_PATH, { bbox: "-114,43,-113,44", zoom: 13 });

    // Viewport A is read and cached while the release is admitted.
    await persister(
      vi.fn().mockResolvedValue(publishedAnswer("a".repeat(64))),
      {} as QueryFunctionContext,
      makeQuery(viewportA, "ssurgo-rollback-a")
    );

    // The admission pin is unset; a different viewport is the first read to see it.
    await persister(
      vi.fn().mockResolvedValue(unavailableAnswer("soil_survey_release_not_admitted")),
      {} as QueryFunctionContext,
      makeQuery(viewportB, "ssurgo-rollback-b")
    );

    // Panning back to viewport A must NOT replay the withdrawn release from cache, even though
    // its entry is well inside MANUAL_TTL_MS (365 days) and names no superseding revision.
    const refetch = vi.fn().mockResolvedValue(unavailableAnswer("soil_survey_release_not_admitted"));
    await persister(refetch, {} as QueryFunctionContext, makeQuery(viewportA, "ssurgo-rollback-a"));

    expect(refetch).toHaveBeenCalledTimes(1);
  });

  it("also invalidates on an unbound-region refusal, not only a withdrawn admission", async () => {
    const viewportA = trpcQueryKey(SOIL_SURVEY_PATH, { bbox: "-116,43,-115,44", zoom: 13 });
    const viewportB = trpcQueryKey(SOIL_SURVEY_PATH, { bbox: "-114,43,-113,44", zoom: 13 });

    await persister(
      vi.fn().mockResolvedValue(publishedAnswer("a".repeat(64))),
      {} as QueryFunctionContext,
      makeQuery(viewportA, "ssurgo-unbound-a")
    );
    await persister(
      vi.fn().mockResolvedValue(unavailableAnswer("no_source_bound_in_region")),
      {} as QueryFunctionContext,
      makeQuery(viewportB, "ssurgo-unbound-b")
    );

    const refetch = vi.fn().mockResolvedValue(unavailableAnswer("no_source_bound_in_region"));
    await persister(refetch, {} as QueryFunctionContext, makeQuery(viewportA, "ssurgo-unbound-a"));

    expect(refetch).toHaveBeenCalledTimes(1);
  });

  it("does not invalidate on a zoom-in refusal, which is not a rollback", async () => {
    const viewportA = trpcQueryKey(SOIL_SURVEY_PATH, { bbox: "-116,43,-115,44", zoom: 13 });
    const viewportB = trpcQueryKey(SOIL_SURVEY_PATH, { bbox: "-114,43,-113,44", zoom: 9 });

    await persister(
      vi.fn().mockResolvedValue(publishedAnswer("a".repeat(64))),
      {} as QueryFunctionContext,
      makeQuery(viewportA, "ssurgo-zoomin-a")
    );
    await persister(
      vi.fn().mockResolvedValue(unavailableAnswer("soil_survey_zoom_in")),
      {} as QueryFunctionContext,
      makeQuery(viewportB, "ssurgo-zoomin-b")
    );

    const shouldNotRun = vi.fn();
    await persister(shouldNotRun, {} as QueryFunctionContext, makeQuery(viewportA, "ssurgo-zoomin-a"));

    expect(shouldNotRun).not.toHaveBeenCalled();
  });
});

describe("SSURGO cache key allowlisting (unchanged by this port)", () => {
  it("still opts getSoilSurvey in on its bbox", () => {
    const key = trpcQueryKey(SOIL_SURVEY_PATH, { bbox: "0,0,1,1", zoom: 13 });
    expect(isPersistableQueryKey(key)).toBe(true);
  });

  it("attributes every persisted entry to the soil-survey layer", async () => {
    const key = trpcQueryKey(SOIL_SURVEY_PATH, { bbox: "0,0,1,1", zoom: 13 });
    await persister(
      vi.fn().mockResolvedValue(publishedAnswer("a".repeat(64))),
      {} as QueryFunctionContext,
      makeQuery(key, "ssurgo-attribution")
    );

    const [entry] = await getAllEntries<StoredLayerQueryEntry>(STORE_CONFIG);
    expect(entry.layerId).toBe("soil-survey");
  });
});
