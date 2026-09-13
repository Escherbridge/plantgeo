"use client";

import { useMemo } from "react";
import { GeoJsonLayer } from "@deck.gl/layers";
import type { Layer, PickingInfo } from "@deck.gl/core";
import { DECK_DEFAULT_PROPS } from "@/lib/map/deck-config";
import {
  useLandContextStore,
  type LandContextFeature,
  type LandContextGroupId,
} from "@/stores/land-context-store";

/**
 * Per-group fill/line colors so a hover or pinned selection can visually distinguish which of
 * the four toggle groups a feature belongs to at a glance. Kept local rather than added to the
 * shared `CATEGORY_COLORS` table in `deck-config.ts` -- these four ids are specific to this
 * feature and adding them centrally is the integrator's call once this ships.
 */
const GROUP_FILL_COLOR: Record<LandContextGroupId, [number, number, number, number]> = {
  "parcels-land-use": [59, 130, 246, 60],
  "electric-utility-territories": [234, 179, 8, 50],
  "blm-lands": [180, 83, 9, 60],
  "state-managed-lands": [16, 185, 129, 60],
};

const GROUP_LINE_COLOR: Record<LandContextGroupId, [number, number, number, number]> = {
  "parcels-land-use": [59, 130, 246, 220],
  "electric-utility-territories": [234, 179, 8, 220],
  "blm-lands": [180, 83, 9, 220],
  "state-managed-lands": [16, 185, 129, 220],
};

const SELECTED_LINE_WIDTH = 3;
const DEFAULT_LINE_WIDTH = 1;

function featureCollectionFor(
  features: LandContextFeature[],
  group: LandContextGroupId
): GeoJSON.FeatureCollection {
  return {
    type: "FeatureCollection",
    features: features
      .filter((feature) => feature.group === group)
      .map((feature) => ({
        type: "Feature",
        geometry: feature.geometry,
        properties: { landContextId: feature.id },
      })),
  };
}

export interface LandContextPickHandlers {
  /** Hover/keyboard-focus -- updates the dismissible identity card, never the pinned selection. */
  onFeatureHover: (feature: LandContextFeature | null, screenPosition: { x: number; y: number } | null) => void;
  /** Click/tap/explicit keyboard select -- pins the persistent detail panel. */
  onFeatureSelect: (feature: LandContextFeature) => void;
}

/**
 * Builds one pickable deck.gl `GeoJsonLayer` per enabled toggle group from the store's current
 * `results`. Intended to be merged into the host page's own DeckGL `layers` prop (this worker
 * does not own DeckGL/MapView instantiation) -- see `LandContextAGENTS` note in this file's
 * sibling `index.ts` barrel for the integration contract.
 *
 * INTEGRATION RISK (deck.gl interleaved mode): MapLibre's `interleaved: true` mode draws deck.gl
 * layers between basemap layers using the map's own WebGL context. `GeoJsonLayer`'s default
 * `pickable`/`autoHighlight` picking still works with interleaved rendering, but the picking
 * canvas position must come from the SAME DeckGL instance's `onHover`/`onClick`, not a second
 * ad-hoc overlay -- two competing DeckGL instances over one MapLibre canvas will fight for the
 * WebGL context and can blank the map (see `src/components/map/AGENTS.md` deck.gl notes this
 * track should append once merged). This factory intentionally returns layers only, not a
 * DeckGL/MapboxOverlay instance, so the integrator attaches them to the existing composite.
 */
export function useLandContextDeckLayers(handlers: LandContextPickHandlers): Layer[] {
  const enabledGroups = useLandContextStore((state) => state.enabledGroups);
  const results = useLandContextStore((state) => state.results);
  const hoveredFeature = useLandContextStore((state) => state.hoveredFeature);
  const selection = useLandContextStore((state) => state.selection);
  const candidateIndex = useLandContextStore((state) => state.candidateIndex);
  const selectedFeature =
    candidateIndex !== null && results[candidateIndex] ? results[candidateIndex] : null;

  return useMemo(() => {
    const groups = (Object.keys(enabledGroups) as LandContextGroupId[]).filter(
      (group) => enabledGroups[group]
    );

    return groups.map((group) => {
      const data = featureCollectionFor(results, group);
      return new GeoJsonLayer({
        id: `land-context-${group}`,
        data,
        ...DECK_DEFAULT_PROPS,
        stroked: true,
        filled: true,
        getFillColor: GROUP_FILL_COLOR[group],
        getLineColor: (feature) => {
          const id = feature.properties?.landContextId as string | undefined;
          const isActive = id === hoveredFeature?.id || id === selectedFeature?.id;
          return isActive ? [255, 255, 255, 255] : GROUP_LINE_COLOR[group];
        },
        getLineWidth: (feature) => {
          const id = feature.properties?.landContextId as string | undefined;
          return id === selectedFeature?.id ? SELECTED_LINE_WIDTH : DEFAULT_LINE_WIDTH;
        },
        lineWidthUnits: "pixels",
        onHover: (info: PickingInfo) => {
          const id = (info.object as GeoJSON.Feature | undefined)?.properties?.landContextId as
            | string
            | undefined;
          const feature = id ? results.find((candidate) => candidate.id === id) ?? null : null;
          handlers.onFeatureHover(
            feature,
            feature && info.x !== undefined && info.y !== undefined ? { x: info.x, y: info.y } : null
          );
        },
        onClick: (info: PickingInfo) => {
          const id = (info.object as GeoJSON.Feature | undefined)?.properties?.landContextId as
            | string
            | undefined;
          const feature = id ? results.find((candidate) => candidate.id === id) ?? null : null;
          if (feature) handlers.onFeatureSelect(feature);
        },
      });
    });
  }, [enabledGroups, results, hoveredFeature, selectedFeature, handlers, selection]);
}
