"use client";

/**
 * The SSURGO soil-survey read and the three ERA5-Land soil-field reads (moisture, temperature,
 * VPD), each with the collection its layer draws, plus the painted-depth publication for the hover
 * caption. See src/components/map/AGENTS.md §LayerManager structure.
 */

import type {
  DebouncedMapDay,
  LayerVisibility,
  SoilDisplayMode,
} from "@/lib/map/layer-toggle-context";
import { usePaintedSoilFieldDepth } from "@/hooks/usePaintedSoilFieldDepth";
import { useSoilFieldQuery, useSoilSurveyQuery } from "@/hooks/useViewportProxiedLayers";
import type { ParquetReadViewport } from "@/components/map/layer-manager/useParquetLayerReads";

const EMPTY_FEATURE_COLLECTION: GeoJSON.FeatureCollection = {
  type: "FeatureCollection",
  features: [],
};

export function useSoilLayerReads(
  layerVisibility: LayerVisibility,
  { bbox, zoom }: ParquetReadViewport,
  soilMode: SoilDisplayMode,
  {
    soilMoistureDay,
    soilTemperatureDay,
    soilVpdDay,
  }: {
    soilMoistureDay: DebouncedMapDay;
    soilTemperatureDay: DebouncedMapDay;
    soilVpdDay: DebouncedMapDay;
  }
) {
  // SSURGO map units are proxied live from USDA per viewport rather than published to the
  // warehouse, so they carry no slider day: the endpoint answers for a bbox alone. It is a
  // polygon feed an order of magnitude heavier than the point layers above, which is why it
  // is not fetched unless its own toggle is on. Key, fallback bbox, staleTime and retry live
  // in the shared hook, which SoilDetails calls too -- see src/lib/server/AGENTS.md
  // §proxied-viewport-queries. (HUC12 watersheds used to sit here; they are now style-baked
  // Martin tiles and reach the map through applyVisibility below, like every other tile layer.)
  const soilSurveyVisible = layerVisibility["soil-survey"];
  // `zoom` is what selects the survey's render granularity server-side. Omitting it -- which
  // both call sites did until now -- resolves to the detail tier, whose 0.02 sq-deg ceiling
  // the tRPC input then rejects at any ordinary zoom, so the layer only ever drew when zoomed
  // in past ~13. Passed from the same `useViewportBounds()` derivation SoilDetails reads it
  // from, so the map and the details region stay on ONE react-query entry.
  const soilSurveyRead = useSoilSurveyQuery(bbox, { enabled: soilSurveyVisible, zoom });
  // Only the features are drawable: a truncated view and an upstream fault both reach the
  // map as polygons that stop, so the collection's truncated/availability pair is read by
  // SoilDetails instead, from this same query key. See src/lib/server/AGENTS.md §soil-survey.
  const soilSurveyGeoJSON = soilSurveyRead.answer ?? EMPTY_FEATURE_COLLECTION;

  // The three ERA5-Land soil fields. `zoom` is not a hint here -- it selects the server-side
  // aggregation tier, so zooming out makes the answer SMALLER (isobands over a coarse
  // lattice) rather than shipping 1,568 squares. Each takes ITS OWN row's settled day, like
  // every other warehouse-backed feed; the depth is the panel's, and neither is a second
  // time control. staleTime matches vegetation's: a reanalysis archive day is immutable.
  //
  // Three calls rather than a loop: hooks cannot be called from one, and `measure` is in the
  // query key, so each field holds a separate cache entry and any subset can be on at once.
  // Three separate days for the same reason -- they are three toggles, and a reader who scrubs
  // moisture back a week has said nothing about temperature.
  const soilMoistureVisible = layerVisibility["soil-moisture"];
  const soilMoistureRead = useSoilFieldQuery(bbox, {
    enabled: soilMoistureVisible,
    measure: "moisture",
    date: soilMoistureDay.requestDate,
    depth: soilMode.fieldDepth.moisture,
    zoom,
  });
  const soilMoistureGeoJSON: GeoJSON.FeatureCollection =
    soilMoistureRead.answer ?? EMPTY_FEATURE_COLLECTION;

  const soilTemperatureVisible = layerVisibility["soil-temperature"];
  const soilTemperatureRead = useSoilFieldQuery(bbox, {
    enabled: soilTemperatureVisible,
    measure: "temperature",
    date: soilTemperatureDay.requestDate,
    depth: soilMode.fieldDepth.temperature,
    zoom,
  });
  const soilTemperatureGeoJSON: GeoJSON.FeatureCollection =
    soilTemperatureRead.answer ?? EMPTY_FEATURE_COLLECTION;

  const soilVpdVisible = layerVisibility["soil-vpd"];
  const soilVpdRead = useSoilFieldQuery(bbox, {
    enabled: soilVpdVisible,
    measure: "vpd",
    date: soilVpdDay.requestDate,
    depth: soilMode.fieldDepth.vpd,
    zoom,
  });
  const soilVpdGeoJSON: GeoJSON.FeatureCollection =
    soilVpdRead.answer ?? EMPTY_FEATURE_COLLECTION;
  // Which depth each field's drawn answer IS, for the hover caption (AGENTS.md §soil-painted-depth).
  usePaintedSoilFieldDepth("moisture", soilMoistureRead, soilMode.fieldDepth.moisture);
  usePaintedSoilFieldDepth("temperature", soilTemperatureRead, soilMode.fieldDepth.temperature);
  usePaintedSoilFieldDepth("vpd", soilVpdRead, soilMode.fieldDepth.vpd);

  return {
    soilSurveyVisible,
    soilSurveyRead,
    soilSurveyGeoJSON,
    soilMoistureVisible,
    soilMoistureRead,
    soilMoistureGeoJSON,
    soilTemperatureVisible,
    soilTemperatureRead,
    soilTemperatureGeoJSON,
    soilVpdVisible,
    soilVpdRead,
    soilVpdGeoJSON,
  };
}

export type SoilLayerReads = ReturnType<typeof useSoilLayerReads>;
