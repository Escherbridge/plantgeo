"use client";

import { useEffect, useRef } from "react";
import maplibregl from "maplibre-gl";
import { getStyle } from "@/lib/map/styles";
import { MAP_MAX_ZOOM, clampCameraZoom } from "@/lib/map/camera-limits";
import "maplibre-gl/dist/maplibre-gl.css";

export interface RoutePosition {
  time: string | Date;
  metadata: { lat: number; lon: number };
}

export interface RouteStop {
  lat: number;
  lon: number;
  startTime: string | Date;
  endTime: string | Date;
  durationMs: number;
}

export interface AssetRouteMapProps {
  positions: RoutePosition[];
  stops: RouteStop[];
}

const ROUTE_SOURCE_ID = "asset-route-line";
const STOPS_SOURCE_ID = "asset-route-stops";

/**
 * Self-contained MapLibre instance for one asset's route history, mirroring the standalone
 * construction `EmbedMap.tsx` uses (own `maplibregl.Map`, own container, `maxZoom` FROM
 * `MAP_MAX_ZOOM` rather than a bare literal) rather than reusing the shell-owned map the
 * intervention workspace shares, since this panel mounts and unmounts per selected asset.
 */
export default function AssetRouteMap({ positions, stops }: AssetRouteMapProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);

  useEffect(() => {
    if (!containerRef.current) return;
    const map = new maplibregl.Map({
      container: containerRef.current,
      style: getStyle("dark"),
      center: [0, 20],
      zoom: clampCameraZoom(2),
      maxZoom: MAP_MAX_ZOOM,
      attributionControl: false,
    });
    mapRef.current = map;

    return () => {
      map.remove();
      mapRef.current = null;
    };
    // One map per mount; `positions`/`stops` are applied by the effect below once loaded.
     
  }, []);

  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;

    const coordinates = positions.map(
      (position): [number, number] => [position.metadata.lon, position.metadata.lat]
    );

    function paint(map: maplibregl.Map) {
      const routeGeoJson: GeoJSON.Feature<GeoJSON.LineString> = {
        type: "Feature",
        properties: {},
        geometry: { type: "LineString", coordinates },
      };
      const stopsGeoJson: GeoJSON.FeatureCollection<GeoJSON.Point> = {
        type: "FeatureCollection",
        features: stops.map((stop) => ({
          type: "Feature",
          properties: { durationMs: stop.durationMs },
          geometry: { type: "Point", coordinates: [stop.lon, stop.lat] },
        })),
      };

      const routeSource = map.getSource(ROUTE_SOURCE_ID) as maplibregl.GeoJSONSource | undefined;
      if (routeSource) {
        routeSource.setData(routeGeoJson);
      } else if (coordinates.length > 0) {
        map.addSource(ROUTE_SOURCE_ID, { type: "geojson", data: routeGeoJson });
        map.addLayer({
          id: ROUTE_SOURCE_ID,
          type: "line",
          source: ROUTE_SOURCE_ID,
          paint: { "line-color": "#22d3ee", "line-width": 3 },
        });
      }

      const stopsSource = map.getSource(STOPS_SOURCE_ID) as maplibregl.GeoJSONSource | undefined;
      if (stopsSource) {
        stopsSource.setData(stopsGeoJson);
      } else if (stopsGeoJson.features.length > 0) {
        map.addSource(STOPS_SOURCE_ID, { type: "geojson", data: stopsGeoJson });
        map.addLayer({
          id: STOPS_SOURCE_ID,
          type: "circle",
          source: STOPS_SOURCE_ID,
          paint: {
            "circle-radius": 6,
            "circle-color": "#f59e0b",
            "circle-stroke-width": 2,
            "circle-stroke-color": "#0f172a",
          },
        });
      }

      if (coordinates.length > 0) {
        const bounds = coordinates.reduce(
          (acc, coordinate) => acc.extend(coordinate),
          new maplibregl.LngLatBounds(coordinates[0], coordinates[0])
        );
        map.fitBounds(bounds, { padding: 40, duration: 0, maxZoom: MAP_MAX_ZOOM });
      }
    }

    if (map.isStyleLoaded()) {
      paint(map);
    } else {
      map.once("load", () => paint(map));
    }
  }, [positions, stops]);

  return (
    <div
      ref={containerRef}
      role="img"
      aria-label="Asset route history map"
      className="h-64 w-full rounded-xl border border-slate-800 overflow-hidden"
    />
  );
}
