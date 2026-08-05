"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import dynamic from "next/dynamic";
import { useMap } from "@/lib/map/map-context";
import {
  useDebouncedMapDay,
  useLayerVisibility,
  useSoilDisplayMode,
  useVegetationDisplayMode,
  type LayerVisibility,
} from "@/lib/map/layer-toggle-context";
import { useFireData } from "@/hooks/useFireData";
import {
  useSoilSurveyQuery,
  useViewportBounds,
  useWatershedsQuery,
} from "@/hooks/useViewportProxiedLayers";
import { trpc } from "@/lib/trpc/client";
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
const SoilSurveyLayer = dynamic(
  () => import("@/components/map/layers/SoilSurveyLayer").then((m) => ({ default: m.SoilSurveyLayer })),
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
  // One read of the toggle context covers every layer below: which are switched on, and
  // the mode each draws in. Nothing here reads a toggle id as a bare string.
  const layerVisibility = useLayerVisibility();
  const vegetationMode = useVegetationDisplayMode();
  const soilMode = useSoilDisplayMode();
  const fireData = useFireData(layerVisibility.fire);
  // Shared with PanelManager: one derivation, so the map and the panels key on one bbox.
  const { zoom, bbox } = useViewportBounds();

  // The slider's day, settled. `requestDate` is undefined whenever the selection IS the
  // server's today, which keeps the hot path on the exact dateless query key -- and the exact
  // server query -- it has always used, so first paint never fetches the same day twice.
  // Settled rather than raw: a day-granular scrub writes on every pointer tick, and this
  // component sits above ~8 layer children.
  const { requestDate } = useDebouncedMapDay();
  // tRPC keys `undefined` input differently from an object, so the dateless case must stay
  // literally undefined here rather than becoming `{ date: undefined }`.
  const droughtQuery = trpc.environmental.getDroughtClassification.useQuery(
    requestDate === undefined ? undefined : { date: requestDate },
    { enabled: layerVisibility.drought }
  );
  const droughtGeoJSON = droughtQuery.data ?? EMPTY_FEATURE_COLLECTION;
  const waterEnabled = layerVisibility.water;
  const streamflowQuery = trpc.environmental.getStreamflow.useQuery(
    { bbox: bbox ?? "-180,-90,180,90", date: requestDate },
    { enabled: waterEnabled && bbox !== null, staleTime: 15 * 60 * 1000 }
  );
  const groundwaterQuery = trpc.environmental.getGroundwater.useQuery(
    { bbox: bbox ?? "-180,-90,180,90", date: requestDate },
    { enabled: waterEnabled && bbox !== null, staleTime: 60 * 60 * 1000 }
  );

  const vegetationEnabled = layerVisibility.vegetation;
  // The measured NDVI grid, read from the warehouse: one cell per sampling-grid square,
  // carrying that cell's newest Sentinel-2 reading. Distinct from the GIBS raster
  // VegetationLayer also draws -- that one is a global 8-day composite this platform
  // proxies, this one is the 184,409-row series this platform ingested. Sentinel-2 yields
  // at most one clear reading per cell every few days, so the hour-long staleTime matches
  // the groundwater/watershed cadence rather than the 15-minute observation feeds. A named
  // day slides that per-cell window to end there instead of at now.
  const vegetationQuery = trpc.environmental.getVegetationIndex.useQuery(
    { bbox: bbox ?? "-180,-90,180,90", date: requestDate },
    { enabled: vegetationEnabled && bbox !== null, staleTime: 60 * 60 * 1000 }
  );
  const vegetationGeoJSON: GeoJSON.FeatureCollection =
    vegetationQuery.data ?? EMPTY_FEATURE_COLLECTION;

  // HUC12 boundaries and SSURGO map units are proxied live from USGS/USDA per viewport
  // rather than published to the warehouse, so they carry no slider day: both endpoints
  // answer for a bbox alone. Both are polygon feeds an order of magnitude heavier than
  // the point layers above, which is why neither is fetched unless its own toggle is on.
  // Key, fallback bbox, staleTime and retry live in the shared hooks, which WaterPanel
  // and SoilPanel call too -- see src/lib/server/AGENTS.md §proxied-viewport-queries.
  const watershedsVisible = layerVisibility.watersheds;
  const watershedQuery = useWatershedsQuery(bbox, { enabled: watershedsVisible });
  // WaterLayer owns the "watersheds" source and creates it whenever this prop is a
  // collection, so an empty one -- not null -- is what a switched-off watershed layer
  // sends: `setData([])` empties the polygons, whereas null would leave the last
  // viewport's boundaries stranded on the map with nothing to clear them.
  const watershedsGeoJSON =
    watershedsVisible && watershedQuery.data ? watershedQuery.data : EMPTY_FEATURE_COLLECTION;

  const soilSurveyVisible = layerVisibility["soil-survey"];
  const soilSurveyQuery = useSoilSurveyQuery(bbox, { enabled: soilSurveyVisible });
  // Only the features are drawable: a truncated view and an upstream fault both reach the
  // map as polygons that stop, so the collection's truncated/availability pair is read by
  // SoilPanel instead, from this same query key. See src/lib/server/AGENTS.md §soil-survey.
  const soilSurveyGeoJSON = soilSurveyQuery.data ?? EMPTY_FEATURE_COLLECTION;

  const weatherEnabled = layerVisibility.weather;
  // Reads every published observation across the viewport bbox -- not just the
  // nearest one -- so the wind layer reflects the full spread of
  // warehouse-backed samples instead of a single point.
  const weatherQuery = trpc.wildfire.getWeatherForBbox.useQuery(
    { bbox: bbox ?? "-180,-90,180,90", date: requestDate },
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

  // True once the CURRENT style has actually finished loading, per isStyleLoaded() --
  // not merely "style.load fired". isStyleLoaded() also requires every source's tiles
  // to be in, so it can still read false the instant style.load fires; styledata fires
  // repeatedly as those tiles land and lets this catch up. Recomputing from the live
  // map on every event (rather than setting it true once) is also what makes a basemap
  // swap self-resetting: setState()'s diff path (every swap after the first) fires
  // style.load synchronously with no separate "started loading" event, but it also
  // invalidates isStyleLoaded() until the new style's sources finish, so the very same
  // recompute reads false again and then true once the new style settles.
  const [styleReady, setStyleReady] = useState(false);

  // Registered once per map so it keeps its place in the listener queue -- see
  // src/components/map/AGENTS.md "Style.load listener order". The direct
  // applyVisibility call here is the basemap-swap safety net and must not start
  // depending on styleReady: it already runs on every style.load, synchronous or not.
  // The slider's day must never enter these deps either: the queries above own the day, and
  // listing it here would re-register the handler on every scrub, moving it behind
  // ServiceAreaLayer's and dropping the dimming mask on top of the data pins.
  useEffect(() => {
    if (!map) return;
    const mapInstance = map;
    const onStyleLoad = () => {
      applyVisibility(mapInstance, layerVisibilityRef.current);
      // `isStyleLoaded()` is typed `boolean | void`; coerce so this stays a boolean state.
      setStyleReady(!!mapInstance.isStyleLoaded());
    };
    const onStyleData = () => setStyleReady(!!mapInstance.isStyleLoaded());

    mapInstance.on("style.load", onStyleLoad);
    mapInstance.on("styledata", onStyleData);
    return () => {
      mapInstance.off("style.load", onStyleLoad);
      mapInstance.off("styledata", onStyleData);
    };
  }, [map, applyVisibility]);

  // Apply toggles once the style is actually ready, and again whenever styleReady
  // flips true -- without styleReady in the deps, this ran once on first paint while
  // isStyleLoaded() was still false, no-opped, and had nothing left to re-trigger it
  // once the style caught up (see src/components/map/AGENTS.md and the bug this fixes).
  useEffect(() => {
    if (!map || !map.isStyleLoaded()) return;
    applyVisibility(map, layerVisibility);
  }, [map, layerVisibility, applyVisibility, styleReady]);

  if (!map) return null;

  return (
    <>
      <FireLayer map={map} visible={layerVisibility.fire} geojson={fireData.data} />
      {/* One component owns both the gauge points and the watershed polygons, so it stays
          mounted while either toggle is on; each feed is emptied independently when its
          own switch is off. */}
      <WaterLayer
        map={map}
        gauges={streamflowQuery.data ?? []}
        wells={groundwaterQuery.data ?? []}
        watershedsGeoJSON={watershedsGeoJSON}
        visible={waterEnabled}
        watershedsVisible={watershedsVisible}
      />
      <DroughtLayer map={map} geojson={droughtGeoJSON} visible={layerVisibility.drought} />
      <VegetationLayer
        map={map}
        visible={vegetationEnabled}
        geojson={vegetationGeoJSON}
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
      <SoilSurveyLayer map={map} geojson={soilSurveyGeoJSON} visible={soilSurveyVisible} />
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
