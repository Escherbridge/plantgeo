"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import dynamic from "next/dynamic";
import { useMap } from "@/lib/map/map-context";
import {
  useLayerOpacities,
  useLayerVisibility,
  useSoilDisplayMode,
  useVegetationDisplayMode,
} from "@/lib/map/layer-toggle-context";
import { useParquetFireDetections } from "@/hooks/useParquetFireDetections";
import { useCropCover } from "@/hooks/useCropCover";
import { useViewportBounds } from "@/hooks/useViewportProxiedLayers";
import { trpc } from "@/lib/trpc/client";
import { useInterventionDraftsOverlay } from "@/lib/map/use-intervention-drafts";
import { useInterventionDetailClicks } from "@/lib/map/use-intervention-detail-clicks";
import { InterventionDetailModal } from "@/components/map/InterventionDetailModal";
import { useMapStore } from "@/stores/map-store";
import { usePublishedDrawnLayerDays } from "@/stores/useMetricAtDate";
import type { ParquetFeatureSourceId } from "@/lib/map/sources";
// The orchestration seams this component composes rather than inlines (style review W3,
// S12). Each is a plain module with no MapLibre dependency, so they are static imports
// while the layer COMPONENTS below stay dynamic. One line per seam in
// src/components/map/AGENTS.md §LayerManager structure.
import { useLandContextViewportBoundaries } from "@/components/map/layer-manager/useLandContextViewportBoundaries";
import { buildParquetLayerFaults } from "@/components/map/layer-manager/parquet-layer-faults";
import { useSettledLayerDays } from "@/components/map/layer-manager/useSettledLayerDays";
import {
  useDroughtRead,
  useStyleBakedParquetReads,
  useVegetationRead,
  useWaterReads,
  useWeatherRead,
} from "@/components/map/layer-manager/useParquetLayerReads";
import { useSoilLayerReads } from "@/components/map/layer-manager/useSoilLayerReads";
import { buildLiveLayerDayReports } from "@/components/map/layer-manager/live-layer-day-reports";
import * as styleLayerAppliers from "@/components/map/layer-manager/style-layer-appliers";
import type { DateFilterableToggleId } from "@/components/map/layer-manager/style-layer-appliers";
import { ParquetLayerFaultBanner } from "@/components/map/ParquetLayerFaultBanner";
import { soilRasterToggleId } from "@/lib/map/soil-raster";

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

// The automatic land-context viewport lane's renderer. Its own source and layers, never the click
// lane's -- see the component's module doc for why the two must not share one.
const LandContextViewportLayer = dynamic(
  () =>
    import("@/components/map/layers/LandContextViewportLayer").then((m) => ({
      default: m.LandContextViewportLayer,
    })),
  { ssr: false }
);
const CropCoverLayer = dynamic(
  () => import("@/components/map/land-context/CropCoverLayer").then((module) => ({ default: module.CropCoverLayer })),
  { ssr: false }
);

export default function LayerManager() {
  const map = useMap();
  // One read of the toggle context covers every layer below: which are switched on, and
  // the mode each draws in. Nothing here reads a toggle id as a bare string.
  const layerVisibility = useLayerVisibility();
  // The signed-in-only draft/proposed overlay's data. Not bbox-scoped like the Parquet
  // queries above it -- it's the caller's own submissions plus the review queue, not a
  // viewport-sized dataset -- and gated purely on auth inside the hook itself.
  const interventionDraftsOverlay = useInterventionDraftsOverlay();
  // Click-to-inspect for all six merged intervention style layers, bound in one
  // place. The overlay's records are handed in so a draft click resolves from
  // memory; only a published Martin-tile click costs a round trip (NFR-1).
  useInterventionDetailClicks(map, interventionDraftsOverlay.recordsById);
  // The per-layer opacity MULTIPLIER for every registry layer. Style-baked layers are applied
  // from here (nothing else owns them); component-mounted layers take theirs as an
  // `opacityScale` prop and fold it into whatever they already compute -- one writer per
  // (layer, paint property), always. See src/lib/map/layer-opacity.ts.
  const layerOpacity = useLayerOpacities();
  const vegetationMode = useVegetationDisplayMode();
  const soilMode = useSoilDisplayMode();
  const soilRasterCatalog = trpc.environmental.getPublishedSoilRasters.useQuery().data ?? [];
  // Shared with DockDetails: one derivation, so the map and the dock's details regions key on
  // one bbox.
  const { zoom, bbox } = useViewportBounds();
  // Written only by DockDetails' capture hook (SoilDetailsBody); drawn here because the map
  // owns its layers.
  const queryPoint = useMapStore((state) => state.queryPoint);

  const settledDays = useSettledLayerDays();
  const {
    droughtDay,
    waterDay,
    vegetationDay,
    weatherDay,
    firePerimetersDay,
    evacuationZonesDay,
    burnSeverityDay,
    sensorsDay,
    firePerimetersHasSelectableDay,
    evacuationZonesHasSelectableDay,
    burnSeverityHasSelectableDay,
    sensorsHasSelectableDay,
  } = settledDays;

  // Published fire-detection CELLS, read from the private Parquet plane through
  // `wildfire.getFireDetections` with this layer's settled day, the viewport bbox and the
  // viewport zoom. It replaced `useFireData` -> `/api/fires` on 2026-09-01: that route was
  // global (no bbox), un-tiered, silently capped at 2,000 rows, and had no way to say a day
  // was never written. See conductor/tracks/parquet_reader_cutover_acceptance_20260901.
  const fire = useParquetFireDetections(layerVisibility.fire);
  // The Parquet-plane reads, one hook per layer family and in the order they were always
  // issued; the rationale for each lives beside its query in useParquetLayerReads.ts.
  const viewport = { bbox, zoom };
  const droughtRead = useDroughtRead(layerVisibility, viewport, droughtDay);
  const { droughtGeoJSON } = droughtRead;
  const styleBakedReads = useStyleBakedParquetReads(layerVisibility, viewport, {
    sensorsDay,
    evacuationZonesDay,
    burnSeverityDay,
    firePerimetersDay,
  });
  const {
    sensorsGeoJSON,
    evacuationZonesGeoJSON,
    burnSeverityEnabled,
    burnSeverityGeoJSON,
    watershedsGeoJSON,
    firePerimetersGeoJSON,
    wavecLanes,
    burnSnapshot,
  } = styleBakedReads;
  const waterReads = useWaterReads(layerVisibility, viewport, waterDay);
  const { waterEnabled, waterPresentation, groundwaterQuery } = waterReads;
  const vegetationRead = useVegetationRead(layerVisibility, viewport, vegetationDay);
  const { vegetationEnabled, vegetationQuery, vegetationGeoJSON } = vegetationRead;
  const soilReads = useSoilLayerReads(layerVisibility, viewport, soilMode, settledDays);
  const {
    soilSurveyVisible,
    soilSurveyGeoJSON,
    soilMoistureVisible,
    soilMoistureGeoJSON,
    soilTemperatureVisible,
    soilTemperatureGeoJSON,
    soilVpdVisible,
    soilVpdGeoJSON,
  } = soilReads;

  // The nine NASA POWER rows read and draw themselves inside `ClimateFieldLayers` below: each
  // signal owns a toggle, a slider and a day, so there is no single climate query or climate
  // day left for this component to hold. Everything it would have kept here -- eighteen hooks'
  // worth -- lives one component down, beside the layer it feeds.

  const weatherRead = useWeatherRead(layerVisibility, viewport, weatherDay);
  const { weatherEnabled, weatherQuery, weatherData } = weatherRead;

  // The automatic land-context viewport read (owner decision 2026-09-18), now CONSUMED rather
  // than mounted and dropped. The hook gates the fetch on the plane being bound in this region,
  // decodes the answer through the click lane's own feature path, and hands back both the
  // boundaries to draw and the one caption that states which of its states it is in.
  const landContextViewport = useLandContextViewportBoundaries();
  const cropCover = useCropCover();
  const cropCoverVisible = cropCover.enabled && cropCover.availability.data?.available === true &&
    !cropCover.availability.isError && !cropCover.query.isError;

  const parquetLayerFaults = buildParquetLayerFaults({
    burnSeverityEnabled,
    burnSnapshot,
    wavecLanes,
    vegetationEnabled,
    vegetationUnavailable: vegetationQuery.data?.state === "upstream_unavailable",
    weatherEnabled,
    weatherUnavailable: weatherQuery.data?.state === "upstream_unavailable",
    fire: {
      isDrawn: layerVisibility.fire,
      state: fire.state,
      truncated: fire.truncated === true,
      absenceReason: fire.result?.state === "absent" ? fire.result.evidence.reason : null,
      isLaneNeverWritten:
        fire.result?.state === "not_generated" && fire.result.reason === "lane_never_written",
    },
    landContextFault: landContextViewport.fault,
  });
  if (cropCover.enabled) {
    const failed = cropCover.availability.isError || cropCover.query.isError;
    const years = [...new Set(cropCover.query.data?.geojson?.features.map((feature) => String(feature.properties?.observed_year)) ?? [])];
    parquetLayerFaults.push({
      layerId: "crop-cover",
      tone: failed ? "fault" : "notice",
      message: failed ? "Crop-cover estimates could not be read." : cropCover.query.isFetching
        ? "Reading estimated crop cover." : cropCover.query.data?.geojson?.features.length
          ? `Estimated crop cover (${years.join(", ")}); published ${cropCover.query.data.servedDay}. ${cropCover.query.data.message}`
          : cropCover.query.data?.message ?? cropCover.availability.data?.reason ?? "Checking crop-cover availability.",
    });
  }

  // What each live layer is DRAWING, for the surfaces that caption the map -- the other half of
  // `keepPreviousData`. The table and its rationale: live-layer-day-reports.ts.
  const liveLayerDayReports = buildLiveLayerDayReports({
    layerVisibility,
    days: settledDays,
    fire,
    drought: droughtRead,
    styleBaked: styleBakedReads,
    water: waterReads,
    vegetation: vegetationRead,
    soil: soilReads,
    weather: weatherRead,
  });
  usePublishedDrawnLayerDays("layer-manager", liveLayerDayReports);

  /**
   * Every Parquet-fed style source's current collection, keyed by the source id styles.ts declares.
   *
   * One record rather than five `setData` calls scattered through the component, so the style.load
   * safety net below can re-apply all five from one ref -- a basemap swap rebuilds each source from
   * its (empty) spec, so without that pass the four layers would silently blank on every swap.
   */
  const parquetFeatureCollections = useMemo<
    Record<ParquetFeatureSourceId, GeoJSON.FeatureCollection>
  >(
    () => ({
      "sensor-station-features": sensorsGeoJSON,
      "evacuation-zone-features": evacuationZonesGeoJSON,
      "burn-severity-features": burnSeverityGeoJSON,
      "watershed-features": watershedsGeoJSON,
      "fire-perimeter-features": firePerimetersGeoJSON,
    }),
    [
      sensorsGeoJSON,
      evacuationZonesGeoJSON,
      burnSeverityGeoJSON,
      watershedsGeoJSON,
      firePerimetersGeoJSON,
    ]
  );


  // The five style-layer writers. Their bodies (and why each is shaped the way it is) live in
  // style-layer-appliers.ts; these stable `useCallback` wrappers keep every effect's dependency
  // array below exactly as it was.
  const applyParquetFeatureData = useCallback<typeof styleLayerAppliers.applyParquetFeatureData>(
    (mapInstance, collections) => styleLayerAppliers.applyParquetFeatureData(mapInstance, collections),
    []
  );
  const applyInterventionDraftsData = useCallback<
    typeof styleLayerAppliers.applyInterventionDraftsData
  >((mapInstance, geojson) => styleLayerAppliers.applyInterventionDraftsData(mapInstance, geojson), []);
  const applyVisibility = useCallback<typeof styleLayerAppliers.applyVisibility>(
    (mapInstance, currentVisibility) =>
      styleLayerAppliers.applyVisibility(mapInstance, currentVisibility),
    []
  );
  const applyOpacity = useCallback<typeof styleLayerAppliers.applyOpacity>(
    (mapInstance, currentOpacity) => styleLayerAppliers.applyOpacity(mapInstance, currentOpacity),
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

  const applyDateFilter = useCallback<typeof styleLayerAppliers.applyDateFilter>(
    (mapInstance, days) => styleLayerAppliers.applyDateFilter(mapInstance, days),
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

  // Same discipline again, for the source data: a basemap swap must re-fill the five Parquet-fed
  // sources from whatever the readers currently hold, without the collections entering the
  // style.load handler's dependency list and re-registering it behind ServiceAreaLayer's.
  const parquetFeatureCollectionsRef = useRef(parquetFeatureCollections);
  useEffect(() => {
    parquetFeatureCollectionsRef.current = parquetFeatureCollections;
  }, [parquetFeatureCollections]);

  // Same discipline for the intervention-drafts source's data.
  const interventionDraftsGeoJSONRef = useRef(interventionDraftsOverlay.geojson);
  useEffect(() => {
    interventionDraftsGeoJSONRef.current = interventionDraftsOverlay.geojson;
  }, [interventionDraftsOverlay.geojson]);

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
      // Data first: the filter and the opacity pass below both write onto layers whose source was
      // just rebuilt empty by the swap, and a layer with no features has nothing to filter.
      applyParquetFeatureData(mapInstance, parquetFeatureCollectionsRef.current);
      applyInterventionDraftsData(mapInstance, interventionDraftsGeoJSONRef.current);
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
  }, [
    map,
    applyVisibility,
    applyDateFilter,
    applyOpacity,
    applyParquetFeatureData,
    applyInterventionDraftsData,
  ]);

  // The data sibling of the filter effect below, and ungated for the same reason: a read that has
  // landed must reach the map even while `isStyleLoaded()` is false, and `applyParquetFeatureData`
  // already no-ops per missing source. `styleReady` stays in the deps so the write repeats once
  // the style settles, which is what covers the first paint.
  useEffect(() => {
    if (!map) return;
    applyParquetFeatureData(map, parquetFeatureCollections);
  }, [map, parquetFeatureCollections, applyParquetFeatureData, styleReady]);

  // The intervention-drafts source's own reactive write, same reasoning as its sibling above:
  // a fetch that has landed (the overlay hook re-fetches on auth change, on submission success
  // via invalidateInterventionDraftsOverlay, etc.) must reach the map even while the style is
  // still settling.
  useEffect(() => {
    if (!map) return;
    applyInterventionDraftsData(map, interventionDraftsOverlay.geojson);
  }, [map, interventionDraftsOverlay.geojson, applyInterventionDraftsData, styleReady]);

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
  // `@/workers/layer-processor.worker` was a worker module never instantiated as one; it was
  // imported directly, which also installed its module-scope `message` listener on `window`
  // (on the main thread `self` IS `window`). That module and `@/lib/map/webgpu-accelerator`
  // were deleted on 2026-09-02 (conformity `c3`), so there is nothing left here to import; a
  // reintroduction repeats the incident above. See `useActionNetworkFeatures` for the shape a
  // real worker takes in this codebase.

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
        geojson={fire.geojson}
        opacityScale={layerOpacity.fire}
      />
      <WaterLayer
        map={map}
        gauges={waterPresentation.gauges}
        aggregateCells={waterPresentation.cells}
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
      {soilRasterCatalog.map((release) => {
        const toggleId = soilRasterToggleId(release.property);
        return (
          <SoilLayer
            key={release.property}
            map={map}
            release={release}
            visible={layerVisibility[toggleId]}
            opacityScale={layerOpacity[toggleId]}
          />
        );
      })}
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
      <ClimateFieldLayers map={map} bbox={bbox} zoom={zoom} />
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
      {/* The boundaries the AUTOMATIC land-context read returned for this viewport. Drawn only
          when the lane produced features; every other state of that lane reaches the reader as a
          caption in the stack above, never as an unexplained empty canvas. */}
      <LandContextViewportLayer
        map={map}
        geojson={landContextViewport.geoJSON}
        visible={landContextViewport.geoJSON !== null}
      />
      <CropCoverLayer geojson={cropCoverVisible ? cropCover.query.data?.geojson ?? null : null} visible={cropCoverVisible} />
      <ParquetLayerFaultBanner faults={parquetLayerFaults} />
      {/* Not a data layer and so not in the registry: it marks where the user clicked,
          and DockDetails' capture hook (SoilDetailsBody, DockDetails.tsx:100-101) is the
          only thing that ever sets it. */}
      <QueryPointLayer map={map} point={queryPoint} />

      {/* Click-to-inspect for the merged intervention layer. Mounted here, not
          in MapView, because this component already owns both of that layer's
          sources -- and because MapView's render-count contract
          (map-view-render-count.test.tsx) is a contract about MapView's own
          subscriptions, which this adds nothing to. Phase 5 (FR-4) passes the
          like/comment UI in as this modal's children. */}
      <InterventionDetailModal />
    </>
  );
}
