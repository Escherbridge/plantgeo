"use client";

import { useCallback, useEffect, useMemo, useRef } from "react";
import dynamic from "next/dynamic";
import { useMap } from "@/lib/map/map-context";
import { useMapStore } from "@/stores/map-store";
import { useVegetationStore } from "@/stores/vegetation-store";
import { useSoilStore } from "@/stores/soil-store";
import { useFireData } from "@/hooks/useFireData";
import { trpc } from "@/lib/trpc/client";
import { viewportBbox } from "@/lib/map/viewport-bbox";
import { STYLE_LAYER_TOGGLE_MAP } from "@/lib/map/layers";
import type { WindPoint } from "@/components/map/layers/WeatherLayer";

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
const WeatherLayer = dynamic(
  () => import("@/components/map/layers/WeatherLayer").then((m) => ({ default: m.WeatherLayer })),
  { ssr: false }
);

export default function LayerManager() {
  const map = useMap();
  const viewport = useMapStore((s) => s.viewport);
  const activeLayers = useMapStore((s) => s.activeLayers);
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

  const weatherEnabled = activeLayers.includes("weather");
  // Reads every published, fresh observation across the viewport bbox --
  // not just the nearest one -- so the wind layer reflects the full spread
  // of warehouse-backed samples instead of a single point.
  const weatherQuery = trpc.wildfire.getWeatherForBbox.useQuery(
    { bbox: bbox ?? "-180,-90,180,90" },
    { enabled: weatherEnabled && bbox !== null, staleTime: 15 * 60 * 1000 }
  );
  // Every rendered field must be measured: a partial observation is dropped
  // rather than back-filled with a zero the upstream never reported. Memoized
  // because this component re-renders on every viewport tick.
  const weatherData = useMemo<WindPoint[]>(
    () =>
      (weatherQuery.data ?? [])
        .filter(
          (observation) =>
            observation.windSpeed !== null &&
            observation.windDirection !== null &&
            observation.temperature !== null &&
            observation.humidity !== null
        )
        .map((observation) => ({
          coordinates: [observation.lon, observation.lat],
          windSpeed: observation.windSpeed as number,
          windDirection: observation.windDirection as number,
          temperature: observation.temperature as number,
          humidity: observation.humidity as number,
        })),
    [weatherQuery.data]
  );

  // Sync visibility of style-baked Martin layers (fire-perimeters/
  // interventions/building-footprints) with activeLayers -- these are static
  // layers added via getStyle(), not React-mounted components, so they need
  // setLayoutProperty instead of an unmount/remount cycle.
  const applyVisibility = useCallback(
    (mapInstance: NonNullable<typeof map>, layerToggles: string[]) => {
      for (const [toggleId, layerIds] of Object.entries(STYLE_LAYER_TOGGLE_MAP)) {
        const visibility = layerToggles.includes(toggleId) ? "visible" : "none";
        for (const layerId of layerIds) {
          if (mapInstance.getLayer(layerId)) {
            mapInstance.setLayoutProperty(layerId, "visibility", visibility);
          }
        }
      }
    },
    []
  );

  // Read through a ref so the style.load registration below never depends on
  // activeLayers -- see src/components/map/AGENTS.md "Style.load listener order".
  const activeLayersRef = useRef(activeLayers);
  useEffect(() => {
    activeLayersRef.current = activeLayers;
  }, [activeLayers]);

  // Registered once per map so it keeps its place in the listener queue.
  useEffect(() => {
    if (!map) return;
    const mapInstance = map;
    const onStyleLoad = () => applyVisibility(mapInstance, activeLayersRef.current);

    mapInstance.on("style.load", onStyleLoad);
    return () => {
      mapInstance.off("style.load", onStyleLoad);
    };
  }, [map, applyVisibility]);

  // Apply toggles immediately, without touching the listener registration.
  useEffect(() => {
    if (!map || !map.isStyleLoaded()) return;
    applyVisibility(map, activeLayers);
  }, [map, activeLayers, applyVisibility]);

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
      <WeatherLayer map={map} data={weatherData} visible={weatherEnabled} />
    </>
  );
}
