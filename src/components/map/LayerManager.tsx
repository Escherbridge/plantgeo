"use client";

import { useState } from "react";
import dynamic from "next/dynamic";
import { useMap } from "@/lib/map/map-context";
import { useMapStore } from "@/stores/map-store";

const LandFireLayer = dynamic(
  () => import("@/components/map/layers/LandFireLayer").then((m) => ({ default: m.LandFireLayer })),
  { ssr: false }
);
const WaterLayer = dynamic(
  () => import("@/components/map/layers/WaterLayer").then((m) => ({ default: m.WaterLayer })),
  { ssr: false }
);
const DroughtLayer = dynamic(
  () => import("@/components/map/layers/DroughtLayer").then((m) => ({ default: m.DroughtLayer })),
  { ssr: false }
);
const VegetationLayer = dynamic(
  () => import("@/components/map/layers/VegetationLayer").then((m) => ({ default: m.VegetationLayer })),
  { ssr: false }
);
const SoilLayer = dynamic(
  () => import("@/components/map/layers/SoilLayer").then((m) => ({ default: m.SoilLayer })),
  { ssr: false }
);
const DemandHeatmapLayer = dynamic(
  () => import("@/components/map/layers/DemandHeatmapLayer").then((m) => ({ default: m.DemandHeatmapLayer })),
  { ssr: false }
);

export default function LayerManager() {
  const map = useMap();
  const { viewport } = useMapStore();

  // Layers with a `visible` prop get boolean state; others are always mounted
  const [showLandFire, setShowLandFire] = useState(false);
  const [showDemandHeatmap, setShowDemandHeatmap] = useState(false);

  // Suppress unused-variable warnings — these setters are available for
  // future wiring (e.g. from PanelManager or a toolbar).
  void setShowLandFire;
  void setShowDemandHeatmap;

  // Compute bbox string from viewport for DemandHeatmapLayer
  const zoom = viewport.zoom ?? 8;
  const degPerPixel = 360 / Math.pow(2, zoom + 8);
  const halfW = degPerPixel * 512;
  const halfH = degPerPixel * 256;
  const bbox = [
    (viewport.longitude - halfW).toFixed(4),
    (viewport.latitude - halfH).toFixed(4),
    (viewport.longitude + halfW).toFixed(4),
    (viewport.latitude + halfH).toFixed(4),
  ].join(",");

  if (!map) return null;

  return (
    <>
      <LandFireLayer map={map} visible={showLandFire} />
      <WaterLayer map={map} />
      <DroughtLayer map={map} geojson={null} />
      <VegetationLayer map={map} />
      <SoilLayer map={map} />
      <DemandHeatmapLayer map={map} bbox={bbox} visible={showDemandHeatmap} />
    </>
  );
}
