"use client";

import { useEffect, useRef, useCallback } from "react";
import type { Map as MapLibreMap, GeoJSONSource, MapMouseEvent } from "maplibre-gl";
import { getFirstSymbolLayer, safeRemoveLayerAndSource } from "@/lib/map/layer-utils";
import { BOTANICAL_DETAIL_MIN_ZOOM, type BotanicalOccurrenceFeature } from "@/lib/botanical-occurrences";

const SOURCE_ID = "botanical-occurrences";
const LAYER_ID_EXACT = "botanical-occurrences-exact";
const LAYER_ID_GENERALIZED = "botanical-occurrences-generalized";
const LAYER_ID_POSSIBLE = "botanical-occurrences-possible";

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
] as const;

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
 */
export function botanicalOccurrencesToGeoJSON(
  features: BotanicalOccurrenceFeature[]
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
        scientific_name: feature.scientific_name,
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
  }, []);

  const removeLayers = useCallback((m: MapLibreMap) => {
    safeRemoveLayerAndSource(m, [LAYER_ID_EXACT, LAYER_ID_GENERALIZED, LAYER_ID_POSSIBLE], SOURCE_ID);
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
