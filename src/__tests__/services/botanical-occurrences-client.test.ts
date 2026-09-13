import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/**
 * Same seam-stubbing convention as `parquet-plane-client.test.ts`: only `providerUrl` and
 * `fetchBoundedJson` are stubbed, so the zod contract, error classes and route/param wiring in
 * `botanical-occurrences-client.ts` all run for real.
 */
vi.mock("@/lib/server/http/bounded-upstream", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/server/http/bounded-upstream")>();
  return {
    ...actual,
    providerUrl: vi.fn(actual.providerUrl),
    fetchBoundedJson: vi.fn(),
  };
});

import { fetchBoundedJson, providerUrl } from "@/lib/server/http/bounded-upstream";
import {
  getBotanicalOccurrences,
  getCurrentBotanicalReleaseSetId,
  BotanicalOccurrencesContractError,
  BotanicalOccurrencesUnavailableError,
} from "@/lib/server/services/botanical-occurrences-client";

const mockedProviderUrl = vi.mocked(providerUrl);
const mockedFetch = vi.mocked(fetchBoundedJson);

function requestedUrl(callIndex = 0): URL {
  return mockedFetch.mock.calls[callIndex][0] as URL;
}

beforeEach(() => {
  mockedProviderUrl.mockReset();
  mockedFetch.mockReset();
  mockedProviderUrl.mockImplementation(() => new URL("http://agri.internal:8000"));
});

afterEach(() => vi.useRealTimers());

const wireFeature = {
  occurrence_id: "occ-1",
  collection_key: "ubc",
  source_record_key: "ubc:12345",
  taxon_concept_id: "wfo-0000123",
  resolution_state: "matched",
  scientific_name: "Pseudotsuga menziesii",
  family: "Pinaceae",
  event_interval: { start: "2020-06-01", end: null, precision: "day" },
  longitude: -123.25,
  latitude: 49.26,
  coordinate_uncertainty_m: 30,
  spatial_class: "exact",
  membership: "confirmed",
  catalog_number: "V123456",
  recorded_by: "J. Doe",
  basis_of_record: "PreservedSpecimen",
  rights_uri: "https://example.org/rights",
  attribution_text: "UBC Herbarium",
};

const wireCell = {
  cell_id: "grid-0.25:12:34",
  geometry: {
    type: "Polygon" as const,
    coordinates: [
      [
        [-123.5, 49.0],
        [-123.25, 49.0],
        [-123.25, 49.25],
        [-123.5, 49.25],
        [-123.5, 49.0],
      ],
    ],
  },
  evaluation: "documented",
  documented_taxa: 12,
  record_count: 40,
  event_estimate: 38,
  collection_count: 2,
  excluded_by_qc: 1,
  possible_only_records: 0,
};

describe("getCurrentBotanicalReleaseSetId", () => {
  it("resolves the pinned release_set_id on the happy path", async () => {
    mockedFetch.mockResolvedValue({
      product: "botanical-occurrences",
      state: "current",
      release_set_id: "ubc-v16.43",
      published_at: "2026-09-10T00:00:00Z",
    });

    const releaseSetId = await getCurrentBotanicalReleaseSetId();

    expect(releaseSetId).toBe("ubc-v16.43");
    expect(requestedUrl().pathname).toBe("/api/v1/botanical-occurrences/current");
  });

  it("resolves when published_at is omitted entirely", async () => {
    mockedFetch.mockResolvedValue({
      product: "botanical-occurrences",
      state: "current",
      release_set_id: "ubc-v16.43",
    });

    await expect(getCurrentBotanicalReleaseSetId()).resolves.toBe("ubc-v16.43");
  });

  it("throws BotanicalOccurrencesUnavailableError on the unavailable state", async () => {
    mockedFetch.mockResolvedValue({
      product: "botanical-occurrences",
      state: "unavailable",
      reason: "no generation has ever been published for botanical-occurrences",
      note: "The pinned generation could not be opened. This says nothing about what it contains.",
    });

    await expect(getCurrentBotanicalReleaseSetId()).rejects.toBeInstanceOf(
      BotanicalOccurrencesUnavailableError
    );
  });

  it("throws BotanicalOccurrencesContractError on a malformed body", async () => {
    mockedFetch.mockResolvedValue({ state: "surprising" });

    await expect(getCurrentBotanicalReleaseSetId()).rejects.toBeInstanceOf(
      BotanicalOccurrencesContractError
    );
  });
});

describe("getBotanicalOccurrences", () => {
  it("resolves the current pointer, then queries with it, for a detail answer", async () => {
    mockedFetch.mockResolvedValueOnce({
      product: "botanical-occurrences",
      state: "current",
      release_set_id: "ubc-v16.43",
    });
    mockedFetch.mockResolvedValueOnce({
      product: "botanical-occurrences",
      state: "detail",
      release_set_id: "ubc-v16.43",
      published_at: "2026-09-10T00:00:00Z",
      taxonomy_recipe_version: "v3",
      qc_policy_version: "v2",
      support_id: null,
      features: [wireFeature],
      truncated: false,
      next_cursor: null,
      counts: { returned: 1, matched: 1, withheld: 0, nonspatial: 0, excluded_by_qc: 0 },
    });

    const result = await getBotanicalOccurrences({
      bbox: "-124,48,-122,50",
      zoom: 13,
    });

    expect(mockedFetch).toHaveBeenCalledTimes(2);
    const queryUrl = requestedUrl(1);
    expect(queryUrl.pathname).toBe("/api/v1/botanical-occurrences/query");
    expect(queryUrl.searchParams.get("release_set_id")).toBe("ubc-v16.43");
    expect(queryUrl.searchParams.get("bbox")).toBe("-124,48,-122,50");
    expect(queryUrl.searchParams.get("zoom")).toBe("13");
    // release_set_id is never "current" on the wire -- the whole point of resolving it internally.
    expect(queryUrl.searchParams.get("release_set_id")).not.toBe("current");

    expect(result.state).toBe("detail");
    if (result.state !== "detail") throw new Error("expected detail");
    expect(result.features).toHaveLength(1);
    expect(result.features[0]).toMatchObject({
      occurrenceId: "occ-1",
      scientificName: "Pseudotsuga menziesii",
      membership: "confirmed",
    });
    expect(result.counts).toEqual({ returned: 1, matched: 1, withheld: 0, nonspatial: 0, excludedByQc: 0 });
  });

  it("returns an aggregate answer below the detail floor", async () => {
    mockedFetch.mockResolvedValueOnce({
      product: "botanical-occurrences",
      state: "current",
      release_set_id: "ubc-v16.43",
    });
    mockedFetch.mockResolvedValueOnce({
      product: "botanical-occurrences",
      state: "aggregate",
      release_set_id: "ubc-v16.43",
      published_at: null,
      taxonomy_recipe_version: null,
      qc_policy_version: null,
      support_id: "grid-0.25",
      cells: [wireCell],
      truncated: false,
      next_cursor: null,
      counts: { returned: 1, matched: 1 },
    });

    const result = await getBotanicalOccurrences({ bbox: "-125,42,-111,49", zoom: 6 });

    expect(result.state).toBe("aggregate");
    if (result.state !== "aggregate") throw new Error("expected aggregate");
    expect(result.supportId).toBe("grid-0.25");
    expect(result.cells).toHaveLength(1);
    expect(result.cells[0]).toMatchObject({ cellId: "grid-0.25:12:34", recordCount: 40 });
  });

  it("returns a refused answer as a union member, not a throw", async () => {
    mockedFetch.mockResolvedValueOnce({
      product: "botanical-occurrences",
      state: "current",
      release_set_id: "ubc-v16.43",
    });
    mockedFetch.mockResolvedValueOnce({
      product: "botanical-occurrences",
      state: "refused",
      reason: "bbox_too_large_for_zoom",
      detail: "a detail answer is bounded at 4.0 square degrees",
      note: "This is a refusal, not an absence. Nothing was read, so nothing follows about the collection.",
    });

    const result = await getBotanicalOccurrences({ bbox: "-140,20,-60,60", zoom: 13 });

    expect(result).toEqual({
      state: "refused",
      reason: "bbox_too_large_for_zoom",
      detail: "a detail answer is bounded at 4.0 square degrees",
      note: "This is a refusal, not an absence. Nothing was read, so nothing follows about the collection.",
    });
  });

  it("throws BotanicalOccurrencesContractError when /query breaks the four-state contract", async () => {
    mockedFetch.mockResolvedValueOnce({
      product: "botanical-occurrences",
      state: "current",
      release_set_id: "ubc-v16.43",
    });
    mockedFetch.mockResolvedValueOnce({ state: "conflict", release_set_id: "ubc-v16.43" });

    await expect(
      getBotanicalOccurrences({ bbox: "-124,48,-122,50", zoom: 13 })
    ).rejects.toBeInstanceOf(BotanicalOccurrencesContractError);
  });
});
