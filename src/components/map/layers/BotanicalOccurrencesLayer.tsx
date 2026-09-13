"use client";

import { useEffect, useRef, useCallback } from "react";
import type { Map as MapLibreMap, GeoJSONSource, MapMouseEvent } from "maplibre-gl";
import { getFirstSymbolLayer, safeRemoveLayerAndSource } from "@/lib/map/layer-utils";
import { BOTANICAL_DETAIL_MIN_ZOOM, type BotanicalOccurrenceFeature } from "@/lib/botanical-occurrences";
import { isProvisionalBotanicalCollection } from "@/lib/environmental/botanical-governance-status";

const SOURCE_ID = "botanical-occurrences";
const LAYER_ID_EXACT = "botanical-occurrences-exact";
const LAYER_ID_GENERALIZED = "botanical-occurrences-generalized";
const LAYER_ID_POSSIBLE = "botanical-occurrences-possible";
const LAYER_ID_PROVISIONAL_RING = "botanical-occurrences-provisional-ring";

/**
 * Circle-radius/style legend for the detail layer. Confirmed-exact records draw the smallest,
 * most saturated dot -- the tightest coordinate support gets the strongest visual claim.
 * `possible`-membership records draw as a distinct hollow-ring style at every zoom so an
 * uncertain determination is never visually indistinguishable from an admitted one, matching
 * the spec's "distinct style for membership: possible" requirement.
 */
export const BOTANICAL_OCCURRENCE_LEGEND = [
  { label: "Confirmed, exact locality", color: "#5ec26a" },
  { label: "Confirmed, generalized locality", color: "#2f7d3a" },
  { label: "Possible determination", color: "#e0c341" },
  { label: "Provisional: not yet governance-admitted", color: "#e8813f" },
] as const;

/** Circle-radius of the outer provisional ring; drawn slightly larger than the widest base dot. */
const PROVISIONAL_RING_RADIUS = 9;

/** A specimen record has no coordinates to place; the caller must never synthesize a centroid. */
function isSpatial(
  feature: BotanicalOccurrenceFeature
): feature is BotanicalOccurrenceFeature & { longitude: number; latitude: number } {
  return Number.isFinite(feature.longitude) && Number.isFinite(feature.latitude);
}

/**
 * Builds the GeoJSON this layer draws from raw wire features, dropping nonspatial records.
 * Exported so the surrounding data hook and the test suite can both build the same shape
 * without duplicating the spatial guard.
 *
 * The first four properties are what the PAINT FILTERS key on. The rest are PROVENANCE, carried
 * because the shared hover manager (`lib/map/hover-fields.ts`) reads MapLibre feature properties
 * and nothing else -- a tooltip cannot reach back into the response -- and a specimen dot whose
 * source and rights are not reachable on hover is an unattributed use of somebody's collection.
 * `publishedAt` is the RESPONSE's publication timestamp rather than a per-feature field, threaded
 * in by the caller so a reader can see how stale the generation they are looking at is.
 */
export function botanicalOccurrencesToGeoJSON(
  features: BotanicalOccurrenceFeature[],
  publishedAt: string | null = null
): GeoJSON.FeatureCollection {
  return {
    type: "FeatureCollection",
    features: features.filter(isSpatial).map((feature) => ({
      type: "Feature",
      geometry: { type: "Point", coordinates: [feature.longitude, feature.latitude] },
      properties: {
        occurrence_id: feature.occurrence_id,
        spatial_class: feature.spatial_class,
        membership: feature.membership,
        resolution_state: feature.resolution_state,
        provisional: isProvisionalBotanicalCollection(feature.collection_key),
        scientific_name: feature.scientific_name,
        family: feature.family,
        collection_key: feature.collection_key,
        catalog_number: feature.catalog_number,
        recorded_by: feature.recorded_by,
        event_start: feature.event_interval.start,
        event_end: feature.event_interval.end,
        event_precision: feature.event_interval.precision,
        coordinate_uncertainty_m: feature.coordinate_uncertainty_m,
        rights_uri: feature.rights_uri,
        attribution_text: feature.attribution_text,
        published_at: publishedAt,
      },
    })),
  };
}

interface BotanicalOccurrencesLayerProps {
  map: MapLibreMap | null;
  geojson: GeoJSON.FeatureCollection | null;
  zoom: number;
  visible?: boolean;
  onSelectFeature?: (occurrenceId: string) => void;
}

/**
 * Detail-zoom specimen occurrence points. Only mounted at `zoom >= BOTANICAL_DETAIL_MIN_ZOOM`
 * by the caller -- this component draws unconditionally once handed geojson, so the zoom-band
 * exclusivity with `BotanicalRichnessLayer` lives in whichever container chooses which layer
 * to mount, matching the pattern the other layer components in this directory use for their
 * own zoom/visibility gates.
 */
export function BotanicalOccurrencesLayer({
  map,
  geojson,
  zoom,
  visible = true,
  onSelectFeature,
}: BotanicalOccurrencesLayerProps) {
  const propsRef = useRef({ geojson, visible, zoom });
  propsRef.current = { geojson, visible, zoom };
  const onSelectFeatureRef = useRef(onSelectFeature);
  onSelectFeatureRef.current = onSelectFeature;

  const addLayers = useCallback((m: MapLibreMap) => {
    const { geojson } = propsRef.current;
    if (!geojson) return;
    const beforeId = getFirstSymbolLayer(m);

    if (!m.getSource(SOURCE_ID)) {
      m.addSource(SOURCE_ID, { type: "geojson", data: geojson });
    } else {
      (m.getSource(SOURCE_ID) as GeoJSONSource).setData(geojson);
    }

    if (!m.getLayer(LAYER_ID_EXACT)) {
      m.addLayer(
        {
          id: LAYER_ID_EXACT,
          type: "circle",
          source: SOURCE_ID,
          filter: ["all", ["==", ["get", "membership"], "confirmed"], ["==", ["get", "spatial_class"], "exact"]],
          paint: {
            "circle-radius": 4,
            "circle-color": BOTANICAL_OCCURRENCE_LEGEND[0].color,
            "circle-stroke-width": 1,
            "circle-stroke-color": "#0a1f0d",
          },
        },
        beforeId
      );
    }
    if (!m.getLayer(LAYER_ID_GENERALIZED)) {
      m.addLayer(
        {
          id: LAYER_ID_GENERALIZED,
          type: "circle",
          source: SOURCE_ID,
          filter: ["all", ["==", ["get", "membership"], "confirmed"], ["==", ["get", "spatial_class"], "generalized"]],
          paint: {
            "circle-radius": 8,
            "circle-color": BOTANICAL_OCCURRENCE_LEGEND[1].color,
            "circle-opacity": 0.45,
            "circle-stroke-width": 1,
            "circle-stroke-color": "#0a1f0d",
          },
        },
        beforeId
      );
    }
    if (!m.getLayer(LAYER_ID_POSSIBLE)) {
      m.addLayer(
        {
          id: LAYER_ID_POSSIBLE,
          type: "circle",
          source: SOURCE_ID,
          filter: ["==", ["get", "membership"], "possible"],
          paint: {
            "circle-radius": 5,
            "circle-color": "transparent",
            "circle-stroke-width": 2,
            "circle-stroke-color": BOTANICAL_OCCURRENCE_LEGEND[2].color,
            "circle-stroke-opacity": 0.9,
          },
        },
        beforeId
      );
    }
    // A distinct outer ring for records from a collection serving ahead of governance admission
    // (see `isProvisionalBotanicalCollection`) -- drawn on EVERY membership/spatial_class style
    // above rather than a fourth mutually-exclusive class, because "provisional" is an orthogonal
    // governance fact, not a competing claim about the coordinate or the determination. MapLibre
    // has no native dashed-circle-stroke; a ring one size up in a clearly non-taxonomic colour is
    // the plain-canvas equivalent, and it never obscures the base dot it surrounds.
    if (!m.getLayer(LAYER_ID_PROVISIONAL_RING)) {
      m.addLayer(
        {
          id: LAYER_ID_PROVISIONAL_RING,
          type: "circle",
          source: SOURCE_ID,
          filter: ["==", ["get", "provisional"], true],
          paint: {
            "circle-radius": PROVISIONAL_RING_RADIUS,
            "circle-color": "transparent",
            "circle-stroke-width": 1.5,
            "circle-stroke-color": BOTANICAL_OCCURRENCE_LEGEND[3].color,
            "circle-stroke-opacity": 0.85,
          },
        },
        beforeId
      );
    }
  }, []);

  const removeLayers = useCallback((m: MapLibreMap) => {
    safeRemoveLayerAndSource(
      m,
      [LAYER_ID_EXACT, LAYER_ID_GENERALIZED, LAYER_ID_POSSIBLE, LAYER_ID_PROVISIONAL_RING],
      SOURCE_ID
    );
  }, []);

  useEffect(() => {
    if (!map) return;
    if (!visible || !geojson || zoom < BOTANICAL_DETAIL_MIN_ZOOM) {
      removeLayers(map);
      return;
    }

    const onStyleLoad = () => {
      const current = propsRef.current;
      if (!current.visible || !current.geojson || current.zoom < BOTANICAL_DETAIL_MIN_ZOOM) return;
      addLayers(map);
    };

    if (map.isStyleLoaded()) {
      addLayers(map);
    } else {
      map.once("style.load", () => addLayers(map));
    }
    map.on("style.load", onStyleLoad);

    const onClick = (event: MapMouseEvent) => {
      const features = map.queryRenderedFeatures(event.point, {
        layers: [LAYER_ID_EXACT, LAYER_ID_GENERALIZED, LAYER_ID_POSSIBLE].filter((id) => map.getLayer(id)),
      });
      const occurrenceId = features[0]?.properties?.occurrence_id;
      if (typeof occurrenceId === "string") {
        onSelectFeatureRef.current?.(occurrenceId);
      }
    };
    map.on("click", LAYER_ID_EXACT, onClick);
    map.on("click", LAYER_ID_GENERALIZED, onClick);
    map.on("click", LAYER_ID_POSSIBLE, onClick);

    return () => {
      map.off("style.load", onStyleLoad);
      map.off("click", LAYER_ID_EXACT, onClick);
      map.off("click", LAYER_ID_GENERALIZED, onClick);
      map.off("click", LAYER_ID_POSSIBLE, onClick);
      removeLayers(map);
    };
  }, [map, geojson, visible, zoom, addLayers, removeLayers]);

  return null;
}
