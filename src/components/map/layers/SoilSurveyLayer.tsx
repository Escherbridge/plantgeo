"use client";

import { useCallback, useEffect, useRef } from "react";
import type { Map as MapLibreMap, GeoJSONSource } from "maplibre-gl";
import { getFirstSymbolLayer, safeRemoveLayerAndSource } from "@/lib/map/layer-utils";
import {
  SOIL_SURVEY_SOURCE,
  soilSurveyLayer,
  soilSurveyOutlineLayer,
  soilSurveySummaryLayer,
} from "@/lib/map/layers";
import { useStyleReady } from "@/components/map/layers/use-style-ready";
import { authoredPaintValue } from "@/lib/map/layer-opacity";

const SOIL_SURVEY_LAYER_IDS = [
  soilSurveyLayer.id,
  soilSurveyOutlineLayer.id,
  soilSurveySummaryLayer.id,
];

/**
 * The authored strengths, read off the shared specs rather than restated -- a palette edit in
 * layers.ts reaches the multiplier without a second hand-kept copy.
 *
 * They are read here, never written: `soilSurveyLayer` and `soilSurveyOutlineLayer` are
 * module singletons that `addLayer` receives by reference, so mutating their `paint` would
 * change the authored base for every later add and make the multiplier compound.
 */
const SURVEY_FILL_OPACITY = authoredPaintValue(soilSurveyLayer, "fill-opacity") as number;
const SURVEY_OUTLINE_OPACITY = authoredPaintValue(
  soilSurveyOutlineLayer,
  "line-opacity"
) as number;
const SUMMARY_FILL_OPACITY = authoredPaintValue(
  soilSurveySummaryLayer,
  "circle-opacity"
) as number;
const SUMMARY_STROKE_OPACITY = authoredPaintValue(
  soilSurveySummaryLayer,
  "circle-stroke-opacity"
) as number;

interface SoilSurveyLayerProps {
  map: MapLibreMap | null;
  /** SSURGO map units for the viewport, from environmental.getSoilSurvey. */
  geojson: GeoJSON.FeatureCollection | null;
  visible?: boolean;
  /** The reader's MULTIPLIER over every authored strength. See src/lib/map/layer-opacity.ts. */
  opacityScale?: number;
}

/**
 * Draws USDA SSURGO map units for the viewport, served by `environmental.getSoilSurvey`.
 * Distinct from `SoilLayer`, which paints the SoilGrids raster: this one is the vector
 * survey.
 *
 * Three style layers over ONE source, because the server answers a viewport with one of
 * three shapes and each needs its own geometry type: real delineations and drainage-class
 * unions are polygons (the fill + outline pair), and a viewport too wide to union honestly
 * is a lattice of counted points (the summary circles). The circle layer's own `summary`
 * filter is what keeps them apart, so nothing here has to branch on which tier answered.
 *
 * Paint and ids come from `src/lib/map/layers.ts` rather than being restated here, so the
 * fill's `drainageClass` arms, the outline, and the hover formatter keyed on
 * "soil-survey-fill" cannot drift apart. Readiness follows the `useStyleReady` pattern
 * required by `src/components/map/AGENTS.md` -- never `map.once(...)`, which the diff path
 * of `setStyle()` can fire past before this component ever registers.
 */
export function SoilSurveyLayer({
  map,
  geojson,
  visible = true,
  opacityScale = 1,
}: SoilSurveyLayerProps) {
  const fillOpacity = SURVEY_FILL_OPACITY * opacityScale;
  const outlineOpacity = SURVEY_OUTLINE_OPACITY * opacityScale;
  const summaryFillOpacity = SUMMARY_FILL_OPACITY * opacityScale;
  const summaryStrokeOpacity = SUMMARY_STROKE_OPACITY * opacityScale;

  // The style.load handler must read the newest data without re-registering, or it drops
  // to the back of the listener queue on every viewport query. See AGENTS.md. Synced in an
  // effect rather than assigned during render (as WaterLayer and DroughtLayer still do):
  // both run before any style event can fire, and only this form is free of
  // react-hooks/refs.
  const propsRef = useRef({
    geojson,
    visible,
    fillOpacity,
    outlineOpacity,
    summaryFillOpacity,
    summaryStrokeOpacity,
  });
  useEffect(() => {
    propsRef.current = {
      geojson,
      visible,
      fillOpacity,
      outlineOpacity,
      summaryFillOpacity,
      summaryStrokeOpacity,
    };
  }, [
    geojson,
    visible,
    fillOpacity,
    outlineOpacity,
    summaryFillOpacity,
    summaryStrokeOpacity,
  ]);

  // Idempotent: every add is guarded, so the two effects below may both call it.
  const addLayers = useCallback((m: MapLibreMap) => {
    const {
      geojson,
      fillOpacity,
      outlineOpacity,
      summaryFillOpacity,
      summaryStrokeOpacity,
    } = propsRef.current;
    if (!geojson) return;

    const beforeId = getFirstSymbolLayer(m);

    if (!m.getSource(SOIL_SURVEY_SOURCE)) {
      m.addSource(SOIL_SURVEY_SOURCE, { type: "geojson", data: geojson });
    } else {
      (m.getSource(SOIL_SURVEY_SOURCE) as GeoJSONSource).setData(geojson);
    }

    // The shared spec goes in unmodified and the multiplier is applied afterwards, rather
    // than spreading a patched copy in: `addLayer` keeps a reference to what it is given, and
    // a spread would also fork the paint away from layers.ts on every future edit.
    if (!m.getLayer(soilSurveyLayer.id)) {
      m.addLayer(soilSurveyLayer, beforeId);
      m.setPaintProperty(soilSurveyLayer.id, "fill-opacity", fillOpacity);
    }
    if (!m.getLayer(soilSurveyOutlineLayer.id)) {
      m.addLayer(soilSurveyOutlineLayer, beforeId);
      m.setPaintProperty(soilSurveyOutlineLayer.id, "line-opacity", outlineOpacity);
    }
    // Last, so the lattice dots sit over the polygon pair. In practice a response is all
    // one tier or all the other, so they never actually overlap -- the ordering is what
    // makes that assumption unnecessary.
    if (!m.getLayer(soilSurveySummaryLayer.id)) {
      m.addLayer(soilSurveySummaryLayer, beforeId);
      m.setPaintProperty(soilSurveySummaryLayer.id, "circle-opacity", summaryFillOpacity);
      m.setPaintProperty(
        soilSurveySummaryLayer.id,
        "circle-stroke-opacity",
        summaryStrokeOpacity
      );
    }
  }, []);

  const removeLayers = useCallback((m: MapLibreMap) => {
    safeRemoveLayerAndSource(m, SOIL_SURVEY_LAYER_IDS, SOIL_SURVEY_SOURCE);
  }, []);

  // Persistent listener: survives every future basemap swap, which wipes custom layers.
  useEffect(() => {
    if (!map) return;

    if (!visible) {
      removeLayers(map);
      return;
    }

    const onStyleLoad = () => {
      if (!propsRef.current.visible) return;
      addLayers(map);
    };
    map.on("style.load", onStyleLoad);

    return () => {
      map.off("style.load", onStyleLoad);
      removeLayers(map);
    };
  }, [map, visible, addLayers, removeLayers]);

  // Covers the mount-time race the persistent listener cannot: a style that already
  // finished loading before this component mounted fires no further "style.load".
  // styleReady only forces a re-run; the gate re-reads the live map so it is never stale.
  const styleReady = useStyleReady(map);
  useEffect(() => {
    if (!map || !visible || !map.isStyleLoaded()) return;
    addLayers(map);
  }, [map, visible, addLayers, styleReady]);

  // A new viewport response updates the existing source rather than rebuilding layers.
  // An empty collection is pushed like any other: the provider reporting no map units
  // here must look like an empty layer, not like a stale one.
  useEffect(() => {
    if (!map || !visible || !geojson) return;
    try {
      if (!map.getStyle()) return;
      const source = map.getSource(SOIL_SURVEY_SOURCE) as GeoJSONSource | undefined;
      if (source) {
        source.setData(geojson);
        return;
      }
      // No source yet: the first viewport response routinely lands after the readiness
      // effect already ran with nothing to draw, and no further style event is owed.
      // addLayers is idempotent, so attaching it here is safe.
      addLayers(map);
    } catch {
      // Style torn down mid-swap; the persistent style.load listener re-adds from the ref.
    }
  }, [map, geojson, visible, addLayers]);

  // The multiplier, applied without a rebuild. Separate from the viewport effect above so a
  // pan does not rewrite the paint the reader just set, and vice versa.
  useEffect(() => {
    if (!map || !visible) return;
    try {
      if (!map.getStyle()) return;
    } catch {
      return;
    }
    if (map.getLayer(soilSurveyLayer.id)) {
      map.setPaintProperty(soilSurveyLayer.id, "fill-opacity", fillOpacity);
    }
    if (map.getLayer(soilSurveyOutlineLayer.id)) {
      map.setPaintProperty(soilSurveyOutlineLayer.id, "line-opacity", outlineOpacity);
    }
    if (map.getLayer(soilSurveySummaryLayer.id)) {
      map.setPaintProperty(soilSurveySummaryLayer.id, "circle-opacity", summaryFillOpacity);
      map.setPaintProperty(
        soilSurveySummaryLayer.id,
        "circle-stroke-opacity",
        summaryStrokeOpacity
      );
    }
  }, [map, visible, fillOpacity, outlineOpacity, summaryFillOpacity, summaryStrokeOpacity]);

  return null;
}
