"use client";

import { useState, useEffect } from "react";
import dynamic from "next/dynamic";
import { useMap } from "@/lib/map/map-context";
import { useMapStore } from "@/stores/map-store";
import { useVegetationStore } from "@/stores/vegetation-store";
import { useSoilStore } from "@/stores/soil-store";
import { DEMO_DROUGHT_GEOJSON, DEMO_WATER_GAUGES, DEMO_GROUNDWATER_WELLS } from "@/lib/map/demo-data";

const FireLayer = dynamic(
  () => import("@/components/map/layers/FireLayer").then((m) => ({ default: m.FireLayer })),
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
  const { viewport, activeLayers } = useMapStore();
  const vegState = useVegetationStore();
  const soilState = useSoilStore();

  // Live drought data with fallback to demo
  const [droughtGeoJSON, setDroughtGeoJSON] = useState(DEMO_DROUGHT_GEOJSON);
  useEffect(() => {
    if (!activeLayers.includes("drought")) return;
    fetch("https://droughtmonitor.unl.edu/data/json/usdm_current.json")
      .then((r) => r.json())
      .then((data) => setDroughtGeoJSON(data))
      .catch(() => {}); // keep demo data on error
  }, [activeLayers]);

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
      <FireLayer map={map} visible={activeLayers.includes("fire")} />
      <WaterLayer map={map} gauges={DEMO_WATER_GAUGES} wells={DEMO_GROUNDWATER_WELLS} visible={activeLayers.includes("water")} />
      <DroughtLayer map={map} geojson={droughtGeoJSON} visible={activeLayers.includes("drought")} />
      <VegetationLayer
        map={map}
        visible={activeLayers.includes("vegetation")}
        mode={vegState.mode}
        year={vegState.year}
        month={vegState.month}
        ndviMode={vegState.ndviMode}
        showNDWI={vegState.showNDWI}
        opacity={vegState.opacity}
      />
      <SoilLayer
        map={map}
        visible={activeLayers.includes("soil")}
        property={soilState.property}
        opacity={soilState.opacity}
      />
      <DemandHeatmapLayer map={map} bbox={bbox} visible={activeLayers.includes("demand-heatmap")} />
    </>
  );
}
