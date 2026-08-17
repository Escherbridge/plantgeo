"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import dynamic from "next/dynamic";
import { keepPreviousData } from "@tanstack/react-query";
import { useMap } from "@/lib/map/map-context";
import {
  useDebouncedLayerDay,
  useLayerOpacities,
  useLayerVisibility,
  useSoilDisplayMode,
  useVegetationDisplayMode,
  type LayerVisibility,
} from "@/lib/map/layer-toggle-context";
import { scaleOpacityValue, styleLayerOpacityTargets } from "@/lib/map/layer-opacity";
import { useFireData } from "@/hooks/useFireData";
import {
  useSoilFieldQuery,
  useSoilSurveyQuery,
  useViewportBounds,
} from "@/hooks/useViewportProxiedLayers";
import { trpc } from "@/lib/trpc/client";
import {
  LAYER_REGISTRY,
  styleBackedLayerEntries,
  type LayerToggleId,
} from "@/lib/map/layer-registry";
import {
  DATE_FILTERABLE_TILE_LAYER_TOGGLE_IDS,
  tileLayerDateFilter,
} from "@/lib/map/tile-layer-date-filter";
import { useMapStore } from "@/stores/map-store";
import { hasSelectableDay, useTimeSliderStore } from "@/stores/time-slider-store";
import {
  drawnDayFlagsFromQuery,
  usePublishedDrawnLayerDays,
  type LiveLayerDayReport,
} from "@/stores/useMetricAtDate";
import { isRenderableWeatherObservation } from "@/lib/environmental/weather";
import type { WeatherPoint } from "@/components/map/layers/WeatherLayer";

const EMPTY_FEATURE_COLLECTION: GeoJSON.FeatureCollection = {
  type: "FeatureCollection",
  features: [],
};

/**
 * The style-baked tile toggles this component holds a day for.
 *
 * `DATE_FILTERABLE_TILE_LAYER_TOGGLE_IDS` is typed `readonly LayerToggleId[]`, so it cannot
 * narrow anything on its own; this tuple is what makes `dateFilterableLayerDays` below a record
 * the compiler checks against exactly the toggles a `useDebouncedLayerDay` call exists for.
 * A tuple with `satisfies` rather than a hand-written union, so the list and the type cannot
 * drift from each other -- there is now one place to add a toggle here instead of two.
 *
 * It can still drift from the EXPORTED constant, which is the drift that matters: an id added
 * there without a hook here would leave that layer with no filter at all, and an unfiltered
 * date-filterable layer draws its whole published record while its row's slider says otherwise
 * -- the map showing four years of perimeters under a control that reads one day. Two things
 * catch it: `applyDateFilter` reports the gap rather than skipping it silently, and the test
 * "filters every date-filterable tile layer on that layer's own day" walks the exported
 * constant, so the gap fails a case rather than shipping.
 */
const DATE_FILTERABLE_TOGGLES_WITH_A_DAY_HERE = [
  "fire-perimeters",
  "evacuation-zones",
  "burn-severity",
  "sensors",
] as const satisfies readonly LayerToggleId[];

type DateFilterableToggleId = (typeof DATE_FILTERABLE_TOGGLES_WITH_A_DAY_HERE)[number];

/** True once the two lists have been compared; the drift is structural, so one report is enough. */
let dateFilterableToggleDriftReported = false;

/**
 * Names any disagreement between the exported constant and the toggles wired here.
 *
 * Both directions are a defect and neither shows up as one. A toggle the constant lists and
 * this component holds no day for draws its whole published record under a row whose slider
 * claims a single day; a toggle wired here that the constant does not list has a day nobody
 * ever applies, so its slider moves and its map does not. Reported rather than thrown: a
 * console line in the browser and in the test run is enough to lose an hour to, whereas
 * throwing would take the whole map down for a filter.
 */
function reportDateFilterableToggleDrift(): void {
  if (dateFilterableToggleDriftReported) return;
  dateFilterableToggleDriftReported = true;
  const wiredHere = new Set<string>(DATE_FILTERABLE_TOGGLES_WITH_A_DAY_HERE);
  for (const toggleId of DATE_FILTERABLE_TILE_LAYER_TOGGLE_IDS) {
    if (wiredHere.has(toggleId)) continue;
    console.error(
      `LayerManager holds no day for the date-filterable toggle "${toggleId}", so its style ` +
        `layers draw their whole published record with no upper bound while its row offers a ` +
        `day. Add a useDebouncedLayerDay("${toggleId}") call, an entry in ` +
        `DATE_FILTERABLE_TOGGLES_WITH_A_DAY_HERE and one in dateFilterableLayerDays.`
    );
  }
  const listedAsFilterable = new Set<string>(DATE_FILTERABLE_TILE_LAYER_TOGGLE_IDS);
  for (const toggleId of DATE_FILTERABLE_TOGGLES_WITH_A_DAY_HERE) {
    if (listedAsFilterable.has(toggleId)) continue;
    console.error(
      `LayerManager holds a day for "${toggleId}", which ` +
        `DATE_FILTERABLE_TILE_LAYER_TOGGLE_IDS does not list, so nothing ever applies it: ` +
        `that row's slider moves and its map does not.`
    );
  }
}

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
const SoilFieldLayer = dynamic(
  () => import("@/components/map/layers/SoilFieldLayer").then((m) => ({ default: m.SoilFieldLayer })),
  { ssr: false }
);
const ClimateFieldLayers = dynamic(
  () => import("@/components/map/layers/ClimateFieldLayers").then((m) => ({ default: m.ClimateFieldLayers })),
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
const QueryPointLayer = dynamic(
  () => import("@/components/map/layers/QueryPointLayer").then((m) => ({ default: m.QueryPointLayer })),
  { ssr: false }
);
const StrategyLayer = dynamic(
  () => import("@/components/map/layers/StrategyLayer").then((m) => ({ default: m.StrategyLayer })),
  { ssr: false }
);

export default function LayerManager() {
  const map = useMap();
  // One read of the toggle context covers every layer below: which are switched on, and
  // the mode each draws in. Nothing here reads a toggle id as a bare string.
  const layerVisibility = useLayerVisibility();
  // The per-layer opacity MULTIPLIER for every registry layer. Style-baked layers are applied
  // from here (nothing else owns them); component-mounted layers take theirs as an
  // `opacityScale` prop and fold it into whatever they already compute -- one writer per
  // (layer, paint property), always. See src/lib/map/layer-opacity.ts.
  const layerOpacity = useLayerOpacities();
  const vegetationMode = useVegetationDisplayMode();
  const soilMode = useSoilDisplayMode();
  // Shared with DockDetails: one derivation, so the map and the dock's details regions key on
  // one bbox.
  const { zoom, bbox } = useViewportBounds();
  // Written only by DockDetails' capture hook (SoilDetailsBody); drawn here because the map
  // owns its layers.
  const queryPoint = useMapStore((state) => state.queryPoint);

  // One settled day per LAYER, never one for the map: since 2026-08-09 each row scrubs its own
  // axis and opens on its own `latestObservedDate`, so there is no map-wide day left to read.
  // `requestDate` is undefined whenever THAT layer's day is the server's today, which keeps the
  // hot path on the exact dateless query key -- and the exact server query -- it has always
  // used, so first paint never fetches the same day twice. Settled rather than raw: a
  // day-granular scrub writes on every pointer tick.
  //
  // One call per layer rather than a loop, for the same reason the three soil fields below are
  // three calls: hooks cannot be called from one. The upside over the single global read this
  // replaces is that a scrub on one row now re-runs one of these, not all of them.
  const fireDay = useDebouncedLayerDay("fire");
  const droughtDay = useDebouncedLayerDay("drought");
  const waterDay = useDebouncedLayerDay("water");
  const vegetationDay = useDebouncedLayerDay("vegetation");
  const soilMoistureDay = useDebouncedLayerDay("soil-moisture");
  const soilTemperatureDay = useDebouncedLayerDay("soil-temperature");
  const soilVpdDay = useDebouncedLayerDay("soil-vpd");
  // No climate day here: the nine NASA POWER rows each settle their own inside
  // `ClimateFieldLayers`, which is the point of giving each signal its own slider.
  const weatherDay = useDebouncedLayerDay("weather");
  // The four style-baked tile toggles. They are read here alongside the component-mounted
  // layers rather than inside their own children because each is a ROW in the dock with a
  // slider of its own, exactly like the layers above -- being drawn by a style filter instead
  // of by a React component changes how the day is applied, not whose day it is.
  const firePerimetersDay = useDebouncedLayerDay("fire-perimeters");
  const evacuationZonesDay = useDebouncedLayerDay("evacuation-zones");
  const burnSeverityDay = useDebouncedLayerDay("burn-severity");
  const sensorsDay = useDebouncedLayerDay("sensors");

  // Whether each tile toggle has a day a user can actually CHOOSE -- the same question, asked of
  // the same function, that decides whether its row draws a slider (LayerRow.tsx). A layer with
  // no selectable day must carry no date filter: `sliderDomain` refuses a snapshot, so
  // evacuation-zones and sensors get no control. burn-severity is NOT one of them -- the read
  // model declares it `event` (environmental-read-model.ts, LAYER_TEMPORAL_KINDS), so it draws a
  // slider and must be filtered. It was in that list only while it was absent from that table and
  // inherited `snapshot`, which is why an unbounded MTBS layer used to draw every scar through
  // 2026 beneath perimeters scrubbed to 2024. Filtering a genuine snapshot anyway installed
  // `["<=", ["get","observed_day"], latestObservedDate]` on rows with nothing to change it with,
  // and `latestObservedDate` is by contract the newest day AT OR ABOVE the density floor -- so
  // through every partially-ingested live-edge day, which is the normal state of a running
  // ingest lane, every sensor and evacuation zone observed that day was filtered off the map
  // with no slider, no date and no caption to say why.
  //
  // Selected as BOOLEANS, so the five-minute capabilities poll re-runs the applier only when an
  // answer actually changes rather than on every fresh payload object.
  const firePerimetersHasSelectableDay = useTimeSliderStore((state) =>
    hasSelectableDay(state.capabilities, "fire-perimeters")
  );
  const evacuationZonesHasSelectableDay = useTimeSliderStore((state) =>
    hasSelectableDay(state.capabilities, "evacuation-zones")
  );
  const burnSeverityHasSelectableDay = useTimeSliderStore((state) =>
    hasSelectableDay(state.capabilities, "burn-severity")
  );
  const sensorsHasSelectableDay = useTimeSliderStore((state) =>
    hasSelectableDay(state.capabilities, "sensors")
  );

  const fireData = useFireData(layerVisibility.fire, fireDay.requestDate);
  // `placeholderData: keepPreviousData` on every dated feed below: each keys on a day AND a
  // bbox, so without it every settled scrub and every pan blanked the layer for a full round
  // trip. Legal only because `usePublishedDrawnLayerDays` below labels the retained frame --
  // see src/components/map/AGENTS.md "A layer must not blank between days". Never one without
  // the other.
  //
  // tRPC keys `undefined` input differently from an object, so the dateless case must stay
  // literally undefined here rather than becoming `{ date: undefined }`.
  const droughtQuery = trpc.environmental.getDroughtClassification.useQuery(
    droughtDay.requestDate === undefined ? undefined : { date: droughtDay.requestDate },
    { enabled: layerVisibility.drought, placeholderData: keepPreviousData }
  );
  const droughtGeoJSON = droughtQuery.data ?? EMPTY_FEATURE_COLLECTION;
  const waterEnabled = layerVisibility.water;
  // Both feeds take `water`'s day, because both are drawn by the one `water` toggle and so by
  // the one row that carries a slider for them. Gauges and wells sharing a day is a property of
  // there being a single control over them, not an assumption about the two upstreams.
  const streamflowQuery = trpc.environmental.getStreamflow.useQuery(
    { bbox: bbox ?? "-180,-90,180,90", date: waterDay.requestDate },
    {
      enabled: waterEnabled && bbox !== null,
      staleTime: 15 * 60 * 1000,
      placeholderData: keepPreviousData,
    }
  );
  const groundwaterQuery = trpc.environmental.getGroundwater.useQuery(
    { bbox: bbox ?? "-180,-90,180,90", date: waterDay.requestDate },
    {
      enabled: waterEnabled && bbox !== null,
      staleTime: 60 * 60 * 1000,
      placeholderData: keepPreviousData,
    }
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
    { bbox: bbox ?? "-180,-90,180,90", date: vegetationDay.requestDate },
    {
      enabled: vegetationEnabled && bbox !== null,
      staleTime: 60 * 60 * 1000,
      placeholderData: keepPreviousData,
    }
  );
  const vegetationGeoJSON: GeoJSON.FeatureCollection =
    vegetationQuery.data ?? EMPTY_FEATURE_COLLECTION;

  // SSURGO map units are proxied live from USDA per viewport rather than published to the
  // warehouse, so they carry no slider day: the endpoint answers for a bbox alone. It is a
  // polygon feed an order of magnitude heavier than the point layers above, which is why it
  // is not fetched unless its own toggle is on. Key, fallback bbox, staleTime and retry live
  // in the shared hook, which SoilDetails calls too -- see src/lib/server/AGENTS.md
  // §proxied-viewport-queries. (HUC12 watersheds used to sit here; they are now style-baked
  // Martin tiles and reach the map through applyVisibility below, like every other tile layer.)
  const soilSurveyVisible = layerVisibility["soil-survey"];
  // `zoom` is what selects the survey's render granularity server-side. Omitting it -- which
  // both call sites did until now -- resolves to the detail tier, whose 0.02 sq-deg ceiling
  // the tRPC input then rejects at any ordinary zoom, so the layer only ever drew when zoomed
  // in past ~13. Passed from the same `useViewportBounds()` derivation SoilDetails reads it
  // from, so the map and the details region stay on ONE react-query entry.
  const soilSurveyQuery = useSoilSurveyQuery(bbox, { enabled: soilSurveyVisible, zoom });
  // Only the features are drawable: a truncated view and an upstream fault both reach the
  // map as polygons that stop, so the collection's truncated/availability pair is read by
  // SoilDetails instead, from this same query key. See src/lib/server/AGENTS.md §soil-survey.
  const soilSurveyGeoJSON = soilSurveyQuery.data ?? EMPTY_FEATURE_COLLECTION;

  // The three ERA5-Land soil fields. `zoom` is not a hint here -- it selects the server-side
  // aggregation tier, so zooming out makes the answer SMALLER (isobands over a coarse
  // lattice) rather than shipping 1,568 squares. Each takes ITS OWN row's settled day, like
  // every other warehouse-backed feed; the depth is the panel's, and neither is a second
  // time control. staleTime matches vegetation's: a reanalysis archive day is immutable.
  //
  // Three calls rather than a loop: hooks cannot be called from one, and `measure` is in the
  // query key, so each field holds a separate cache entry and any subset can be on at once.
  // Three separate days for the same reason -- they are three toggles, and a reader who scrubs
  // moisture back a week has said nothing about temperature.
  const soilMoistureVisible = layerVisibility["soil-moisture"];
  const soilMoistureQuery = useSoilFieldQuery(bbox, {
    enabled: soilMoistureVisible,
    measure: "moisture",
    date: soilMoistureDay.requestDate,
    depth: soilMode.fieldDepth.moisture,
    zoom,
  });
  const soilMoistureGeoJSON: GeoJSON.FeatureCollection =
    soilMoistureQuery.data ?? EMPTY_FEATURE_COLLECTION;

  const soilTemperatureVisible = layerVisibility["soil-temperature"];
  const soilTemperatureQuery = useSoilFieldQuery(bbox, {
    enabled: soilTemperatureVisible,
    measure: "temperature",
    date: soilTemperatureDay.requestDate,
    depth: soilMode.fieldDepth.temperature,
    zoom,
  });
  const soilTemperatureGeoJSON: GeoJSON.FeatureCollection =
    soilTemperatureQuery.data ?? EMPTY_FEATURE_COLLECTION;

  const soilVpdVisible = layerVisibility["soil-vpd"];
  const soilVpdQuery = useSoilFieldQuery(bbox, {
    enabled: soilVpdVisible,
    measure: "vpd",
    date: soilVpdDay.requestDate,
    depth: soilMode.fieldDepth.vpd,
    zoom,
  });
  const soilVpdGeoJSON: GeoJSON.FeatureCollection =
    soilVpdQuery.data ?? EMPTY_FEATURE_COLLECTION;

  // The nine NASA POWER rows read and draw themselves inside `ClimateFieldLayers` below: each
  // signal owns a toggle, a slider and a day, so there is no single climate query or climate
  // day left for this component to hold. Everything it would have kept here -- eighteen hooks'
  // worth -- lives one component down, beside the layer it feeds.

  const weatherEnabled = layerVisibility.weather;
  // Reads every published observation across the viewport bbox -- not just the
  // nearest one -- so the wind layer reflects the full spread of
  // warehouse-backed samples instead of a single point.
  const weatherQuery = trpc.wildfire.getWeatherForBbox.useQuery(
    { bbox: bbox ?? "-180,-90,180,90", date: weatherDay.requestDate },
    {
      enabled: weatherEnabled && bbox !== null,
      staleTime: 15 * 60 * 1000,
      placeholderData: keepPreviousData,
    }
  );
  // Every rendered field must still be measured -- nothing is back-filled with a zero the
  // upstream never reported -- but completeness is now judged PER DRAWN LAYER rather than
  // across the whole observation. The toggle paints two things: wind arrows, which need
  // windSpeed and windDirection, and temperature dots, which need temperature. Humidity is
  // drawn by neither and only captions the tooltip, so requiring it here (as this filter did
  // until 2026-08-08) dropped stations that had everything the map actually draws. The nulls
  // themselves never reach a painted expression: WeatherLayer carries a `hasWind`/
  // `hasTemperature` flag per feature and each layer filters on its own.
  //
  // `isRenderableWeatherObservation` (src/lib/environmental/weather.ts) is the single source
  // of this rule: `getPublishedWeatherForBbox` applies the exact same relaxed check
  // server-side, so this is a client-side re-check of an already-complete feed, not a second
  // opinion that could drift from the server's.
  // Memoized because this component re-renders on every viewport tick.
  const weatherData = useMemo<WeatherPoint[]>(
    () =>
      (weatherQuery.data ?? [])
        .filter(isRenderableWeatherObservation)
        .map((observation) => ({
          coordinates: [observation.lon, observation.lat],
          windSpeed: observation.windSpeed,
          windDirection: observation.windDirection,
          temperature: observation.temperature,
          humidity: observation.humidity,
          observedAt: observation.observedAt,
        })),
    [weatherQuery.data]
  );

  // What each live layer is actually DRAWING, for the surfaces that caption the map. The other
  // half of `keepPreviousData` above; see src/components/map/AGENTS.md "A layer must not blank
  // between days". `settledDate`, not `requestDate` -- a caption cannot state an omission.
  //
  // Nine ids, never the nine NASA POWER signals: each `ClimateSignalLayer` publishes its own,
  // because it owns its own read. Publishers must stay disjoint.
  const liveLayerDayReports: LiveLayerDayReport[] = [
    {
      layerId: "fire",
      isDrawn: layerVisibility.fire,
      requestedDate: fireDay.settledDate,
      // `useFireData` is not a react-query read, but it retains the previous day's detections
      // across a 304, a failed fetch and a date change, and states that as
      // `isStaleForRequestedDate` -- whose own doc says a caller must read it before captioning
      // `data`. `count` is what separates a retained frame from nothing having loaded at all,
      // which `isStaleForRequestedDate` alone does not.
      isFetching: fireData.isLoading,
      hasLandedForRequestedDate: fireData.isStaleForRequestedDate !== true,
      isShowingPreviousDay: fireData.isStaleForRequestedDate === true && fireData.count > 0,
    },
    {
      layerId: "drought",
      isDrawn: layerVisibility.drought,
      requestedDate: droughtDay.settledDate,
      ...drawnDayFlagsFromQuery(droughtQuery),
    },
    {
      // One row over two upstreams: the day is drawn only once BOTH have answered for it.
      layerId: "water",
      isDrawn: waterEnabled,
      requestedDate: waterDay.settledDate,
      isFetching: streamflowQuery.isFetching === true || groundwaterQuery.isFetching === true,
      hasLandedForRequestedDate:
        drawnDayFlagsFromQuery(streamflowQuery).hasLandedForRequestedDate &&
        drawnDayFlagsFromQuery(groundwaterQuery).hasLandedForRequestedDate,
      isShowingPreviousDay:
        streamflowQuery.isPlaceholderData === true || groundwaterQuery.isPlaceholderData === true,
    },
    {
      layerId: "vegetation",
      isDrawn: vegetationEnabled,
      requestedDate: vegetationDay.settledDate,
      ...drawnDayFlagsFromQuery(vegetationQuery),
    },
    {
      // SSURGO is proxied per viewport and its key holds no date, so it has no day of its own to
      // draw: a retained frame here is a different VIEWPORT, never a different day.
      layerId: "soil-survey",
      isDrawn: soilSurveyVisible,
      requestedDate: null,
      ...drawnDayFlagsFromQuery(soilSurveyQuery),
      isShowingPreviousDay: false,
    },
    {
      layerId: "soil-moisture",
      isDrawn: soilMoistureVisible,
      requestedDate: soilMoistureDay.settledDate,
      ...drawnDayFlagsFromQuery(soilMoistureQuery),
    },
    {
      layerId: "soil-temperature",
      isDrawn: soilTemperatureVisible,
      requestedDate: soilTemperatureDay.settledDate,
      ...drawnDayFlagsFromQuery(soilTemperatureQuery),
    },
    {
      layerId: "soil-vpd",
      isDrawn: soilVpdVisible,
      requestedDate: soilVpdDay.settledDate,
      ...drawnDayFlagsFromQuery(soilVpdQuery),
    },
    {
      layerId: "weather",
      isDrawn: weatherEnabled,
      requestedDate: weatherDay.settledDate,
      ...drawnDayFlagsFromQuery(weatherQuery),
    },
  ];
  usePublishedDrawnLayerDays("layer-manager", liveLayerDayReports);

  // Sync visibility of style-baked Martin layers (fire-perimeters/interventions/
  // watersheds) with activeLayers -- these are static layers added via getStyle(),
  // not React-mounted components, so they need setLayoutProperty instead of an
  // unmount/remount cycle.
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

  // The opacity sibling of applyVisibility, and for the same reason: a style-baked layer has
  // no React component to fold a multiplier into, so this is its single writer. It reaches
  // ONLY the (layer, property) pairs styleLayerOpacityTargets() derives from the registry
  // crossed with getLayers() -- basemap chrome, the service-area mask and every
  // component-added layer are structurally out of reach. The value is always the AUTHORED
  // base scaled, never an absolute, so re-running it is idempotent and a factor of 1 rewrites
  // exactly what the style declared.
  const applyOpacity = useCallback(
    (
      mapInstance: NonNullable<typeof map>,
      currentOpacity: Record<LayerToggleId, number>
    ) => {
      for (const target of styleLayerOpacityTargets()) {
        if (!mapInstance.getLayer(target.layerId)) continue;
        mapInstance.setPaintProperty(
          target.layerId,
          target.property,
          scaleOpacityValue(target.base, currentOpacity[target.toggleId] ?? 1)
        );
      }
    },
    []
  );

  // Every date-filterable tile toggle's own settled day, in one object so the applier and the
  // style.load safety net read one value. `settledDate`, NOT `requestDate`: `requestDate` is
  // deliberately undefined at the server's today so a query keys the same as a dateless one,
  // and borrowing that here would drop the filter entirely on any layer sitting on today --
  // restoring the undated behaviour where every published row drew at every point on the axis.
  // A style filter costs no request, so there is nothing to save by omitting the day.
  //
  // `null` is a DAY WE MAY NOT FILTER ON rather than a missing value, and it is the whole of
  // F1's fix: a layer whose row offers no day to pick must be drawn unfiltered, because a
  // filter is a claim the reader has no way to make and no way to see. `undefined` stays
  // distinct from it and means something else entirely -- a toggle listed as filterable that
  // this component was never wired for. See `applyDateFilter`.
  const dateFilterableLayerDays = useMemo<Record<DateFilterableToggleId, string | null>>(
    () => ({
      "fire-perimeters": firePerimetersHasSelectableDay ? firePerimetersDay.settledDate : null,
      "evacuation-zones": evacuationZonesHasSelectableDay
        ? evacuationZonesDay.settledDate
        : null,
      "burn-severity": burnSeverityHasSelectableDay ? burnSeverityDay.settledDate : null,
      sensors: sensorsHasSelectableDay ? sensorsDay.settledDate : null,
    }),
    [
      firePerimetersDay.settledDate,
      evacuationZonesDay.settledDate,
      burnSeverityDay.settledDate,
      sensorsDay.settledDate,
      firePerimetersHasSelectableDay,
      evacuationZonesHasSelectableDay,
      burnSeverityHasSelectableDay,
      sensorsHasSelectableDay,
    ]
  );

  // Puts each style-baked Martin layer on ITS OWN row's slider. They are not React-mounted, so
  // they cannot take a date as a prop the way every layer below does; migration 0015 emits
  // `observed_day` on each feature and this applies the matching style filter per layer.
  // Re-filtering costs no requests -- the tiles are already in the browser -- so unlike the
  // queries above this reads the SETTLED day only because there is no point re-running it per
  // pointer tick.
  //
  // One filter per toggle rather than one fanned across all of them: `tileLayerDateFilter`
  // always took a single day for a single layer, and fanning it was only ever the global
  // slider's shape leaking down here.
  //
  // Every listed toggle is WRITTEN on every pass, including the ones that end up unfiltered.
  // Clearing has to be as explicit as filtering: a basemap swap rebuilds each style layer from
  // its authored spec, so "leave it alone" and "make sure it carries no day" are the same
  // instruction only until the first swap -- and a layer that loses its selectable day (a
  // capabilities payload that reclassifies it, or one that fails to arrive) would otherwise keep
  // a filter nothing can move.
  const applyDateFilter = useCallback(
    (
      mapInstance: NonNullable<typeof map>,
      days: Record<DateFilterableToggleId, string | null>
    ) => {
      reportDateFilterableToggleDrift();
      for (const toggleId of DATE_FILTERABLE_TILE_LAYER_TOGGLE_IDS) {
        // Three states, not two. A day filters; `null` -- no selectable day -- clears, which is
        // exactly what the layer drew before it had a slider at all; `undefined` is the wiring
        // gap `reportDateFilterableToggleDrift` just named, and clears too, because there is no
        // day to honour. Skipping it used to read as the cautious choice and was not: the layer
        // is rebuilt filterless on the next style load either way, so the gap's real consequence
        // is the whole published record under a row that claims one day.
        const day = (days as Record<string, string | null | undefined>)[toggleId];
        const filter = tileLayerDateFilter(day ?? null);
        for (const layerId of LAYER_REGISTRY[toggleId].styleLayerIds) {
          if (mapInstance.getLayer(layerId)) mapInstance.setFilter(layerId, filter ?? undefined);
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

  // Same ref discipline, and for the same reason: a basemap swap rebuilds every style layer,
  // so the style.load handler has to reapply the CURRENT days without listing them as a
  // dependency. Listing them would re-register that handler on every settled scrub and move it
  // behind ServiceAreaLayer's in the listener queue -- the bug the AGENTS.md note describes.
  // More load-bearing than it was: with per-layer days there are now several of these moving
  // independently, so the handler would re-register that much more often.
  const filterDaysRef = useRef(dateFilterableLayerDays);
  useEffect(() => {
    filterDaysRef.current = dateFilterableLayerDays;
  }, [dateFilterableLayerDays]);

  // The third instance of the same discipline, and the one it matters most for: an opacity
  // slider fires far more often than a settled scrub, so listing the record in the style.load
  // deps below would re-register that handler on nearly every pointer tick and move it behind
  // ServiceAreaLayer's -- dropping the dimming mask on top of the data pins. See
  // src/components/map/AGENTS.md "Style.load listener order".
  const layerOpacityRef = useRef(layerOpacity);
  useEffect(() => {
    layerOpacityRef.current = layerOpacity;
  }, [layerOpacity]);

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
    // One pending frame for the convergence pass below, cancelled on teardown. See
    // src/components/map/AGENTS.md for what actually emits `styledata` and why that makes the
    // coalescing load-bearing.
    let convergenceFrame: number | null = null;
    const onStyleLoad = () => {
      applyVisibility(mapInstance, layerVisibilityRef.current);
      applyDateFilter(mapInstance, filterDaysRef.current);
      // A basemap swap rebuilds every style layer from its authored paint, so the multiplier
      // has to be re-applied here or a dimmed layer silently snaps back to full strength.
      applyOpacity(mapInstance, layerOpacityRef.current);
      // `isStyleLoaded()` is typed `boolean | void`; coerce so this stays a boolean state.
      setStyleReady(!!mapInstance.isStyleLoaded());
    };
    // All three appliers converge here, not only on style.load: a style layer added after this
    // style loaded (every component that adds its own does so on `style.load`) would otherwise
    // keep its authored visibility, filter and opacity until the next basemap swap. See
    // src/components/map/AGENTS.md "`isStyleLoaded()` is a signal to retry on".
    //
    // Visibility runs unthrottled; the filter and the opacity multiplier build an expression per
    // style layer before MapLibre gets to compare it, so they share one frame.
    const onStyleData = () => {
      applyVisibility(mapInstance, layerVisibilityRef.current);
      setStyleReady(!!mapInstance.isStyleLoaded());
      if (convergenceFrame !== null) return;
      convergenceFrame = requestAnimationFrame(() => {
        convergenceFrame = null;
        applyDateFilter(mapInstance, filterDaysRef.current);
        applyOpacity(mapInstance, layerOpacityRef.current);
      });
    };

    mapInstance.on("style.load", onStyleLoad);
    mapInstance.on("styledata", onStyleData);
    return () => {
      if (convergenceFrame !== null) cancelAnimationFrame(convergenceFrame);
      mapInstance.off("style.load", onStyleLoad);
      mapInstance.off("styledata", onStyleData);
    };
    // applyOpacity is a stable useCallback; the opacity RECORD must never appear here.
  }, [map, applyVisibility, applyDateFilter, applyOpacity]);

  // Apply toggles once the style is actually ready, and again whenever styleReady
  // flips true -- without styleReady in the deps, this ran once on first paint while
  // isStyleLoaded() was still false, no-opped, and had nothing left to re-trigger it
  // once the style caught up (see src/components/map/AGENTS.md and the bug this fixes).
  // Deliberately NOT gated on isStyleLoaded(). `applyVisibility` already guards every write
  // with `getLayer()`, so running it against a half-built style is a no-op per missing layer
  // rather than an error -- while GATING it there means a reader's click on the eye is silently
  // dropped, with nothing left to re-trigger it, for as long as one source fails to settle
  // (styleReady never changes, so this effect never re-runs). A dead toggle is a worse failure
  // than a redundant setLayoutProperty. styleReady stays in the deps so the pass still repeats
  // as the style becomes ready.
  useEffect(() => {
    if (!map) return;
    applyVisibility(map, layerVisibility);
  }, [map, layerVisibility, applyVisibility, styleReady]);

  // This effect is what actually answers a scrub, and it is NOT gated on isStyleLoaded() -- see
  // src/components/map/AGENTS.md "`isStyleLoaded()` is a signal to retry on, never a gate to
  // drop writes behind" for the outage that gate caused. Unthrottled on purpose: it responds to
  // the reader's own input, and a settled day arrives at most once per SCRUB_SETTLE_MS anyway.
  useEffect(() => {
    if (!map) return;
    applyDateFilter(map, dateFilterableLayerDays);
  }, [map, dateFilterableLayerDays, applyDateFilter, styleReady]);

  // Same shape, plus one animation-frame of coalescing: an opacity drag fires per pointer tick
  // and rebuilds an expression per style layer. Deliberately NOT the slider's settle constant --
  // that exists to coalesce network requests, and opacity issues none, so borrowing it would
  // leave the map visibly trailing the thumb. Ungated for the same reason the filter is.
  useEffect(() => {
    if (!map) return;
    const frame = requestAnimationFrame(() => applyOpacity(map, layerOpacity));
    return () => cancelAnimationFrame(frame);
  }, [map, layerOpacity, applyOpacity, styleReady]);

  // No WebGPU/worker pipeline here, deliberately. One lived here from 2026-08-14 to 2026-08-15
  // and did nothing but cost: every result it computed was discarded (`void`), so the packing
  // pass and the GPU readback ran on the MAIN thread, synchronously, on every change to fire,
  // drought or vegetation data -- on the same render path this component exists to keep clear.
  // `@/workers/layer-processor.worker` is a worker module and is never instantiated as one; it
  // was imported directly, which also installed its module-scope `message` listener on `window`
  // (on the main thread `self` IS `window`). Nothing here may import it. See
  // `useActionNetworkFeatures` for the shape a real worker takes in this codebase.

  if (!map) return null;

  return (
    <>
      {/* Every child below takes `opacityScale`, never an absolute opacity: the component
          keeps owning its authored base (and, for vegetation, its mode gating) and folds the
          reader's multiplier into it, so nothing outside ever writes these paint properties.
          The scalar is passed per child, so moving `fire` cannot re-run VegetationLayer's
          effects. */}
      <FireLayer
        map={map}
        visible={layerVisibility.fire}
        geojson={fireData.data}
        opacityScale={layerOpacity.fire}
      />
      <WaterLayer
        map={map}
        gauges={streamflowQuery.data ?? []}
        wells={groundwaterQuery.data ?? []}
        visible={waterEnabled}
        opacityScale={layerOpacity.water}
      />
      <DroughtLayer
        map={map}
        geojson={droughtGeoJSON}
        visible={layerVisibility.drought}
        opacityScale={layerOpacity.drought}
      />
      <VegetationLayer
        map={map}
        visible={vegetationEnabled}
        geojson={vegetationGeoJSON}
        mode={vegetationMode.mode}
        year={vegetationMode.year}
        month={vegetationMode.month}
        ndviMode={vegetationMode.ndviMode}
        showNDWI={vegetationMode.showNDWI}
        opacityScale={layerOpacity.vegetation}
      />
      <SoilLayer
        map={map}
        visible={layerVisibility.soil}
        property={soilMode.property}
        opacityScale={layerOpacity.soil}
      />
      <SoilSurveyLayer
        map={map}
        geojson={soilSurveyGeoJSON}
        visible={soilSurveyVisible}
        opacityScale={layerOpacity["soil-survey"]}
      />
      {/* Three independent multipliers where there used to be one `soilMode.opacity` shared
          by the raster and both fields -- dimming the SoilGrids raster necessarily dimmed
          both ERA5-Land measurements. */}
      <SoilFieldLayer
        map={map}
        measure="moisture"
        geojson={soilMoistureGeoJSON}
        opacityScale={layerOpacity["soil-moisture"]}
        visible={soilMoistureVisible}
      />
      <SoilFieldLayer
        map={map}
        measure="temperature"
        geojson={soilTemperatureGeoJSON}
        opacityScale={layerOpacity["soil-temperature"]}
        visible={soilTemperatureVisible}
      />
      <SoilFieldLayer
        map={map}
        measure="vpd"
        geojson={soilVpdGeoJSON}
        opacityScale={layerOpacity["soil-vpd"]}
        visible={soilVpdVisible}
      />
      {/* Nine instances, one per signal, each on its own row's day and in its own form. The
          ERA5-Land fields above get one instance per measure for the same reason: these are
          toggles a reader may have on at once, and one instance cannot hold two days. */}
      <ClimateFieldLayers map={map} bbox={bbox} />
      <DemandHeatmapLayer
        map={map}
        bbox={bbox}
        zoom={zoom}
        visible={layerVisibility["demand-heatmap"] && bbox !== null}
        opacityScale={layerOpacity["demand-heatmap"]}
      />
      <WeatherLayer
        map={map}
        data={weatherData}
        visible={weatherEnabled}
        opacityScale={layerOpacity.weather}
      />
      {/* Not a data layer and so not in the registry: it marks where the user clicked,
          and DockDetails' capture hook (SoilDetailsBody, DockDetails.tsx:100-101) is the
          only thing that ever sets it. */}
      <QueryPointLayer map={map} point={queryPoint} />
      <StrategyLayer map={map} loaded={styleReady} />
    </>
  );
}
