"use client";

import { useCallback, useEffect, useRef } from "react";
import type { Map as MapLibreMap, MapLayerMouseEvent, GeoJSONSource } from "maplibre-gl";
import { getFirstSymbolLayer, safeRemoveLayerAndSource } from "@/lib/map/layer-utils";
import {
  LAND_CONTEXT_GROUP_IDS,
  useLandContextStore,
  type LandContextFeature,
  type LandContextGroupId,
} from "@/stores/land-context-store";
import { WideAreaSelectionAction } from "@/components/map/land-context/mobile/WideAreaSelectionAction";

/**
 * Native MapLibre GL rendering for the four land-context groups.
 *
 * This repo's convention for a selectable/hoverable overlay is `map.addSource`
 * + `map.addLayer` re-registered on `style.load` (see `ServiceAreaLayer.tsx`),
 * not a second deck.gl instance -- deck.gl is reserved elsewhere in this
 * codebase for aggregation/scalar-field rendering (see
 * `src/components/map/AGENTS.md` "Why not deck.gl" for the dependency
 * boundary). An earlier draft of this component built deck.gl layers
 * directly; that risked two overlays fighting over one WebGL context and has
 * been replaced with this native implementation to match `ServiceAreaLayer`.
 *
 * One shared GeoJSON source holds every result from the store; each group
 * gets its own fill+line layer pair filtered by `properties.group`, so
 * toggling a group is a `setLayoutProperty("visibility", ...)` call, never a
 * re-fetch.
 */
const SOURCE_ID = "land-context-results";

const GROUP_COLORS: Record<LandContextGroupId, string> = {
  "parcels-land-use": "#f59e0b",
  "electric-utility-territories": "#38bdf8",
  "blm-lands": "#16a34a",
  "state-managed-lands": "#a855f7",
};

function fillLayerId(group: LandContextGroupId): string {
  return `land-context-fill-${group}`;
}
function lineLayerId(group: LandContextGroupId): string {
  return `land-context-line-${group}`;
}

function toFeatureCollection(features: LandContextFeature[]): GeoJSON.FeatureCollection {
  return {
    type: "FeatureCollection",
    features: features.map((feature) => ({
      type: "Feature",
      id: feature.id,
      properties: {
        featureId: feature.id,
        group: feature.group,
        title: feature.title,
        category: feature.category ?? null,
        sourceVintage: feature.sourceVintage ?? null,
        contactRouteSummary: feature.contactRouteSummary ?? null,
      },
      geometry: feature.geometry,
    })),
  };
}

const ALL_LAYER_IDS = LAND_CONTEXT_GROUP_IDS.flatMap((group) => [fillLayerId(group), lineLayerId(group)]);

interface LandContextLayerProps {
  map: MapLibreMap | null;
}

export function LandContextLayer({ map }: LandContextLayerProps) {
  const results = useLandContextStore((state) => state.results);
  const enabledGroups = useLandContextStore((state) => state.enabledGroups);
  const setHoveredFeature = useLandContextStore((state) => state.setHoveredFeature);
  const setSelection = useLandContextStore((state) => state.setSelection);
  const setCandidateIndex = useLandContextStore((state) => state.setCandidateIndex);
  const openPanel = useLandContextStore((state) => state.openPanel);

  const resultsRef = useRef(results);
  const enabledGroupsRef = useRef(enabledGroups);

  useEffect(() => {
    resultsRef.current = results;
  }, [results]);
  useEffect(() => {
    enabledGroupsRef.current = enabledGroups;
  }, [enabledGroups]);

  const addAllLayers = useCallback((m: MapLibreMap) => {
    const data = toFeatureCollection(resultsRef.current);
    const beforeId = getFirstSymbolLayer(m);

    if (!m.getSource(SOURCE_ID)) {
      m.addSource(SOURCE_ID, { type: "geojson", data, promoteId: "featureId" });
    } else {
      (m.getSource(SOURCE_ID) as GeoJSONSource).setData(data);
    }

    for (const group of LAND_CONTEXT_GROUP_IDS) {
      const color = GROUP_COLORS[group];
      const visible = enabledGroupsRef.current[group];

      if (!m.getLayer(fillLayerId(group))) {
        m.addLayer(
          {
            id: fillLayerId(group),
            type: "fill",
            source: SOURCE_ID,
            filter: ["==", ["get", "group"], group],
            layout: { visibility: visible ? "visible" : "none" },
            paint: {
              "fill-color": color,
              "fill-opacity": [
                "case",
                ["boolean", ["feature-state", "selected"], false],
                0.55,
                ["boolean", ["feature-state", "hover"], false],
                0.4,
                0.2,
              ],
            },
          },
          beforeId
        );
      }

      if (!m.getLayer(lineLayerId(group))) {
        m.addLayer(
          {
            id: lineLayerId(group),
            type: "line",
            source: SOURCE_ID,
            filter: ["==", ["get", "group"], group],
            layout: { visibility: visible ? "visible" : "none" },
            paint: {
              "line-color": color,
              "line-width": ["case", ["boolean", ["feature-state", "selected"], false], 3, 1.5],
            },
          },
          beforeId
        );
      }
    }
  }, []);

  const removeAllLayers = useCallback((m: MapLibreMap) => {
    safeRemoveLayerAndSource(m, ALL_LAYER_IDS, SOURCE_ID);
  }, []);

  // Re-register across style switches, which wipe custom layers/sources.
  useEffect(() => {
    if (!map) return;
    const onStyleLoad = () => addAllLayers(map);
    map.on("style.load", onStyleLoad);
    if (map.isStyleLoaded()) addAllLayers(map);
    return () => {
      map.off("style.load", onStyleLoad);
      removeAllLayers(map);
    };
  }, [map, addAllLayers, removeAllLayers]);

  // Keep the shared source in sync with the store's current results.
  useEffect(() => {
    if (!map || !map.isStyleLoaded()) return;
    const source = map.getSource(SOURCE_ID) as GeoJSONSource | undefined;
    if (source) source.setData(toFeatureCollection(results));
  }, [map, results]);

  // Toggle visibility per group without touching the source/data.
  useEffect(() => {
    if (!map || !map.isStyleLoaded()) return;
    for (const group of LAND_CONTEXT_GROUP_IDS) {
      const visibility = enabledGroups[group] ? "visible" : "none";
      if (map.getLayer(fillLayerId(group))) map.setLayoutProperty(fillLayerId(group), "visibility", visibility);
      if (map.getLayer(lineLayerId(group))) map.setLayoutProperty(lineLayerId(group), "visibility", visibility);
    }
  }, [map, enabledGroups]);

  // Hover: concise identity card, keyed by the same `LandContextFeature` the accessible list uses.
  useEffect(() => {
    if (!map) return;

    const findFeature = (featureId: string): LandContextFeature | null =>
      resultsRef.current.find((f) => f.id === featureId) ?? null;

    const onMouseMove = (event: MapLayerMouseEvent) => {
      const hit = event.features?.[0];
      if (!hit || typeof hit.properties?.featureId !== "string") {
        setHoveredFeature(null);
        return;
      }
      const feature = findFeature(hit.properties.featureId);
      setHoveredFeature(feature, { x: event.point.x, y: event.point.y });
    };

    const onMouseLeave = () => setHoveredFeature(null);

    const onClick = (event: MapLayerMouseEvent) => {
      const hit = event.features?.[0];
      if (!hit || typeof hit.properties?.featureId !== "string") return;
      const index = resultsRef.current.findIndex((f) => f.id === hit.properties?.featureId);
      if (index === -1) return;
      setSelection({ mode: "point", point: [event.lngLat.lng, event.lngLat.lat] });
      setCandidateIndex(index);
      openPanel();
    };

    const interactiveLayers = ALL_LAYER_IDS.filter((id) => id.startsWith("land-context-fill-"));
    for (const layerId of interactiveLayers) {
      map.on("mousemove", layerId, onMouseMove);
      map.on("mouseleave", layerId, onMouseLeave);
      map.on("click", layerId, onClick);
    }

    return () => {
      for (const layerId of interactiveLayers) {
        map.off("mousemove", layerId, onMouseMove);
        map.off("mouseleave", layerId, onMouseLeave);
        map.off("click", layerId, onClick);
      }
    };
  }, [map, setHoveredFeature, setSelection, setCandidateIndex, openPanel]);

  // Mount point only: the accessible area-selection alternative renders its own affordance
  // (visible whenever a point selection exists) but owns no map click/hover wiring of its own --
  // see `WideAreaSelectionAction.tsx` for the interaction-pattern rationale.
  return <WideAreaSelectionAction />;
}
