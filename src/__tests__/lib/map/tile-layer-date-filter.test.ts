import { describe, expect, it } from "vitest";
import { featureFilter } from "@maplibre/maplibre-gl-style-spec";
import {
  DATE_FILTERABLE_TILE_LAYER_TOGGLE_IDS,
  dateFilterableStyleLayerIds,
  tileLayerDateFilter,
} from "@/lib/map/tile-layer-date-filter";
import { LAYER_REGISTRY } from "@/lib/map/layer-registry";

/**
 * The filter is evaluated with MapLibre's OWN expression compiler, not re-implemented here.
 * A hand-rolled check would pass on an expression MapLibre rejects at style-parse time, and a
 * rejected filter does not throw at runtime -- it drops, silently restoring the undated
 * behaviour this whole change exists to remove.
 */
function drawsFeature(filter: unknown, properties: Record<string, unknown>): boolean {
  const compiled = featureFilter(filter as never);
  expect(compiled.needGeometry).toBe(false);
  return compiled.filter(
    { zoom: 8 } as never,
    { type: 3, properties } as never,
    undefined as never
  );
}

describe("tileLayerDateFilter", () => {
  it("compiles as a MapLibre expression rather than merely looking like one", () => {
    // The failure this guards is silent: MapLibre reports an invalid filter by ignoring it.
    expect(() => featureFilter(tileLayerDateFilter("2024-03-01") as never)).not.toThrow();
  });

  it("keeps a feature observed on or before the selected day", () => {
    const filter = tileLayerDateFilter("2024-03-01");
    expect(drawsFeature(filter, { observed_day: "2024-03-01" })).toBe(true);
    expect(drawsFeature(filter, { observed_day: "2020-11-24" })).toBe(true);
  });

  it("hides a feature the record had not observed yet at that day", () => {
    // The bug in one line: a 2026 fire perimeter was drawn over a 2023 map with nothing
    // saying so, because no part of the style-baked tile path took a date at all.
    expect(drawsFeature(tileLayerDateFilter("2024-03-01"), { observed_day: "2026-08-06" })).toBe(
      false
    );
  });

  it("keeps a feature the warehouse could not date", () => {
    // geo.feature_observation_day returns NULL for an unparseable upstream timestamp, and
    // ST_AsMVT then omits the attribute. A row that cannot be dated has not been dated as
    // future, so dropping it would delete data from the map on a parse failure -- which is
    // the state of 13 of the 119 published fire-perimeter rows in production today.
    expect(drawsFeature(tileLayerDateFilter("2024-03-01"), { fire_name: "Cedar Creek" })).toBe(
      true
    );
  });

  it("compares ISO dates as strings, which is why no date parsing enters the style", () => {
    // Lexicographic order and calendar order agree for YYYY-MM-DD, and only for that shape.
    const filter = tileLayerDateFilter("2024-09-05");
    expect(drawsFeature(filter, { observed_day: "2024-09-04" })).toBe(true);
    expect(drawsFeature(filter, { observed_day: "2024-09-06" })).toBe(false);
    // Across a month and a year boundary, where a naive numeric compare would break.
    expect(drawsFeature(filter, { observed_day: "2024-10-01" })).toBe(false);
    expect(drawsFeature(filter, { observed_day: "2023-12-31" })).toBe(true);
  });

  it("clears the filter when there is no day, rather than filtering on a guess", () => {
    // Null means capabilities have not landed. Nothing knows what "today" is yet, and the
    // browser clock is exactly the source serverCurrentDate exists to keep out.
    expect(tileLayerDateFilter(null)).toBeNull();
  });
});

describe("dateFilterableStyleLayerIds", () => {
  it("covers every style layer of every date-filterable toggle", () => {
    const ids = dateFilterableStyleLayerIds();
    for (const toggleId of DATE_FILTERABLE_TILE_LAYER_TOGGLE_IDS) {
      for (const layerId of LAYER_REGISTRY[toggleId].styleLayerIds) {
        // An outline layer left out would keep drawing the boundary of a feature whose fill
        // the filter had just removed.
        expect(ids).toContain(layerId);
      }
    }
    expect(ids.length).toBeGreaterThan(DATE_FILTERABLE_TILE_LAYER_TOGGLE_IDS.length);
  });

  it("lists only toggles whose tile function emits observed_day", () => {
    // interventions is the deliberate omission: geo.intervention_tiles was left untouched by
    // migration 0015 because the layer has 2 rows, both unpublished, and neither carries any
    // of the three timestamp keys -- there is no day to filter on. Adding it to this list
    // without the migration would filter on a missing attribute and hide the layer outright.
    expect(DATE_FILTERABLE_TILE_LAYER_TOGGLE_IDS).not.toContain("interventions");
    expect(DATE_FILTERABLE_TILE_LAYER_TOGGLE_IDS).not.toContain("building-footprints");
  });
});
