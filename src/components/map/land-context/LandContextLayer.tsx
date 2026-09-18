"use client";

import { useCallback, useEffect, useRef } from "react";
import type {
  Map as MapLibreMap,
  MapLayerMouseEvent,
  MapMouseEvent,
  GeoJSONSource,
  PointLike,
} from "maplibre-gl";
import { getFirstSymbolLayer, safeRemoveLayerAndSource } from "@/lib/map/layer-utils";
import {
  LAND_CONTEXT_GROUP_IDS,
  useLandContextStore,
  type LandContextFeature,
  type LandContextGroupId,
} from "@/stores/land-context-store";
import { WideAreaSelectionAction } from "@/components/map/land-context/mobile/WideAreaSelectionAction";
import { LandContextStatusNotice } from "@/components/map/land-context/LandContextStatusNotice";
import { isClickOwnedByAnotherSurface } from "@/components/map/land-context/click-ownership";

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
 *
 * Interaction is one bare `click` listener per map (see the click effect
 * below): features only draw once a selection exists, so the entry into a
 * selection cannot itself require a drawn feature.
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

const FILL_LAYER_IDS = LAND_CONTEXT_GROUP_IDS.map(fillLayerId);
const ALL_LAYER_IDS = LAND_CONTEXT_GROUP_IDS.flatMap((group) => [fillLayerId(group), lineLayerId(group)]);

function anyGroupEnabled(enabledGroups: Record<LandContextGroupId, boolean>): boolean {
  return LAND_CONTEXT_GROUP_IDS.some((group) => enabledGroups[group]);
}

/**
 * The land-context feature under a screen point, if a group fill layer is
 * present AND visible there (`visibility: none` excludes a layer from
 * `queryRenderedFeatures`, so a toggled-off group can never be hit). Only
 * layers that exist are queried: MapLibre answers a `layers` entry it cannot
 * find by firing an `error` event and returning nothing, and MapView's
 * `error` handler would report that as a basemap fault.
 */
export function pickRenderedLandContextFeatureId(map: MapLibreMap, point: PointLike): string | null {
  const presentLayers = FILL_LAYER_IDS.filter((id) => Boolean(map.getLayer(id)));
  if (presentLayers.length === 0) return null;
  const hit = map.queryRenderedFeatures(point, { layers: presentLayers })[0];
  const featureId = hit?.properties?.featureId;
  return typeof featureId === "string" ? featureId : null;
}

interface LandContextLayerProps {
  map: MapLibreMap | null;
}

export function LandContextLayer({ map }: LandContextLayerProps) {
  const results = useLandContextStore((state) => state.results);
  const enabledGroups = useLandContextStore((state) => state.enabledGroups);

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
  // Layer-delegated listeners, registered once per map; current results come from the store at
  // event time, so nothing changing can re-register them.
  useEffect(() => {
    if (!map) return;

    const onMouseMove = (event: MapLayerMouseEvent) => {
      const { results: current, setHoveredFeature } = useLandContextStore.getState();
      const hit = event.features?.[0];
      if (!hit || typeof hit.properties?.featureId !== "string") {
        setHoveredFeature(null);
        return;
      }
      const feature = current.find((f) => f.id === hit.properties?.featureId) ?? null;
      setHoveredFeature(feature, { x: event.point.x, y: event.point.y });
    };

    const onMouseLeave = () => useLandContextStore.getState().setHoveredFeature(null);

    for (const layerId of FILL_LAYER_IDS) {
      map.on("mousemove", layerId, onMouseMove);
      map.on("mouseleave", layerId, onMouseLeave);
    }

    return () => {
      for (const layerId of FILL_LAYER_IDS) {
        map.off("mousemove", layerId, onMouseMove);
        map.off("mouseleave", layerId, onMouseLeave);
      }
    };
  }, [map]);

  // Click: ONE bare map listener per map, registered once. Deps are `[map]`-shaped and every
  // changing value is read from the store at click time, so toggles/results can never
  // re-register it (see `src/components/map/AGENTS.md` "Picking a point to query" for why a
  // per-map click handler reads the store imperatively).
  //
  // Two meanings, resolved in one handler so a single click can never fire both:
  //  1. a click ON a drawn land-context feature focuses that candidate precisely and pins the
  //     panel -- WITHOUT touching the selection. An area selection is therefore never collapsed
  //     to the clicked point (spec: "browse multiple features without losing the selected
  //     project area"), and `results` is not wiped from under the index being set, which is what
  //     the previous layer-scoped handler did by calling `setSelection` first.
  //  2. a click on bare canvas makes a new POINT selection at the clicked coordinate. This is the
  //     entry that breaks the deadlock: features only draw for a selection, and the only click
  //     path used to be a listener on those very features. It is active only while at least one
  //     group is toggled on, and the point is always the click -- never the viewport centre
  //     (spec: "Never substitute the viewport centre"; the store has no "viewport" mode).
  //     The panel is NOT pinned here: with no candidate focused it has nothing to show, and
  //     "arbitrarily choose the first overlap" is forbidden -- the drawn features and the
  //     accessible list are the candidate pickers.
  //
  // Order matters: the land-context pick runs FIRST, so a click on a drawn land-context feature
  // wins outright. Only a click that hit no drawn feature is then checked against the other
  // click owners ("one click, one meaning": a panel capturing query points, an intervention
  // feature, a popup/tap-pinned feature layer) before it may become a new point selection. That
  // predicate is shared with MapView's agent-popup handler -- see `click-ownership.ts`.
  useEffect(() => {
    if (!map) return;

    const onClick = (event: MapMouseEvent) => {
      const store = useLandContextStore.getState();
      if (!anyGroupEnabled(store.enabledGroups)) return;

      const hitId = pickRenderedLandContextFeatureId(map, event.point);
      if (hitId !== null) {
        const index = store.results.findIndex((feature) => feature.id === hitId);
        if (index !== -1) {
          store.setCandidateIndex(index);
          store.openPanel();
          return;
        }
      }

      if (isClickOwnedByAnotherSurface(map, event.point)) return;
      store.setSelection({ mode: "point", point: [event.lngLat.lng, event.lngLat.lat] });
    };

    map.on("click", onClick);
    return () => {
      map.off("click", onClick);
    };
  }, [map]);

  // Mount point for the two store-driven affordances that need no map wiring of their own: the
  // honest status notice (what is admitted, what a click does, what came back) and the
  // accessible area-selection alternative (visible whenever a point selection exists) -- see
  // `LandContextStatusNotice.tsx` and `WideAreaSelectionAction.tsx`.
  return (
    <>
      <LandContextStatusNotice />
      <WideAreaSelectionAction />
    </>
  );
}
