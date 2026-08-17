import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  useFireData,
  rememberFireResponse,
  MAX_CACHED_RESPONSES,
  type CachedFireResponse,
} from "@/hooks/useFireData";

/**
 * D3: on a 304 or a failed fetch, `data` is deliberately retained rather than blanked -- these
 * cases pin that the retention is LABELLED (`isStaleForRequestedDate`, `dataDate`) rather than
 * silent, and that a 304 repaints the payload it is actually a 304 FOR even after the slider
 * has visited a different day in between.
 */

function pointCollection(featureIds: string[]): GeoJSON.FeatureCollection {
  return {
    type: "FeatureCollection",
    features: featureIds.map((id) => ({
      type: "Feature",
      properties: { id },
      geometry: { type: "Point", coordinates: [0, 0] },
    })),
  };
}

/** The minimal `Response` surface `useFireData` actually reads. */
function okResponse(body: GeoJSON.FeatureCollection, etag: string) {
  return {
    status: 200,
    ok: true,
    headers: { get: (name: string) => (name === "ETag" ? etag : null) },
    json: async () => body,
  };
}

function notModifiedResponse() {
  return { status: 304, ok: false, headers: { get: () => null } };
}

function fetchMock() {
  return globalThis.fetch as ReturnType<typeof vi.fn>;
}

/** Renders the hook behind a `date` prop so a test can `rerender` it to a new day. */
function renderFireDataHook(initialDate: string) {
  return renderHook(({ date }: { date: string }) => useFireData(true, date), {
    initialProps: { date: initialDate },
  });
}

/** A response the test controls the settling of, so nothing is left pending across tests. */
function deferredResponse() {
  let resolve!: (value: ReturnType<typeof okResponse>) => void;
  const promise = new Promise<ReturnType<typeof okResponse>>((res) => {
    resolve = res;
  });
  return { promise, resolve };
}

/** F3/F8: the bounded response cache backing the 304-restores-its-own-payload fix. */
describe("rememberFireResponse", () => {
  function entry(tag: string): CachedFireResponse {
    return { etag: tag, data: pointCollection([tag]) };
  }

  it("retains entries under the bound", () => {
    const cache = new Map<string, CachedFireResponse>();
    rememberFireResponse(cache, "2026-08-10", entry("a"));
    rememberFireResponse(cache, "2026-08-11", entry("b"));

    expect(cache.size).toBe(2);
    expect(cache.get("2026-08-10")).toEqual(entry("a"));
  });

  it("evicts the least-recently-used key once the bound is exceeded", () => {
    const cache = new Map<string, CachedFireResponse>();
    for (let day = 1; day <= MAX_CACHED_RESPONSES; day++) {
      rememberFireResponse(cache, `2026-08-${String(day).padStart(2, "0")}`, entry(`e${day}`));
    }
    expect(cache.size).toBe(MAX_CACHED_RESPONSES);

    // One more insert pushes past the bound -- the OLDEST key (day 1, never re-touched) must
    // be the one evicted, not an arbitrary one.
    rememberFireResponse(cache, "2026-08-99", entry("newest"));

    expect(cache.size).toBe(MAX_CACHED_RESPONSES);
    expect(cache.has("2026-08-01")).toBe(false);
    expect(cache.has("2026-08-99")).toBe(true);
  });

  it("re-touching an existing key refreshes its recency instead of duplicating it", () => {
    const cache = new Map<string, CachedFireResponse>();
    for (let day = 1; day <= MAX_CACHED_RESPONSES; day++) {
      rememberFireResponse(cache, `2026-08-${String(day).padStart(2, "0")}`, entry(`e${day}`));
    }

    // Re-touch the OLDEST key (day 1) so it is no longer the least-recently-used one.
    rememberFireResponse(cache, "2026-08-01", entry("e1-refreshed"));
    rememberFireResponse(cache, "2026-08-99", entry("newest"));

    // Day 1 survived the eviction that followed because touching it moved it to the back of
    // the LRU order; day 2 (now the oldest untouched key) is what gets evicted instead.
    expect(cache.size).toBe(MAX_CACHED_RESPONSES);
    expect(cache.has("2026-08-01")).toBe(true);
    expect(cache.get("2026-08-01")).toEqual(entry("e1-refreshed"));
    expect(cache.has("2026-08-02")).toBe(false);
  });
});

describe("useFireData", () => {
  beforeEach(() => {
    fetchMock().mockReset();
  });

  afterEach(() => {
    act(() => cleanup());
  });

  it("starts stale for the requested date before any fetch has landed", () => {
    // Never resolves during this test -- proves the SYNCHRONOUS initial state, not a race with
    // whatever the mock would eventually return.
    fetchMock().mockReturnValue(new Promise(() => {}));

    const { result } = renderHook(() => useFireData(true, "2026-08-10"));

    expect(result.current.isStaleForRequestedDate).toBe(true);
    expect(result.current.dataDate).toBeUndefined();
    expect(result.current.data.features).toEqual([]);
  });

  it("clears staleness once the fetch for the requested date lands", async () => {
    fetchMock().mockResolvedValue(okResponse(pointCollection(["a"]), 'W/"day1"'));

    const { result } = renderHook(() => useFireData(true, "2026-08-10"));

    await waitFor(() => expect(result.current.isStaleForRequestedDate).toBe(false));
    expect(result.current.dataDate).toBe("2026-08-10");
    expect(result.current.count).toBe(1);
  });

  it("resolves an omitted date (the live window) as not-stale once loaded, not as a literal 'live' date", async () => {
    fetchMock().mockResolvedValue(okResponse(pointCollection(["a"]), 'W/"live1"'));

    const { result, unmount } = renderHook(() => useFireData(true, undefined));

    await waitFor(() => expect(result.current.isStaleForRequestedDate).toBe(false));
    expect(result.current.dataDate).toBeUndefined();

    unmount(); // stop the live-window poll interval this hook arms for date === undefined
  });

  it("marks retained data stale for the new date immediately on a date change, before the new fetch resolves", async () => {
    fetchMock().mockResolvedValueOnce(okResponse(pointCollection(["a", "b"]), 'W/"day1"'));

    const { result, rerender } = renderFireDataHook("2026-08-10");
    await waitFor(() => expect(result.current.dataDate).toBe("2026-08-10"));
    expect(result.current.count).toBe(2);

    // The next fetch does not settle until this test explicitly resolves it below -- what is
    // being pinned here is the state immediately after the date changes, before any response
    // (success OR failure) exists yet.
    const secondFetch = deferredResponse();
    fetchMock().mockReturnValueOnce(secondFetch.promise);
    act(() => rerender({ date: "2026-08-11" }));

    // Retained rather than blanked (the D5 blank-and-refill antipattern this hook must not
    // reintroduce) -- but no longer asserted as 2026-08-11's own detections.
    expect(result.current.count).toBe(2);
    expect(result.current.dataDate).toBe("2026-08-10");
    expect(result.current.isStaleForRequestedDate).toBe(true);

    // Settle the in-flight fetch so nothing is left pending once this test ends.
    secondFetch.resolve(okResponse(pointCollection(["c"]), 'W/"day2"'));
    await waitFor(() => expect(result.current.dataDate).toBe("2026-08-11"));
  });

  it("retains the previous day's detections, still labelled stale, when the new day's fetch fails", async () => {
    fetchMock().mockResolvedValueOnce(okResponse(pointCollection(["a", "b"]), 'W/"day1"'));

    const { result, rerender } = renderFireDataHook("2026-08-10");
    await waitFor(() => expect(result.current.dataDate).toBe("2026-08-10"));

    fetchMock().mockRejectedValueOnce(new Error("network down"));
    act(() => rerender({ date: "2026-08-11" }));

    await waitFor(() => expect(result.current.error).not.toBeNull());
    expect(result.current.count).toBe(2);
    expect(result.current.dataDate).toBe("2026-08-10");
    expect(result.current.isStaleForRequestedDate).toBe(true);
  });

  it("repaints the correct day's payload on a 304, even after visiting a different day in between", async () => {
    fetchMock().mockResolvedValueOnce(okResponse(pointCollection(["day-a"]), 'W/"a"'));
    const { result, rerender } = renderFireDataHook("2026-08-10");
    await waitFor(() => expect(result.current.dataDate).toBe("2026-08-10"));
    expect(result.current.data.features[0]?.properties?.id).toBe("day-a");

    fetchMock().mockResolvedValueOnce(okResponse(pointCollection(["day-b"]), 'W/"b"'));
    act(() => rerender({ date: "2026-08-11" }));
    await waitFor(() => expect(result.current.dataDate).toBe("2026-08-11"));
    expect(result.current.data.features[0]?.properties?.id).toBe("day-b");

    // Revisiting 2026-08-10 sends its cached ETag ("a") and the server 304s -- without the
    // response-cache fix, `data` would stay "day-b" (this hook's OLD behaviour: leave `data`
    // untouched on a 304), silently mislabelled as 2026-08-10's own answer.
    fetchMock().mockResolvedValueOnce(notModifiedResponse());
    act(() => rerender({ date: "2026-08-10" }));

    await waitFor(() => expect(result.current.dataDate).toBe("2026-08-10"));
    expect(result.current.data.features[0]?.properties?.id).toBe("day-a");

    const lastCall = fetchMock().mock.calls.at(-1) as [string, RequestInit];
    expect((lastCall[1].headers as Record<string, string>)["If-None-Match"]).toBe('W/"a"');
  });
});
