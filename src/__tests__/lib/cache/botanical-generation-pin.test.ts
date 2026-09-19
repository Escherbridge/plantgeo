/**
 * The botanical occurrence plane's local-first caching, and specifically the one property the
 * rest of the cache had no need for until this layer joined it: a stored answer must never be
 * served once the warehouse has published a newer generation.
 *
 * Every other allowlisted layer puts everything identifying its answer into the query key.
 * `environmental.getBotanicalOccurrences` resolves `release_set_id` SERVER-side from the plane's
 * `/current` pointer, so two reads either side of a publication share a `queryHash` and differ
 * only in the `releaseSetId` on the payload -- which is what these cases pin down. It matters
 * because `botanical-occurrences` is `release_series`, hence `manual`: a 365-day TTL and no
 * background revalidation, so the generation check is the ONLY thing standing between a reader
 * and a year of silently superseded provenance.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { QueryFunctionContext } from "@tanstack/react-query";
import { getAllEntries } from "@/lib/cache/indexeddb-store";
import {
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

const FAKE_CONTEXT = {} as QueryFunctionContext;

const BOTANICAL_PATH = ["environmental", "getBotanicalOccurrences"];

/** The inputs the hook actually sends (useViewportProxiedLayers.ts:444-459), minus the defaults. */
function botanicalInput(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return { bbox: "-124,44,-122,46", zoom: 12, limit: 2000, ...overrides };
}

/** A `detail` answer pinned to one generation, shaped like the client's decoded union member. */
function detailAnswer(releaseSetId: string, publishedAt: string | null = "2026-09-01T00:00:00Z") {
  return {
    state: "detail" as const,
    releaseSetId,
    publishedAt,
    taxonomyRecipeVersion: "r1",
    qcPolicyVersion: "q1",
    truncated: false,
    nextCursor: null,
    features: [{ id: `specimen-from-${releaseSetId}` }],
    counts: { returned: 1, matched: 1, withheld: 0, nonspatial: 0, excludedByQc: 0 },
  };
}

/** The aggregate band's answer; same provenance fields, different payload shape. */
function aggregateAnswer(releaseSetId: string) {
  return {
    state: "aggregate" as const,
    releaseSetId,
    publishedAt: "2026-09-01T00:00:00Z",
    taxonomyRecipeVersion: "r1",
    qcPolicyVersion: "q1",
    truncated: false,
    nextCursor: null,
    supportId: "h3-5",
    cells: [{ id: `cell-from-${releaseSetId}` }],
    counts: { returned: 1, matched: 1 },
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

describe("allowlisting", () => {
  it("opts the botanical read in on its bbox, with no date of its own", () => {
    // The plane is pinned to a RELEASE, not a day, so it carries no `date` -- which is exactly
    // why `isPersistableQueryKey` accepts a bbox OR a date rather than requiring both.
    expect(isPersistableQueryKey(trpcQueryKey(BOTANICAL_PATH, botanicalInput()))).toBe(true);
  });
});

describe("resolveEntryGeneration", () => {
  it("reads the generation off an answer that names one", () => {
    expect(resolveEntryGeneration(detailAnswer("rs-1"))).toBe("rs-1");
    expect(resolveEntryGeneration(aggregateAnswer("rs-1"))).toBe("rs-1");
  });

  it("returns null for answers and payloads that name no generation", () => {
    // `refused`/`unavailable` carry no records, so they misattribute nothing; every other
    // allowlisted layer's payload is in the same position.
    expect(resolveEntryGeneration({ state: "unavailable", reason: "no_release", note: "" })).toBeNull();
    expect(resolveEntryGeneration({ availability: "published", features: [] })).toBeNull();
    expect(resolveEntryGeneration(null)).toBeNull();
  });

  it("refuses a non-string generation rather than coercing it", () => {
    // `String(null)` is `"null"`, which would make two unstamped answers compare equal and pin
    // a layer to a generation that does not exist.
    expect(resolveEntryGeneration({ releaseSetId: null })).toBeNull();
    expect(resolveEntryGeneration({ releaseSetId: "" })).toBeNull();
    expect(resolveEntryGeneration({ releaseSetId: 7 })).toBeNull();
  });
});

describe("write/read round trip", () => {
  it("stores a detail answer and serves the second read from disk without refetching", async () => {
    const key = trpcQueryKey(BOTANICAL_PATH, botanicalInput());
    const query = makeQuery(key, JSON.stringify(key));
    const queryFn = vi.fn().mockResolvedValue(detailAnswer("rs-1"));

    const first = await persister(queryFn, FAKE_CONTEXT, query);
    expect(first).toEqual(detailAnswer("rs-1"));
    expect(queryFn).toHaveBeenCalledTimes(1);

    const second = await persister(queryFn, FAKE_CONTEXT, query);
    expect(second).toEqual(detailAnswer("rs-1"));
    // The whole point: a reader returning to an already-seen viewport costs no network at all.
    // `botanical-occurrences` is manual, so no background revalidation fires either.
    expect(queryFn).toHaveBeenCalledTimes(1);
  });

  it("stores the aggregate band under its own entry, not the detail one", async () => {
    const detailKey = trpcQueryKey(BOTANICAL_PATH, botanicalInput({ zoom: 12 }));
    const aggregateKey = trpcQueryKey(BOTANICAL_PATH, botanicalInput({ zoom: 6 }));

    await persister(
      vi.fn().mockResolvedValue(detailAnswer("rs-1")),
      FAKE_CONTEXT,
      makeQuery(detailKey, JSON.stringify(detailKey))
    );
    await persister(
      vi.fn().mockResolvedValue(aggregateAnswer("rs-1")),
      FAKE_CONTEXT,
      makeQuery(aggregateKey, JSON.stringify(aggregateKey))
    );

    const entries = await getAllEntries<StoredLayerQueryEntry>(STORE_CONFIG);
    expect(entries).toHaveLength(2);
    // `zoom` rides in the queryHash, so a band switch is a different entry rather than a stale
    // hit that would feed grid cells to the specimen-point layer.
    expect(entries.map((entry) => (entry.value as { state: string }).state).sort()).toEqual([
      "aggregate",
      "detail",
    ]);
  });

  it("keys the five filters separately, so a filtered view never answers an unfiltered one", async () => {
    const unfiltered = trpcQueryKey(BOTANICAL_PATH, botanicalInput());
    const filtered = trpcQueryKey(BOTANICAL_PATH, botanicalInput({ family: "Rosaceae" }));
    const queryFn = vi.fn().mockResolvedValue(detailAnswer("rs-1"));

    await persister(queryFn, FAKE_CONTEXT, makeQuery(unfiltered, JSON.stringify(unfiltered)));
    await persister(queryFn, FAKE_CONTEXT, makeQuery(filtered, JSON.stringify(filtered)));

    expect(queryFn).toHaveBeenCalledTimes(2);
    expect(await getAllEntries<StoredLayerQueryEntry>(STORE_CONFIG)).toHaveLength(2);
  });

  it("attributes the entry to the botanical layer so a per-layer reset can find it", async () => {
    const key = trpcQueryKey(BOTANICAL_PATH, botanicalInput());
    await persister(
      vi.fn().mockResolvedValue(detailAnswer("rs-1")),
      FAKE_CONTEXT,
      makeQuery(key, JSON.stringify(key))
    );

    const [entry] = await getAllEntries<StoredLayerQueryEntry>(STORE_CONFIG);
    expect(entry.layerId).toBe("botanical-occurrences");
    // No date in the input, so the entry names no day and contributes to no sync track -- the
    // same position `getWatersheds` is in, and correct here: a release is not a day.
    expect(entry.day).toBeUndefined();
  });
});

describe("generation pinning", () => {
  it("does not serve an entry from a generation that is no longer current", async () => {
    const viewportA = trpcQueryKey(BOTANICAL_PATH, botanicalInput({ bbox: "-124,44,-122,46" }));
    const viewportB = trpcQueryKey(BOTANICAL_PATH, botanicalInput({ bbox: "-120,44,-118,46" }));

    // Viewport A is read and cached under generation rs-1.
    const oldFetch = vi.fn().mockResolvedValue(detailAnswer("rs-1"));
    await persister(oldFetch, FAKE_CONTEXT, makeQuery(viewportA, JSON.stringify(viewportA)));

    // The warehouse publishes rs-2, which the reader learns by panning to a viewport that misses.
    const newFetch = vi.fn().mockResolvedValue(detailAnswer("rs-2"));
    await persister(newFetch, FAKE_CONTEXT, makeQuery(viewportB, JSON.stringify(viewportB)));

    // Panning BACK to viewport A must not replay the rs-1 answer, even though its entry is well
    // inside a 365-day TTL. It refetches and returns the current generation instead.
    const refetch = vi.fn().mockResolvedValue(detailAnswer("rs-2"));
    const served = await persister(
      refetch,
      FAKE_CONTEXT,
      makeQuery(viewportA, JSON.stringify(viewportA))
    );

    expect(refetch).toHaveBeenCalledTimes(1);
    expect((served as { releaseSetId: string }).releaseSetId).toBe("rs-2");
  });

  it("replaces the superseded entry on disk once a read of it misses", async () => {
    const viewportA = trpcQueryKey(BOTANICAL_PATH, botanicalInput({ bbox: "-124,44,-122,46" }));
    const viewportB = trpcQueryKey(BOTANICAL_PATH, botanicalInput({ bbox: "-120,44,-118,46" }));

    await persister(
      vi.fn().mockResolvedValue(detailAnswer("rs-1")),
      FAKE_CONTEXT,
      makeQuery(viewportA, JSON.stringify(viewportA))
    );
    // A MISS is what teaches the pin; see the limitation case below for why that is the only teacher.
    await persister(
      vi.fn().mockResolvedValue(detailAnswer("rs-2")),
      FAKE_CONTEXT,
      makeQuery(viewportB, JSON.stringify(viewportB))
    );
    await persister(
      vi.fn().mockResolvedValue(detailAnswer("rs-2")),
      FAKE_CONTEXT,
      makeQuery(viewportA, JSON.stringify(viewportA))
    );

    // The stale row is rewritten, not merely skipped: re-checking it on every future read would
    // hold budget against an answer that can never be served again.
    const entries = await getAllEntries<StoredLayerQueryEntry>(STORE_CONFIG);
    expect(entries).toHaveLength(2);
    for (const entry of entries) expect(resolveEntryGeneration(entry.value)).toBe("rs-2");
  });

  it("LIMITATION: a fully-cached reader is not told about a publication by this mechanism alone", async () => {
    // Stated as a test because it is the honest boundary of the pin, and a future reader will
    // otherwise assume more than it delivers. The pin is learned from answers that come back
    // through the server; a reader whose every viewport hits cache sends no request, so nothing
    // teaches it a new generation exists. The pin's job is narrower and it does it completely:
    // once ANY read proves the generation moved, no other entry of that layer is served again.
    //
    // What covers the saturated case is the control that already exists for every `manual` layer
    // -- `requestLayerRefresh` (layer-cache-policy-store.ts:187) stamps the layer and turns every
    // older entry into a miss -- which is exactly the escape hatch `release_series`/manual is
    // designed around, rather than a gap this cache invented.
    const key = trpcQueryKey(BOTANICAL_PATH, botanicalInput());
    const query = makeQuery(key, JSON.stringify(key));

    await persister(vi.fn().mockResolvedValue(detailAnswer("rs-1")), FAKE_CONTEXT, query);

    const neverConsulted = vi.fn().mockResolvedValue(detailAnswer("rs-2"));
    const served = await persister(neverConsulted, FAKE_CONTEXT, query);

    expect(neverConsulted).not.toHaveBeenCalled();
    expect((served as { releaseSetId: string }).releaseSetId).toBe("rs-1");
  });

  it("still serves an entry whose generation is the current one", async () => {
    const viewportA = trpcQueryKey(BOTANICAL_PATH, botanicalInput({ bbox: "-124,44,-122,46" }));
    const viewportB = trpcQueryKey(BOTANICAL_PATH, botanicalInput({ bbox: "-120,44,-118,46" }));

    await persister(
      vi.fn().mockResolvedValue(detailAnswer("rs-1")),
      FAKE_CONTEXT,
      makeQuery(viewportA, JSON.stringify(viewportA))
    );
    // A second viewport on the SAME generation must not invalidate the first.
    await persister(
      vi.fn().mockResolvedValue(detailAnswer("rs-1")),
      FAKE_CONTEXT,
      makeQuery(viewportB, JSON.stringify(viewportB))
    );

    const shouldNotRun = vi.fn().mockResolvedValue(detailAnswer("rs-1"));
    await persister(shouldNotRun, FAKE_CONTEXT, makeQuery(viewportA, JSON.stringify(viewportA)));
    expect(shouldNotRun).not.toHaveBeenCalled();
  });

  it("serves a cached entry on a fresh page load, before any generation is known", async () => {
    // The asymmetry that makes this safe to ship: rejection needs POSITIVE evidence of a newer
    // generation. With no pin learned yet -- every first read of every session -- the entry is
    // served. The inverse would empty the cache on load and the feature would never help anyone.
    const key = trpcQueryKey(BOTANICAL_PATH, botanicalInput());
    const query = makeQuery(key, JSON.stringify(key));

    await persister(vi.fn().mockResolvedValue(detailAnswer("rs-1")), FAKE_CONTEXT, query);
    resetGenerationPinsForTests(); // a reload: IndexedDB survives, module state does not

    const shouldNotRun = vi.fn().mockResolvedValue(detailAnswer("rs-1"));
    const served = await persister(shouldNotRun, FAKE_CONTEXT, query);
    expect(shouldNotRun).not.toHaveBeenCalled();
    expect((served as { releaseSetId: string }).releaseSetId).toBe("rs-1");
  });

  it("does not let one layer's generation invalidate another layer's entries", async () => {
    const botanicalKey = trpcQueryKey(BOTANICAL_PATH, botanicalInput());
    const vegetationKey = trpcQueryKey(["environmental", "getVegetationIndex"], {
      bbox: "-124,44,-122,46",
      date: "2026-09-01",
    });

    await persister(
      vi.fn().mockResolvedValue({ availability: "published", features: [] }),
      FAKE_CONTEXT,
      makeQuery(vegetationKey, JSON.stringify(vegetationKey))
    );
    await persister(
      vi.fn().mockResolvedValue(detailAnswer("rs-2")),
      FAKE_CONTEXT,
      makeQuery(botanicalKey, JSON.stringify(botanicalKey))
    );

    // The vegetation entry names no generation and belongs to another layer; a botanical
    // publication has nothing to say about it.
    const shouldNotRun = vi.fn();
    await persister(
      shouldNotRun,
      FAKE_CONTEXT,
      makeQuery(vegetationKey, JSON.stringify(vegetationKey))
    );
    expect(shouldNotRun).not.toHaveBeenCalled();
  });

  it("never caches a refused or unavailable answer as if it were records", async () => {
    const key = trpcQueryKey(BOTANICAL_PATH, botanicalInput());
    const query = makeQuery(key, JSON.stringify(key));

    // `unavailable` is a positive claim about the plane and is cacheable by the existing rule,
    // but it must not pin a generation, since it names none.
    await persister(
      vi.fn().mockResolvedValue({ state: "unavailable", reason: "no_release", note: "" }),
      FAKE_CONTEXT,
      query
    );

    const [entry] = await getAllEntries<StoredLayerQueryEntry>(STORE_CONFIG);
    expect(resolveEntryGeneration(entry.value)).toBeNull();
  });
});
