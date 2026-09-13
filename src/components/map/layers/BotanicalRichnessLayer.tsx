"use client";

import { useEffect, useRef, useCallback } from "react";
import type { Map as MapLibreMap, GeoJSONSource } from "maplibre-gl";
import { getFirstSymbolLayer, safeRemoveLayerAndSource } from "@/lib/map/layer-utils";
import type { BotanicalAggregateCell, BotanicalCellEvaluation } from "@/lib/botanical-occurrences";

const SOURCE_ID = "botanical-richness";
const FILL_LAYER_ID = "botanical-richness-fill";
const OUTLINE_LAYER_ID = "botanical-richness-outline";

/**
 * Colors for the four non-documented evaluation states plus the documented ramp's top swatch,
 * chosen for contrast against the dark basemap and to never rely on hue alone (the legend
 * below always pairs a swatch with its exact label text). `documented` itself is a sequential
 * ramp built from `documented_taxa`, not a flat color -- see `documentedFillColor`.
 */
const EVALUATION_COLORS: Record<Exclude<BotanicalCellEvaluation, "documented">, string> = {
  evaluated_zero: "#6b7280",
  outside_coverage: "transparent",
  withheld_or_generalized_only: "#b45309",
  not_evaluated: "#334155",
};

/**
 * Legend rows for the richness layer. Labels are the four EXACT strings the spec requires --
 * `LayerLegend`-style consumers and the test suite both key off this literal text, so a future
 * edit to these strings must be deliberate.
 */
export const BOTANICAL_RICHNESS_LEGEND = [
  { label: "documented taxa (sequential, darker = more)", color: "#0f6b3c" },
  { label: "zero documented records", color: EVALUATION_COLORS.evaluated_zero },
  { label: "outside admitted coverage", color: "#1f2937" },
  { label: "withheld/generalized only", color: EVALUATION_COLORS.withheld_or_generalized_only },
  { label: "not evaluated", color: EVALUATION_COLORS.not_evaluated },
] as const;

/** MapLibre paint expression: sequential ramp for documented cells, flat colors otherwise. */
function buildFillColorExpression(): unknown[] {
  return [
    "case",
    ["==", ["get", "evaluation"], "documented"],
    [
      "interpolate",
      ["linear"],
      ["get", "documented_taxa"],
      0, "#c7e9c0",
      5, "#74c476",
      15, "#238b45",
      40, "#00441b",
    ],
    ["==", ["get", "evaluation"], "evaluated_zero"],
    EVALUATION_COLORS.evaluated_zero,
    ["==", ["get", "evaluation"], "withheld_or_generalized_only"],
    EVALUATION_COLORS.withheld_or_generalized_only,
    ["==", ["get", "evaluation"], "not_evaluated"],
    EVALUATION_COLORS.not_evaluated,
    // outside_coverage and any unrecognized state: never drawn as if it were data.
    "transparent",
  ];
}

/** Converts wire aggregate cells into the FeatureCollection this layer draws. */
export function botanicalRichnessToGeoJSON(cells: BotanicalAggregateCell[]): GeoJSON.FeatureCollection {
  return {
    type: "FeatureCollection",
    features: cells.map((cell) => ({
      type: "Feature",
      geometry: cell.geometry,
      properties: {
        cell_id: cell.cell_id,
        evaluation: cell.evaluation,
        documented_taxa: cell.documented_taxa,
        record_count: cell.record_count,
        collection_count: cell.collection_count,
        excluded_by_qc: cell.excluded_by_qc,
      },
    })),
  };
}

interface BotanicalRichnessLayerProps {
  map: MapLibreMap | null;
  geojson: GeoJSON.FeatureCollection | null;
  /** Named so a caller must pass the pinned release rather than the layer inferring one. */
  releaseSetId: string | null;
  opacity?: number;
  visible?: boolean;
}

/**
 * Aggregate documented-taxon-richness choropleth for zoom bands below the detail threshold.
 * Follows `DroughtLayer`'s add/remove-on-style-load lifecycle. Tooltip content (documented
 * taxa, record_count, collection_count, excluded_by_qc, release_set_id) is read by the
 * consuming panel via `queryRenderedFeatures`, matching how other choropleth layers in this
 * directory expose hover data to their host component rather than rendering their own popup.
 */
export function BotanicalRichnessLayer({
  map,
  geojson,
  releaseSetId,
  opacity = 0.75,
  visible = true,
}: BotanicalRichnessLayerProps) {
  const propsRef = useRef({ geojson, visible, releaseSetId });
  propsRef.current = { geojson, visible, releaseSetId };

  const addLayers = useCallback((m: MapLibreMap) => {
    const { geojson } = propsRef.current;
    if (!geojson) return;
    const beforeId = getFirstSymbolLayer(m);

    if (!m.getSource(SOURCE_ID)) {
      m.addSource(SOURCE_ID, { type: "geojson", data: geojson });
    } else {
      (m.getSource(SOURCE_ID) as GeoJSONSource).setData(geojson);
    }

    if (!m.getLayer(FILL_LAYER_ID)) {
      m.addLayer(
        {
          id: FILL_LAYER_ID,
          type: "fill",
          source: SOURCE_ID,
          paint: {
            "fill-color": buildFillColorExpression() as never,
            "fill-opacity": opacity,
          },
        },
        beforeId
      );
    }
    if (!m.getLayer(OUTLINE_LAYER_ID)) {
      m.addLayer(
        {
          id: OUTLINE_LAYER_ID,
          type: "line",
          source: SOURCE_ID,
          paint: { "line-color": "#0b1e12", "line-width": 0.5, "line-opacity": 0.6 },
        },
        beforeId
      );
    }
  }, [opacity]);

  const removeLayers = useCallback((m: MapLibreMap) => {
    safeRemoveLayerAndSource(m, [FILL_LAYER_ID, OUTLINE_LAYER_ID], SOURCE_ID);
  }, []);

  useEffect(() => {
    if (!map) return;
    if (!visible || !geojson || !releaseSetId) {
      removeLayers(map);
      return;
    }

    const onStyleLoad = () => {
      const current = propsRef.current;
      if (!current.visible || !current.geojson || !current.releaseSetId) return;
      addLayers(map);
    };

    if (map.isStyleLoaded()) {
      addLayers(map);
    } else {
      map.once("style.load", () => addLayers(map));
    }
    map.on("style.load", onStyleLoad);

    return () => {
      map.off("style.load", onStyleLoad);
      removeLayers(map);
    };
  }, [map, geojson, visible, releaseSetId, addLayers, removeLayers]);

  useEffect(() => {
    if (!map || !visible) return;
    try {
      if (!map.getStyle()) return;
    } catch {
      return;
    }
    if (map.getLayer(FILL_LAYER_ID)) {
      map.setPaintProperty(FILL_LAYER_ID, "fill-opacity", opacity);
    }
  }, [map, opacity, visible]);

  return null;
}
