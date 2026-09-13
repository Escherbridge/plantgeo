"use client";

import { useEffect, useRef } from "react";
import type { Map as MapLibreMap, MapMouseEvent, MapGeoJSONFeature } from "maplibre-gl";
import { INTERVENTION_STYLE_LAYER_IDS } from "@/lib/map/layer-registry";
import { INTERVENTION_DRAFTS_SOURCE_ID } from "@/lib/map/sources";
import type { InterventionDetailRecord } from "@/lib/map/intervention-detail";
import { useInterventionDetailStore } from "@/stores/intervention-detail-store";

/**
 * Click-to-inspect for the merged intervention layer, in ONE place.
 *
 * All six style layers are bound here, per-layer, the way `WaterLayer.tsx` and
 * `BotanicalOccurrencesLayer.tsx` already bind theirs -- rather than scattered
 * across the two components that happen to own each source -- because the two
 * origins resolve differently and the difference is the whole point:
 *
 *   - a feature from `intervention-drafts-source` is resolved out of the record
 *     map `useInterventionDraftsOverlay` already holds, with NO network call
 *     (NFR-1); its geometry is the geometry the submitter drew;
 *   - a feature from the Martin `intervention_tiles` source is opened BY ID, and
 *     the modal fetches `interventions.getInterventionDetail`, because a vector
 *     tile's geometry is simplified and its columns are only what the tile
 *     function projects.
 *
 * Handlers are registered once per map and read their inputs through a ref, so a
 * refetch of the overlay does not churn six MapLibre listeners.
 */
export function useInterventionDetailClicks(
  map: MapLibreMap | null,
  recordsById: Map<string, InterventionDetailRecord>
): void {
  const recordsRef = useRef(recordsById);
  useEffect(() => {
    recordsRef.current = recordsById;
  }, [recordsById]);

  useEffect(() => {
    if (!map) return;

    const handleClick = (
      event: MapMouseEvent & { features?: MapGeoJSONFeature[] }
    ) => {
      const feature = event.features?.[0];
      const featureId = interventionFeatureId(feature);
      if (!featureId) return;

      const store = useInterventionDetailStore.getState();
      if (feature?.source === INTERVENTION_DRAFTS_SOURCE_ID) {
        const record = recordsRef.current.get(featureId);
        // A drafted feature whose record went missing (an overlay refetch
        // racing the click) still opens -- by id, like a published one -- rather
        // than swallowing the click.
        if (record) {
          store.openWithRecord(record);
          return;
        }
      }
      store.openById(featureId);
    };

    for (const layerId of INTERVENTION_STYLE_LAYER_IDS) {
      map.on("click", layerId, handleClick);
    }
    return () => {
      for (const layerId of INTERVENTION_STYLE_LAYER_IDS) {
        map.off("click", layerId, handleClick);
      }
    };
  }, [map]);
}

/**
 * The feature's `geo.features.id`, whichever source drew it.
 *
 * The drafts overlay writes it as `id`; `geo.intervention_tiles()` projects the
 * same column, and MapLibre may also surface it as the feature's own `id`. Only
 * a uuid-shaped string is accepted, so a tile carrying an integer feature index
 * is never mistaken for a database id.
 */
function interventionFeatureId(
  feature: MapGeoJSONFeature | undefined
): string | null {
  const candidates = [
    feature?.properties?.id,
    feature?.properties?.feature_id,
    feature?.id,
  ];
  for (const candidate of candidates) {
    if (typeof candidate === "string" && UUID_PATTERN.test(candidate)) {
      return candidate;
    }
  }
  return null;
}

const UUID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
