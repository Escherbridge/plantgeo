"use client";

import { useCallback, useEffect, useMemo, useRef } from "react";
import dynamic from "next/dynamic";
import { useMap } from "@/lib/map/map-context";
import { useMapStore } from "@/stores/map-store";
import {
  useLayerVisibility,
  useSelectedMapDateRef,
  useSoilDisplayMode,
  useVegetationDisplayMode,
  type LayerVisibility,
} from "@/lib/map/layer-toggle-context";
import { useFireData } from "@/hooks/useFireData";
import { trpc } from "@/lib/trpc/client";
import { viewportBbox } from "@/lib/map/viewport-bbox";
import { styleBackedLayerEntries } from "@/lib/map/layer-registry";
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
  // One read of the toggle context covers every layer below: which are switched on, and
  // the mode each draws in. Nothing here reads a toggle id as a bare string.
  const layerVisibility = useLayerVisibility();
  const vegetationMode = useVegetationDisplayMode();
  const soilMode = useSoilDisplayMode();
  const fireData = useFireData(layerVisibility.fire);
  const zoom = viewport.zoom ?? 8;
  const bbox = viewportBbox(viewport.longitude, viewport.latitude, zoom);

  // lane J -- the four queries below are dateless: each returns the latest published
  // value, not the day the slider is labelled with. Making them date-aware needs a
  // *reactive* selectedDate, which is `useMapDay().selectedDate` on the toggle context;
  // selectedDateRef below is for the style.load handler body only and cannot trigger a
  // refetch. Subscribe to the debounced day, not the raw one -- useMetricAtDate debounces
  // so that a scrub issues one request on settle rather than one per pointer tick, and
  // these four would otherwise reintroduce exactly that storm.
  // lane J: replace `undefined` with { date } once getDroughtClassification accepts one.
  const droughtQuery = trpc.environmental.getDroughtClassification.useQuery(
    undefined,
    { enabled: layerVisibility.drought }
  );
  const droughtGeoJSON = droughtQuery.data ?? EMPTY_FEATURE_COLLECTION;
  const waterEnabled = layerVisibility.water;
  // lane J: add { date } to this input once environmental.getStreamflow accepts one.
  const streamflowQuery = trpc.environmental.getStreamflow.useQuery(
    { bbox: bbox ?? "-180,-90,180,90" },
    { enabled: waterEnabled && bbox !== null, staleTime: 15 * 60 * 1000 }
  );
  // lane J: add { date } to this input once environmental.getGroundwater accepts one.
  const groundwaterQuery = trpc.environmental.getGroundwater.useQuery(
    { bbox: bbox ?? "-180,-90,180,90" },
    { enabled: waterEnabled && bbox !== null, staleTime: 60 * 60 * 1000 }
  );

  const weatherEnabled = layerVisibility.weather;
  // Reads every published, fresh observation across the viewport bbox --
  // not just the nearest one -- so the wind layer reflects the full spread
  // of warehouse-backed samples instead of a single point.
  // lane J: add { date } to this input once wildfire.getWeatherForBbox accepts one.
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
    (mapInstance: NonNullable<typeof map>, currentVisibility: LayerVisibility) => {
      for (const entry of styleBackedLayerEntries()) {
        const visibility = currentVisibility[entry.toggleId] ? "visible" : "none";
        for (const layerId of entry.styleLayerIds) {
          if (mapInstance.getLayer(layerId)) {
            mapInstance.setLayoutProperty(layerId, "visibility", visibility);
          }
        }
      }
    },
    []
  );

  // Read through a ref so the style.load registration below never depends on the toggle
  // state -- see src/components/map/AGENTS.md "Style.load listener order".
  const layerVisibilityRef = useRef(layerVisibility);
  useEffect(() => {
    layerVisibilityRef.current = layerVisibility;
  }, [layerVisibility]);

  // The toggle context's non-reactive view of the slider's day. Two distinct reasons it is
  // a ref, both load-bearing. First, a reactive selector here would re-render this
  // component -- and its ~8 layer children -- on every pointer tick of a day-granular
  // scrub, for a value no render path reads. Second, putting selectedDate in the
  // style.load registration effect below would tear down and re-register the handler on
  // every scrub, moving it behind ServiceAreaLayer's and dropping the dimming mask on top
  // of the data pins. Lane J's date-filtered re-add must read selectedDateRef.current
  // inside the handler body and leave the dependency array as [map, applyVisibility].
  const selectedDateRef = useSelectedMapDateRef();

  // Registered once per map so it keeps its place in the listener queue.
  useEffect(() => {
    if (!map) return;
    const mapInstance = map;
    const onStyleLoad = () => applyVisibility(mapInstance, layerVisibilityRef.current);

    mapInstance.on("style.load", onStyleLoad);
    return () => {
      mapInstance.off("style.load", onStyleLoad);
    };
  }, [map, applyVisibility]);

  // Apply toggles immediately, without touching the listener registration.
  useEffect(() => {
    if (!map || !map.isStyleLoaded()) return;
    applyVisibility(map, layerVisibility);
  }, [map, layerVisibility, applyVisibility]);

  if (!map) return null;

  return (
    <>
      <FireLayer map={map} visible={layerVisibility.fire} geojson={fireData.data} />
      <WaterLayer
        map={map}
        gauges={streamflowQuery.data ?? []}
        wells={groundwaterQuery.data ?? []}
        visible={waterEnabled}
      />
      <DroughtLayer map={map} geojson={droughtGeoJSON} visible={layerVisibility.drought} />
      <VegetationLayer
        map={map}
        visible={layerVisibility.vegetation}
        mode={vegetationMode.mode}
        year={vegetationMode.year}
        month={vegetationMode.month}
        ndviMode={vegetationMode.ndviMode}
        showNDWI={vegetationMode.showNDWI}
        opacity={vegetationMode.opacity}
      />
      <SoilLayer
        map={map}
        visible={layerVisibility.soil}
        property={soilMode.property}
        opacity={soilMode.opacity}
      />
      <DemandHeatmapLayer
        map={map}
        bbox={bbox}
        zoom={zoom}
        visible={layerVisibility["demand-heatmap"] && bbox !== null}
      />
      <WeatherLayer map={map} data={weatherData} visible={weatherEnabled} />
    </>
  );
}
