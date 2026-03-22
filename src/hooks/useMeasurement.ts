"use client";

import { useMemo, useState } from "react";
import { useDrawingStore } from "@/stores/drawing-store";
import {
  haversineDistance,
  polygonArea,
  lineStringDistance,
  formatDistance,
  formatArea,
} from "@/lib/map/measurement";

export type MeasurementUnit = "metric" | "imperial";

interface MeasurementResult {
  distance: string | null;
  area: string | null;
  unit: MeasurementUnit;
  setUnit: (unit: MeasurementUnit) => void;
}

export function useMeasurement(): MeasurementResult {
  const [unit, setUnit] = useState<MeasurementUnit>("metric");
  const features = useDrawingStore((s) => s.features);
  const selectedIndex = useDrawingStore((s) => s.selectedFeatureIndex);
  const isDrawing = useDrawingStore((s) => s.isDrawing);

  const result = useMemo(() => {
    const activeFeatures =
      selectedIndex !== null
        ? [features.features[selectedIndex]]
        : isDrawing
        ? [features.features[features.features.length - 1]]
        : features.features;

    let totalDistance = 0;
    let totalArea = 0;

    for (const feature of activeFeatures) {
      if (!feature) continue;
      const geom = feature.geometry;
      if (geom.type === "LineString") {
        totalDistance += lineStringDistance(geom.coordinates as [number, number][]);
      } else if (geom.type === "Polygon") {
        const ring = geom.coordinates[0] as [number, number][];
        totalArea += polygonArea(ring);
        if (ring.length >= 2) {
          totalDistance += lineStringDistance(ring);
        }
      } else if (geom.type === "Point") {
        // single point: no distance/area
      }
    }

    return {
      distance: totalDistance > 0 ? formatDistance(totalDistance, unit) : null,
      area: totalArea > 0 ? formatArea(totalArea, unit) : null,
    };
  }, [features, selectedIndex, isDrawing, unit]);

  return { ...result, unit, setUnit };
}
