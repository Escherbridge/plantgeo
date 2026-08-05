"use client";

import { useCallback, useEffect, useRef } from "react";
import type { Map as MapLibreMap, GeoJSONSource } from "maplibre-gl";
import { getFirstSymbolLayer, safeRemoveLayerAndSource } from "@/lib/map/layer-utils";
import {
  SOIL_SURVEY_SOURCE,
  soilSurveyLayer,
  soilSurveyOutlineLayer,
} from "@/lib/map/layers";
import { useStyleReady } from "@/components/map/layers/use-style-ready";

const SOIL_SURVEY_LAYER_IDS = [soilSurveyLayer.id, soilSurveyOutlineLayer.id];

interface SoilSurveyLayerProps {
  map: MapLibreMap | null;
  /** SSURGO map units for the viewport, from environmental.getSoilSurvey. */
  geojson: GeoJSON.FeatureCollection | null;
  visible?: boolean;
}

/**
 * Draws USDA SSURGO map-unit polygons proxied per viewport through
 * `environmental.getSoilSurvey`. Distinct from `SoilLayer`, which paints the SoilGrids
 * raster: this one is the vector survey.
 *
 * Paint and ids come from `src/lib/map/layers.ts` rather than being restated here, so the
 * fill's `drainageClass` arms, the outline, and the hover formatter keyed on
 * "soil-survey-fill" cannot drift apart. Readiness follows the `useStyleReady` pattern
 * required by `src/components/map/AGENTS.md` -- never `map.once(...)`, which the diff path
 * of `setStyle()` can fire past before this component ever registers.
 */
export function SoilSurveyLayer({ map, geojson, visible = true }: SoilSurveyLayerProps) {
  // The style.load handler must read the newest data without re-registering, or it drops
  // to the back of the listener queue on every viewport query. See AGENTS.md. Synced in an
  // effect rather than assigned during render (as WaterLayer and DroughtLayer still do):
  // both run before any style event can fire, and only this form is free of
  // react-hooks/refs.
  const propsRef = useRef({ geojson, visible });
  useEffect(() => {
    propsRef.current = { geojson, visible };
  }, [geojson, visible]);

  // Idempotent: every add is guarded, so the two effects below may both call it.
  const addLayers = useCallback((m: MapLibreMap) => {
    const { geojson } = propsRef.current;
    if (!geojson) return;

    const beforeId = getFirstSymbolLayer(m);

    if (!m.getSource(SOIL_SURVEY_SOURCE)) {
      m.addSource(SOIL_SURVEY_SOURCE, { type: "geojson", data: geojson });
    } else {
      (m.getSource(SOIL_SURVEY_SOURCE) as GeoJSONSource).setData(geojson);
    }

    if (!m.getLayer(soilSurveyLayer.id)) {
      m.addLayer(soilSurveyLayer, beforeId);
    }
    if (!m.getLayer(soilSurveyOutlineLayer.id)) {
      m.addLayer(soilSurveyOutlineLayer, beforeId);
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

  return null;
}
