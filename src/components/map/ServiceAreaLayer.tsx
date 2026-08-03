"use client";

import { useEffect, useRef, useCallback } from "react";
import type { Map as MapLibreMap, GeoJSONSource } from "maplibre-gl";
import { trpc } from "@/lib/trpc/client";
import { getFirstSymbolLayer, safeRemoveLayerAndSource } from "@/lib/map/layer-utils";

const SOURCE_ID = "ingestion-coverage";
const MASK_LAYER_ID = "ingestion-coverage-mask";
const GLOW_LAYER_ID = "ingestion-coverage-boundary-glow";
const LINE_LAYER_ID = "ingestion-coverage-boundary";

interface CoverageBbox {
  west: number;
  south: number;
  east: number;
  north: number;
}

interface ServiceAreaLayerProps {
  map: MapLibreMap | null;
}

// World rectangle wound CCW with the coverage bbox as an opposite-wound (CW)
// hole, per RFC 7946 -- dims everything the ingestion pipeline never touches.
function buildCoverageGeoJSON(bbox: CoverageBbox): GeoJSON.FeatureCollection {
  const { west, south, east, north } = bbox;
  return {
    type: "FeatureCollection",
    features: [
      {
        type: "Feature",
        properties: { kind: "mask" },
        geometry: {
          type: "Polygon",
          coordinates: [
            [
              [-180, -90],
              [180, -90],
              [180, 90],
              [-180, 90],
              [-180, -90],
            ],
            [
              [west, south],
              [west, north],
              [east, north],
              [east, south],
              [west, south],
            ],
          ],
        },
      },
      {
        type: "Feature",
        properties: { kind: "boundary" },
        geometry: {
          type: "LineString",
          coordinates: [
            [west, south],
            [east, south],
            [east, north],
            [west, north],
            [west, south],
          ],
        },
      },
    ],
  };
}

/**
 * Dims the world outside the configured ingestion bbox and draws its boundary,
 * so landing on an empty world view reads as "data lives inside this region"
 * rather than "the app is broken". Renders nothing until coverage is configured.
 */
export function ServiceAreaLayer({ map }: ServiceAreaLayerProps) {
  const coverage = trpc.layers.getIngestionCoverage.useQuery(undefined, {
    staleTime: 60 * 60 * 1000,
    retry: false,
  });

  const bbox: CoverageBbox | null =
    coverage.data?.configured && coverage.data.bbox ? coverage.data.bbox : null;

  const bboxRef = useRef<CoverageBbox | null>(null);
  bboxRef.current = bbox;

  const addAllLayers = useCallback((m: MapLibreMap) => {
    const current = bboxRef.current;
    if (!current) return;
    const data = buildCoverageGeoJSON(current);
    const beforeId = getFirstSymbolLayer(m);

    if (!m.getSource(SOURCE_ID)) {
      m.addSource(SOURCE_ID, { type: "geojson", data });
    } else {
      (m.getSource(SOURCE_ID) as GeoJSONSource).setData(data);
    }

    if (!m.getLayer(MASK_LAYER_ID)) {
      m.addLayer(
        {
          id: MASK_LAYER_ID,
          type: "fill",
          source: SOURCE_ID,
          filter: ["==", ["get", "kind"], "mask"],
          paint: { "fill-color": "#020617", "fill-opacity": 0.45 },
        },
        beforeId
      );
    }

    if (!m.getLayer(GLOW_LAYER_ID)) {
      m.addLayer(
        {
          id: GLOW_LAYER_ID,
          type: "line",
          source: SOURCE_ID,
          filter: ["==", ["get", "kind"], "boundary"],
          paint: {
            "line-color": "#22c55e",
            "line-width": ["interpolate", ["linear"], ["zoom"], 3, 4, 8, 7],
            "line-opacity": 0.15,
            "line-blur": 2,
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
          filter: ["==", ["get", "kind"], "boundary"],
          paint: {
            "line-color": "#22c55e",
            "line-width": ["interpolate", ["linear"], ["zoom"], 3, 1, 8, 2.5],
            "line-opacity": 0.8,
          },
        },
        beforeId
      );
    }
  }, []);

  const removeAllLayers = useCallback((m: MapLibreMap) => {
    safeRemoveLayerAndSource(m, [MASK_LAYER_ID, GLOW_LAYER_ID, LINE_LAYER_ID], SOURCE_ID);
  }, []);

  // Add/remove the mask+boundary and re-add across style switches, which wipe
  // custom layers. Mount this component before LayerManager in MapView so this
  // effect (and its style.load handler) registers first -- later layer
  // components add() after it, and MapLibre stacks later-added layers above
  // earlier ones sharing the same beforeId, so data pins end up above the mask.
  useEffect(() => {
    if (!map) return;

    if (!bbox) {
      removeAllLayers(map);
      return;
    }

    const onStyleLoad = () => {
      if (bboxRef.current) addAllLayers(map);
    };

    if (map.isStyleLoaded()) {
      addAllLayers(map);
    } else {
      map.once("style.load", () => addAllLayers(map));
    }

    map.on("style.load", onStyleLoad);

    return () => {
      map.off("style.load", onStyleLoad);
      removeAllLayers(map);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [map, bbox?.west, bbox?.south, bbox?.east, bbox?.north, addAllLayers, removeAllLayers]);

  // Constrain panning to the service area and land the user on it, once per
  // distinct coverage value. fitBounds only fires on the very first arrival
  // so a later coverage change (rare) doesn't yank the view out from under someone.
  const appliedBoundsKeyRef = useRef<string | null>(null);
  const hasFitRef = useRef(false);

  useEffect(() => {
    if (!map || !bbox) return;
    const key = `${bbox.west},${bbox.south},${bbox.east},${bbox.north}`;
    if (appliedBoundsKeyRef.current === key) return;
    appliedBoundsKeyRef.current = key;

    // Pad by half the span: a pitched (3D) camera sees far more ground than a
    // flat one, and MapLibre constrains the camera against maxBounds -- a tight
    // box silently cancels the pitch, which reads as a dead 3D toggle.
    const longitudePadding = Math.max(2, (bbox.east - bbox.west) * 0.5);
    const latitudePadding = Math.max(2, (bbox.north - bbox.south) * 0.5);
    map.setMaxBounds([
      [bbox.west - longitudePadding, bbox.south - latitudePadding],
      [bbox.east + longitudePadding, bbox.north + latitudePadding],
    ]);
    map.setMinZoom(4);

    if (!hasFitRef.current) {
      hasFitRef.current = true;
      map.fitBounds(
        [
          [bbox.west, bbox.south],
          [bbox.east, bbox.north],
        ],
        { padding: 40, duration: 800 }
      );
    }
  }, [map, bbox?.west, bbox?.south, bbox?.east, bbox?.north]);

  // Release the bounds/zoom lock on unmount so the map behaves normally after.
  useEffect(() => {
    return () => {
      if (map) {
        map.setMaxBounds(null);
        map.setMinZoom(0);
      }
    };
  }, [map]);

  return null;
}
