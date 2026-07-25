"use client";

import dynamic from "next/dynamic";
import { useMap } from "@/lib/map/map-context";
import { useMapStore } from "@/stores/map-store";
import { useVegetationStore } from "@/stores/vegetation-store";
import { useSoilStore } from "@/stores/soil-store";
import { useFireData } from "@/hooks/useFireData";
import { trpc } from "@/lib/trpc/client";
import { viewportBbox } from "@/lib/map/viewport-bbox";

const EMPTY_FEATURE_COLLECTION: GeoJSON.FeatureCollection = {
  type: "FeatureCollection",
  features: [],
};

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
  const fireData = useFireData(activeLayers.includes("fire"));

  const zoom = viewport.zoom ?? 8;
  const bbox = viewportBbox(viewport.longitude, viewport.latitude, zoom);

  const droughtQuery = trpc.environmental.getDroughtClassification.useQuery(
    undefined,
    { enabled: activeLayers.includes("drought") }
  );
  const droughtGeoJSON = droughtQuery.data ?? EMPTY_FEATURE_COLLECTION;
  const waterEnabled = activeLayers.includes("water");
  const streamflowQuery = trpc.environmental.getStreamflow.useQuery(
    { bbox: bbox ?? "-180,-90,180,90" },
    { enabled: waterEnabled && bbox !== null, staleTime: 15 * 60 * 1000 }
  );
  const groundwaterQuery = trpc.environmental.getGroundwater.useQuery(
    { bbox: bbox ?? "-180,-90,180,90" },
    { enabled: waterEnabled && bbox !== null, staleTime: 60 * 60 * 1000 }
  );

  if (!map) return null;

  return (
    <>
      <FireLayer map={map} visible={activeLayers.includes("fire")} geojson={fireData.data} />
      <WaterLayer
        map={map}
        gauges={streamflowQuery.data ?? []}
        wells={groundwaterQuery.data ?? []}
        visible={waterEnabled}
      />
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
      <DemandHeatmapLayer
        map={map}
        bbox={bbox}
        zoom={zoom}
        visible={activeLayers.includes("demand-heatmap") && bbox !== null}
      />
    </>
  );
}
