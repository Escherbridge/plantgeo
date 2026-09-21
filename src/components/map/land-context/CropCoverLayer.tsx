"use client";

import { useMap } from "@/lib/map/map-context";
import { LandContextViewportLayer } from "@/components/map/layers/LandContextViewportLayer";

const CROP_COLORS = ["interpolate", ["linear"], ["get", "crop_fraction"], 0, "#d6c9a5", 0.5, "#81a357", 1, "#287a38"];

export function CropCoverLayer({ geojson, visible }: { geojson: GeoJSON.FeatureCollection | null; visible: boolean }) {
  const map = useMap();
  return <LandContextViewportLayer map={map} idPrefix="crop-cover" colorExpression={CROP_COLORS}
    geojson={geojson} visible={visible} />;
}
