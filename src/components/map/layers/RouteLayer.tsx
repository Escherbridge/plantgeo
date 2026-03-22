"use client";

import { useEffect } from "react";
import { useMap } from "@/lib/map/map-context";
import { useRoutingStore } from "@/stores/routing-store";
import type { GeoJSONSource } from "maplibre-gl";

const PRIMARY_COLOR = "#10b981";
const ALT_COLOR = "#6b7280";
const SOURCE_PRIMARY = "route-primary";
const SOURCE_ALT = "route-alternatives";
const LAYER_PRIMARY = "route-primary-line";
const LAYER_ALT = "route-alt-line";

const EMPTY_GEOJSON: GeoJSON.FeatureCollection = {
  type: "FeatureCollection",
  features: [],
};

function routeToFeature(
  route: { geometry: GeoJSON.LineString },
  index: number
): GeoJSON.Feature {
  return {
    type: "Feature",
    properties: { index },
    geometry: route.geometry,
  };
}

export function RouteLayer() {
  const map = useMap();
  const { activeRoute, alternatives } = useRoutingStore();

  useEffect(() => {
    if (!map) return;

    const setup = () => {
      if (!map.getSource(SOURCE_PRIMARY)) {
        map.addSource(SOURCE_PRIMARY, {
          type: "geojson",
          data: EMPTY_GEOJSON,
        });
      }

      if (!map.getSource(SOURCE_ALT)) {
        map.addSource(SOURCE_ALT, {
          type: "geojson",
          data: EMPTY_GEOJSON,
        });
      }

      if (!map.getLayer(LAYER_ALT)) {
        map.addLayer({
          id: LAYER_ALT,
          type: "line",
          source: SOURCE_ALT,
          layout: {
            "line-join": "round",
            "line-cap": "round",
          },
          paint: {
            "line-color": ALT_COLOR,
            "line-width": 3,
            "line-opacity": 0.7,
          },
        });
      }

      if (!map.getLayer(LAYER_PRIMARY)) {
        map.addLayer({
          id: LAYER_PRIMARY,
          type: "line",
          source: SOURCE_PRIMARY,
          layout: {
            "line-join": "round",
            "line-cap": "round",
          },
          paint: {
            "line-color": PRIMARY_COLOR,
            "line-width": 5,
            "line-opacity": 1,
          },
        });
      }
    };

    if (map.isStyleLoaded()) {
      setup();
    } else {
      map.once("style.load", setup);
    }

    return () => {
      if (map.getLayer(LAYER_PRIMARY)) map.removeLayer(LAYER_PRIMARY);
      if (map.getLayer(LAYER_ALT)) map.removeLayer(LAYER_ALT);
      if (map.getSource(SOURCE_PRIMARY)) map.removeSource(SOURCE_PRIMARY);
      if (map.getSource(SOURCE_ALT)) map.removeSource(SOURCE_ALT);
    };
  }, [map]);

  useEffect(() => {
    if (!map || !map.isStyleLoaded()) return;

    const primarySource = map.getSource(SOURCE_PRIMARY) as GeoJSONSource | undefined;
    const altSource = map.getSource(SOURCE_ALT) as GeoJSONSource | undefined;

    if (!primarySource || !altSource) return;

    if (activeRoute) {
      primarySource.setData({
        type: "FeatureCollection",
        features: [routeToFeature(activeRoute, 0)],
      });

      if (alternatives.length > 0) {
        altSource.setData({
          type: "FeatureCollection",
          features: alternatives.slice(0, 3).map((r, i) => routeToFeature(r, i)),
        });
      } else {
        altSource.setData(EMPTY_GEOJSON);
      }

      const coords = activeRoute.geometry.coordinates as [number, number][];
      if (coords.length > 0) {
        const bounds = coords.reduce(
          (b, c) => ({
            minLng: Math.min(b.minLng, c[0]),
            maxLng: Math.max(b.maxLng, c[0]),
            minLat: Math.min(b.minLat, c[1]),
            maxLat: Math.max(b.maxLat, c[1]),
          }),
          { minLng: coords[0][0], maxLng: coords[0][0], minLat: coords[0][1], maxLat: coords[0][1] }
        );
        map.fitBounds(
          [
            [bounds.minLng, bounds.minLat],
            [bounds.maxLng, bounds.maxLat],
          ],
          { padding: 60, duration: 800 }
        );
      }
    } else {
      primarySource.setData(EMPTY_GEOJSON);
      altSource.setData(EMPTY_GEOJSON);
    }
  }, [map, activeRoute, alternatives]);

  return null;
}

export default RouteLayer;
