"use client";

import React, { useEffect, useRef } from "react";
import { useMap } from "@/lib/map/map-context";
import { useDrawingStore } from "@/stores/drawing-store";
import type maplibregl from "maplibre-gl";

function getCoords(feature: GeoJSON.Feature): [number, number][] {
  const geom = feature.geometry;
  if (geom.type === "Point") return [geom.coordinates as [number, number]];
  if (geom.type === "LineString") return geom.coordinates as [number, number][];
  if (geom.type === "Polygon") return geom.coordinates[0] as [number, number][];
  return [];
}

function setCoords(feature: GeoJSON.Feature, coords: [number, number][]): GeoJSON.Feature {
  const geom = feature.geometry;
  if (geom.type === "Point") {
    return { ...feature, geometry: { ...geom, coordinates: coords[0] } };
  }
  if (geom.type === "LineString") {
    return { ...feature, geometry: { ...geom, coordinates: coords } };
  }
  if (geom.type === "Polygon") {
    const ring = coords[coords.length - 1] === coords[0] ? coords : [...coords, coords[0]];
    return { ...feature, geometry: { ...geom, coordinates: [ring] } };
  }
  return feature;
}

export function VertexEditor() {
  const map = useMap();
  const selectedIndex = useDrawingStore((s) => s.selectedFeatureIndex);
  const features = useDrawingStore((s) => s.features);
  const updateFeature = useDrawingStore((s) => s.updateFeature);
  const undo = useDrawingStore((s) => s.undo);
  const markersRef = useRef<maplibregl.Marker[]>([]);

  const selectedFeature =
    selectedIndex !== null ? features.features[selectedIndex] : null;

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === "z") {
        undo();
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [undo]);

  useEffect(() => {
    markersRef.current.forEach((m) => m.remove());
    markersRef.current = [];

    if (!map || !selectedFeature || selectedIndex === null) return;

    const coords = getCoords(selectedFeature);
    // For polygon, skip the closing duplicate vertex
    const isPolygon = selectedFeature.geometry.type === "Polygon";
    const editCoords = isPolygon ? coords.slice(0, -1) : coords;

    const maplibre = (map as unknown as { _maplibre?: typeof import("maplibre-gl") })
      ._maplibre;

    editCoords.forEach((coord, idx) => {
      const el = document.createElement("div");
      el.style.cssText = `
        width: 10px;
        height: 10px;
        border-radius: 50%;
        background: hsl(var(--primary));
        border: 2px solid white;
        cursor: grab;
        box-shadow: 0 1px 4px rgba(0,0,0,0.4);
      `;

      // Dynamically import maplibre-gl to create Marker
      import("maplibre-gl").then((ml) => {
        const marker = new ml.default.Marker({ element: el, draggable: true })
          .setLngLat(coord)
          .addTo(map);

        marker.on("drag", () => {
          const { lng, lat } = marker.getLngLat();
          const newCoords = editCoords.map((c, i) =>
            i === idx ? ([lng, lat] as [number, number]) : c
          );
          const updated = setCoords(selectedFeature, newCoords);
          updateFeature(selectedIndex, updated);
        });

        markersRef.current.push(marker);
      });
    });

    // Insert vertex on click between vertices
    const handleClick = (e: maplibregl.MapMouseEvent) => {
      if (editCoords.length < 2) return;
      const { lng, lat } = e.lngLat;
      const clickPt: [number, number] = [lng, lat];

      let minDist = Infinity;
      let insertAt = -1;

      for (let i = 0; i < editCoords.length - 1; i++) {
        const a = editCoords[i];
        const b = editCoords[i + 1];
        const mx = (a[0] + b[0]) / 2;
        const my = (a[1] + b[1]) / 2;
        const dx = clickPt[0] - mx;
        const dy = clickPt[1] - my;
        const dist = Math.sqrt(dx * dx + dy * dy);
        if (dist < minDist) {
          minDist = dist;
          insertAt = i + 1;
        }
      }

      if (insertAt >= 0 && minDist < 0.005) {
        const newCoords = [
          ...editCoords.slice(0, insertAt),
          clickPt,
          ...editCoords.slice(insertAt),
        ];
        updateFeature(selectedIndex, setCoords(selectedFeature, newCoords));
      }
    };

    map.on("click", handleClick);

    return () => {
      markersRef.current.forEach((m) => m.remove());
      markersRef.current = [];
      map.off("click", handleClick);
    };
  }, [map, selectedFeature, selectedIndex, updateFeature]);

  return null;
}
