"use client";

import { useCallback, useEffect, useRef } from "react";
import type { Map as MapLibreMap, GeoJSONSource } from "maplibre-gl";
import { getFirstSymbolLayer, safeRemoveLayerAndSource } from "@/lib/map/layer-utils";
import { useStyleReady } from "@/components/map/layers/use-style-ready";
import { LAND_CONTEXT_GROUP_IDS, type LandContextGroupId } from "@/stores/land-context-store";

/**
 * Draws the boundaries the AUTOMATIC land-context viewport read returned.
 *
 * A second source, never the click lane's. `LandContextLayer` owns `land-context-results` and
 * `useLandContextStore.setResults` is documented as that store slice's only writer, so publishing
 * pan-driven boundaries into it would make a pan silently replace the reader's clicked selection.
 * This component therefore holds its own source and its own two layers, sits BENEATH the click
 * lane's (it is added before the first symbol layer like every other overlay, and the click lane
 * adds its own later), and binds no interaction at all: hover, click and the identity card stay
 * the click lane's, so a reader still selects exactly what they selected before.
 *
 * Group colours are the click lane's, restated as a MapLibre `match` expression over
 * `properties.group` rather than imported, because that module keeps them as a private record and
 * exporting them would invite a third lane to fork them. See `src/components/map/AGENTS.md`
 * section "The land-context viewport lane".
 */
const SOURCE_ID = "land-context-viewport";
const FILL_LAYER_ID = "land-context-viewport-fill";
const LINE_LAYER_ID = "land-context-viewport-line";
const LAYER_IDS = [FILL_LAYER_ID, LINE_LAYER_ID];

const GROUP_COLORS: Readonly<Record<LandContextGroupId, string>> = {
  "parcels-land-use": "#f59e0b",
  "electric-utility-territories": "#38bdf8",
  "blm-lands": "#16a34a",
  "state-managed-lands": "#a855f7",
};

const FALLBACK_COLOR = "#94a3b8";

/** `["match", ["get","group"], <group>, <color>, …, fallback]`, built from the one colour table. */
function groupColorExpression(): unknown[] {
  return [
    "match",
    ["get", "group"],
    ...LAND_CONTEXT_GROUP_IDS.flatMap((group) => [group, GROUP_COLORS[group]]),
    FALLBACK_COLOR,
  ];
}

/** Authored strengths; the reader's multiplier is folded in by the caller's `opacityScale`. */
const AUTHORED_FILL_OPACITY = 0.18;
const AUTHORED_LINE_OPACITY = 0.7;

interface LandContextViewportLayerProps {
  map: MapLibreMap | null;
  /** Boundaries for the current viewport, or null when the lane drew nothing. */
  geojson: GeoJSON.FeatureCollection | null;
  visible?: boolean;
  /** The reader's MULTIPLIER over every authored strength. See src/lib/map/layer-opacity.ts. */
  opacityScale?: number;
}

export function LandContextViewportLayer({
  map,
  geojson,
  visible = true,
  opacityScale = 1,
}: LandContextViewportLayerProps) {
  const fillOpacity = AUTHORED_FILL_OPACITY * opacityScale;
  const lineOpacity = AUTHORED_LINE_OPACITY * opacityScale;

  // The style.load handler must read the newest data without re-registering, or it drops to the
  // back of the listener queue on every pan. See src/components/map/AGENTS.md.
  const propsRef = useRef({ geojson, visible, fillOpacity, lineOpacity });
  useEffect(() => {
    propsRef.current = { geojson, visible, fillOpacity, lineOpacity };
  }, [geojson, visible, fillOpacity, lineOpacity]);

  // Idempotent: every add is guarded, so both effects below may call it.
  const addLayers = useCallback((m: MapLibreMap) => {
    const current = propsRef.current;
    if (!current.geojson) return;

    const beforeId = getFirstSymbolLayer(m);

    if (!m.getSource(SOURCE_ID)) {
      m.addSource(SOURCE_ID, { type: "geojson", data: current.geojson, promoteId: "featureId" });
    } else {
      (m.getSource(SOURCE_ID) as GeoJSONSource).setData(current.geojson);
    }

    if (!m.getLayer(FILL_LAYER_ID)) {
      m.addLayer(
        {
          id: FILL_LAYER_ID,
          type: "fill",
          source: SOURCE_ID,
          paint: {
            "fill-color": groupColorExpression() as never,
            "fill-opacity": current.fillOpacity,
          },
        },
        beforeId
      );
    }
    if (!m.getLayer(LINE_LAYER_ID)) {
      m.addLayer(
        {
          id: LINE_LAYER_ID,
          type: "line",
          source: SOURCE_ID,
          paint: {
            "line-color": groupColorExpression() as never,
            "line-width": 1,
            "line-opacity": current.lineOpacity,
          },
        },
        beforeId
      );
    }
  }, []);

  const removeLayers = useCallback((m: MapLibreMap) => {
    safeRemoveLayerAndSource(m, LAYER_IDS, SOURCE_ID);
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

  // Covers the mount-time race the persistent listener cannot: a style that already finished
  // loading before this component mounted fires no further "style.load".
  const styleReady = useStyleReady(map);
  useEffect(() => {
    if (!map || !visible || !map.isStyleLoaded()) return;
    addLayers(map);
  }, [map, visible, addLayers, styleReady]);

  // A new viewport answer updates the source rather than rebuilding the layers. A null collection
  // takes the layers DOWN rather than retaining the previous view's boundaries: this lane follows
  // the camera, so a retained frame would draw one viewport's ownership over another's ground.
  useEffect(() => {
    if (!map || !visible) return;
    if (!geojson) {
      removeLayers(map);
      return;
    }
    try {
      if (!map.getStyle()) return;
      const source = map.getSource(SOURCE_ID) as GeoJSONSource | undefined;
      if (source) {
        source.setData(geojson);
        return;
      }
      addLayers(map);
    } catch {
      // Style torn down mid-swap; the persistent style.load listener re-adds from the ref.
    }
  }, [map, geojson, visible, addLayers, removeLayers]);

  // The multiplier, applied without a rebuild.
  useEffect(() => {
    if (!map || !visible) return;
    try {
      if (!map.getStyle()) return;
    } catch {
      return;
    }
    if (map.getLayer(FILL_LAYER_ID)) {
      map.setPaintProperty(FILL_LAYER_ID, "fill-opacity", fillOpacity);
    }
    if (map.getLayer(LINE_LAYER_ID)) {
      map.setPaintProperty(LINE_LAYER_ID, "line-opacity", lineOpacity);
    }
  }, [map, visible, fillOpacity, lineOpacity]);

  return null;
}
