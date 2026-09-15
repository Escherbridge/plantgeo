"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import dynamic from "next/dynamic";
import type { GeoJSONSource } from "maplibre-gl";
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
import { useParquetFireDetections } from "@/hooks/useParquetFireDetections";
import {
  botanicalBandForZoom,
  useBotanicalOccurrencesQuery,
  useSoilFieldQuery,
  useSoilSurveyQuery,
  useViewportBounds,
} from "@/hooks/useViewportProxiedLayers";
import { useBotanicalOccurrenceStore } from "@/stores/botanical-occurrence-store";
import { trpc } from "@/lib/trpc/client";
import { useInterventionDraftsOverlay } from "@/lib/map/use-intervention-drafts";
import { INTERVENTION_DRAFTS_SOURCE_ID } from "@/lib/map/sources";
import { useInterventionDetailClicks } from "@/lib/map/use-intervention-detail-clicks";
import { InterventionDetailModal } from "@/components/map/InterventionDetailModal";
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
  type QueryReadState,
} from "@/stores/useMetricAtDate";
import type { WeatherPoint } from "@/components/map/layers/WeatherLayer";
import {
  presentParquetBurnSeverity,
  presentParquetDrought,
  presentParquetEvacuationZones,
  presentParquetFirePerimeters,
  presentParquetSensorStations,
  presentParquetVegetation,
  presentParquetWater,
  presentParquetWatersheds,
  presentParquetWeather,
} from "@/lib/environmental/parquet-presentation";
import {
  PARQUET_FEATURE_SOURCE_IDS,
  type ParquetFeatureSourceId,
} from "@/lib/map/sources";
import {
  presentBotanicalCell,
  presentBotanicalOccurrence,
} from "@/lib/environmental/botanical-presentation";
// The three GeoJSON builders come from the layer modules themselves, which is why they are
// exported there: the spatial guard that drops nonspatial specimens must not exist twice.
// Imported statically while the components above are dynamic -- these are pure functions with
// no MapLibre dependency, so they cost nothing at SSR.
import { botanicalOccurrencesToGeoJSON } from "@/components/map/layers/BotanicalOccurrencesLayer";
import { GBIF_COLLECTION_KEY } from "@/lib/environmental/botanical-governance-status";
import { BOTANICAL_DETAIL_MIN_ZOOM } from "@/lib/botanical-occurrences";
import { botanicalRichnessToGeoJSON } from "@/components/map/layers/BotanicalRichnessLayer";
import { botanicalEffortToGeoJSON } from "@/components/map/layers/BotanicalCollectionEffortLayer";

const EMPTY_FEATURE_COLLECTION: GeoJSON.FeatureCollection = {
  type: "FeatureCollection",
  features: [],
};

function parquetDrawnDayFlags(query: QueryReadState) {
  const flags = drawnDayFlagsFromQuery(query, "typed");
  const data = query.data as { state?: string } | undefined;
  return data?.state === "upstream_unavailable"
    ? { ...flags, hasLandedForRequestedDate: false }
    : flags;
}

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
const BotanicalOccurrencesLayer = dynamic(
  () =>
    import("@/components/map/layers/BotanicalOccurrencesLayer").then((m) => ({
      default: m.BotanicalOccurrencesLayer,
    })),
  { ssr: false }
);
const BotanicalRichnessLayer = dynamic(
  () =>
    import("@/components/map/layers/BotanicalRichnessLayer").then((m) => ({
      default: m.BotanicalRichnessLayer,
    })),
  { ssr: false }
);
const GbifOccurrencesLayer = dynamic(
  () =>
    import("@/components/map/layers/GbifOccurrencesLayer").then((m) => ({
      default: m.GbifOccurrencesLayer,
    })),
  { ssr: false }
);
const BotanicalCollectionEffortLayer = dynamic(
  () =>
    import("@/components/map/layers/BotanicalCollectionEffortLayer").then((m) => ({
      default: m.BotanicalCollectionEffortLayer,
    })),
  { ssr: false }
);
const QueryPointLayer = dynamic(
  () => import("@/components/map/layers/QueryPointLayer").then((m) => ({ default: m.QueryPointLayer })),
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
  // No `fireDay` here: `useParquetFireDetections` reads the `fire` row's day itself and hands
  // back the settled one, so the map and `FireDetails` cannot key two entries for one answer.
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

  // Published fire-detection CELLS, read from the private Parquet plane through
  // `wildfire.getFireDetections` with this layer's settled day, the viewport bbox and the
  // viewport zoom. It replaced `useFireData` -> `/api/fires` on 2026-09-01: that route was
  // global (no bbox), un-tiered, silently capped at 2,000 rows, and had no way to say a day
  // was never written. See conductor/tracks/parquet_reader_cutover_acceptance_20260901.
  const fire = useParquetFireDetections(layerVisibility.fire);
  // `placeholderData: keepPreviousData` on every dated feed below: each keys on a day AND a
  // bbox, so without it every settled scrub and every pan blanked the layer for a full round
  // trip. Legal only because `usePublishedDrawnLayerDays` below labels the retained frame --
  // see src/components/map/AGENTS.md "A layer must not blank between days". Never one without
  // the other.
  //
  const droughtQuery = trpc.environmental.getDroughtClassification.useQuery(
    { bbox: bbox ?? undefined, date: droughtDay.requestDate, zoom },
    {
      enabled: layerVisibility.drought && bbox !== null,
      placeholderData: keepPreviousData,
    }
  );
  const droughtGeoJSON = useMemo(
    () => presentParquetDrought(droughtQuery.data),
    [droughtQuery.data]
  );
  // The four style-baked layers that moved off Martin's tile functions in wave C of
  // environmental_postgres_retirement_20260904. They are read here, beside drought and vegetation,
  // rather than inside a component of their own, because they are still STYLE-BAKED: their layers
  // live in styles.ts and their visibility, opacity and date filter are written by the three
  // appliers below. What this component now owns for them is a fifth thing -- their source DATA --
  // which `applyParquetFeatureData` writes onto the empty GeoJSON sources styles.ts declares.
  //
  // Each takes the viewport bbox and the viewport ZOOM, and the zoom is not a hint: it selects the
  // published rung. That is the whole point of the cutover -- `geo.burn_severity_tiles()` did no
  // simplification at any zoom and cost 2,341,323 vertices / 37.5 MB / 28.4 s cold for one read of
  // the whole layer, while the ladder publishes z13/z9/z5/z0 rungs of the same polygons.
  //
  // `keepPreviousData` on the three dated feeds, with the matching `liveLayerDayReports` entries
  // below, for the same reason every dated feed here carries both: a settled scrub or a pan would
  // otherwise blank the layer for a full round trip. Never one without the other.
  const sensorsEnabled = layerVisibility.sensors;
  const sensorsQuery = trpc.environmental.getSensorStations.useQuery(
    { bbox: bbox ?? undefined, date: sensorsDay.requestDate, zoom },
    { enabled: sensorsEnabled && bbox !== null, placeholderData: keepPreviousData }
  );
  const sensorsGeoJSON = useMemo(
    () => presentParquetSensorStations(sensorsQuery.data),
    [sensorsQuery.data]
  );

  const evacuationZonesEnabled = layerVisibility["evacuation-zones"];
  const evacuationZonesQuery = trpc.environmental.getEvacuationZones.useQuery(
    { bbox: bbox ?? undefined, date: evacuationZonesDay.requestDate, zoom },
    { enabled: evacuationZonesEnabled && bbox !== null, placeholderData: keepPreviousData }
  );
  const evacuationZonesGeoJSON = useMemo(
    () => presentParquetEvacuationZones(evacuationZonesQuery.data),
    [evacuationZonesQuery.data]
  );

  const burnSeverityEnabled = layerVisibility["burn-severity"];
  const burnSeverityQuery = trpc.environmental.getBurnSeverity.useQuery(
    { bbox: bbox ?? undefined, date: burnSeverityDay.requestDate, zoom },
    { enabled: burnSeverityEnabled && bbox !== null, placeholderData: keepPreviousData }
  );
  const burnSeverityGeoJSON = useMemo(
    () => presentParquetBurnSeverity(burnSeverityQuery.data),
    [burnSeverityQuery.data]
  );

  // The fifth and last of them, and the one whose day means something slightly different from
  // the others': `fire-perimeters` is a `static_lookup` SNAPSHOT lane, so the day asks "which
  // incidents were current as of this date" and the reader answers from the newest snapshot at or
  // before it. A day between snapshots is therefore answered by an older capture rather than
  // blanked -- which is what `geo.fire_risk_tiles()` plus the style filter always did, and why
  // this layer, unlike watersheds, does take a date.
  const firePerimetersEnabled = layerVisibility["fire-perimeters"];
  const firePerimetersQuery = trpc.environmental.getFirePerimeters.useQuery(
    { bbox: bbox ?? undefined, date: firePerimetersDay.requestDate, zoom },
    { enabled: firePerimetersEnabled && bbox !== null, placeholderData: keepPreviousData }
  );
  const firePerimetersGeoJSON = useMemo(
    () => presentParquetFirePerimeters(firePerimetersQuery.data),
    [firePerimetersQuery.data]
  );

  // NO DAY IS PASSED HERE, and it is not an oversight. Watersheds is the one of the four that
  // carries no date filter (`DATE_FILTERABLE_TILE_LAYER_TOGGLE_IDS` omits it) because a WBD
  // boundary set is a snapshot, not an observation series: `geo.watershed_tiles()` drew the same
  // 9,396 basins at every point on every axis. The lane holds exactly ONE release day, so asking
  // for a historical day would return `not_generated` and blank a layer that has always drawn --
  // scrubbing to 2024 would delete the continent's watersheds. The live edge is the only honest
  // ask for a static lookup.
  const watershedsEnabled = layerVisibility.watersheds;
  const watershedsQuery = trpc.environmental.getWatershedBoundaries.useQuery(
    { bbox: bbox ?? undefined, zoom },
    { enabled: watershedsEnabled && bbox !== null, placeholderData: keepPreviousData }
  );
  const watershedsGeoJSON = useMemo(
    () => presentParquetWatersheds(watershedsQuery.data),
    [watershedsQuery.data]
  );

  const waterEnabled = layerVisibility.water;
  // Both feeds take `water`'s day, because both are drawn by the one `water` toggle and so by
  // the one row that carries a slider for them. Gauges and wells sharing a day is a property of
  // there being a single control over them, not an assumption about the two upstreams.
  const streamflowQuery = trpc.environmental.getStreamflow.useQuery(
    { bbox: bbox ?? "-180,-90,180,90", date: waterDay.requestDate, zoom },
    {
      enabled: waterEnabled && bbox !== null,
      staleTime: 15 * 60 * 1000,
      placeholderData: keepPreviousData,
    }
  );
  const waterPresentation = useMemo(
    () => presentParquetWater(streamflowQuery.data),
    [streamflowQuery.data]
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
    { bbox: bbox ?? "-180,-90,180,90", date: vegetationDay.requestDate, zoom },
    {
      enabled: vegetationEnabled && bbox !== null,
      staleTime: 60 * 60 * 1000,
      placeholderData: keepPreviousData,
    }
  );
  // No zoom or tier is threaded into the three aggregate layers below. Each served feature
  // declares the rung it was read at and the square it covers (`AggregateEnvelopeSupport`), so
  // the presenters choose the form from the data in hand rather than from the camera -- which
  // is what keeps a retained frame drawn as the rung it was actually aggregated at instead of
  // being reshaped by a zoom whose answer has not landed. See src/lib/map/AGENTS.md.
  const vegetationGeoJSON = useMemo(
    () => presentParquetVegetation(vegetationQuery.data),
    [vegetationQuery.data]
  );

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

  // The three herbarium specimen rows, over ONE read.
  //
  // The plane answers `detail` (individual specimens) at zoom >= 11 and `aggregate` (support
  // cells) below it, from the same route on the same inputs -- so one query serves all three
  // toggles and they can never disagree about which generation they are drawing. The zoom band
  // is what decides which of them can draw at all, which is the exclusivity
  // `BotanicalOccurrencesLayer`'s own docstring delegates to "whichever container chooses which
  // layer to mount". This is that container.
  //
  // The floor is 11 and the `ZOOM_TIERS` ladder's rungs are 0/5/9/13, so `botanicalBandForZoom`
  // is a bare comparison rather than a `resolveZoomTier` call -- rounding onto the ladder would
  // send a zoom-11 detail request to the z9 aggregate rung.
  const botanicalFilters = useBotanicalOccurrenceStore((state) => state.filters);
  const setBotanicalResponse = useBotanicalOccurrenceStore((state) => state.setLastResponse);
  const setSelectedBotanicalFeature = useBotanicalOccurrenceStore(
    (state) => state.setSelectedFeature
  );
  const botanicalBand = botanicalBandForZoom(zoom);
  const botanicalOccurrencesVisible = layerVisibility["botanical-occurrences"];
  const botanicalRichnessVisible = layerVisibility["botanical-richness"];
  const botanicalEffortVisible = layerVisibility["botanical-collection-effort"];
  const gbifOccurrencesVisible = layerVisibility["gbif-occurrences"];
  // Enabled when a toggle that could actually DRAW at this band is on. A lit occurrence switch
  // at zoom 4 fetches nothing, because the detail layer cannot draw there and the aggregate
  // layers are off -- the gate is about what is drawable, not about what is switched on.
  // `gbifOccurrencesVisible` joins the detail-band condition alongside the UBC toggle, since
  // GBIF's toggle only ever draws in the same detail band and shares the same one query.
  const botanicalQueryEnabled =
    botanicalBand === "detail"
      ? botanicalOccurrencesVisible || gbifOccurrencesVisible
      : botanicalRichnessVisible || botanicalEffortVisible;
  // Empty filter strings are "unset" in the store, never sent as an empty query parameter --
  // the service would read `family=` as a filter matching nothing.
  const botanicalQuery = useBotanicalOccurrencesQuery(bbox, {
    enabled: botanicalQueryEnabled,
    zoom,
    taxonConceptId: botanicalFilters.taxon_concept_id || undefined,
    family: botanicalFilters.family || undefined,
    collectionKey: botanicalFilters.collection_key || undefined,
    eventStart: botanicalFilters.event_start || undefined,
    eventEnd: botanicalFilters.event_end || undefined,
    spatialQuality: botanicalFilters.spatial_quality,
  });
  // The RETURNED state, never the requested band. A retained frame outlives the zoom it was
  // fetched for (`placeholderData` holds the previous answer across a pan or a zoom), so during
  // a zoom across the floor the band says "detail" while the cells in hand are still aggregate.
  // Reading the answer's own state is what keeps aggregate cells out of the detail layer and
  // specimen points out of the two choropleths.
  const botanicalResult = botanicalQuery.data;
  const botanicalDetail = botanicalResult?.state === "detail" ? botanicalResult : null;
  const botanicalAggregate = botanicalResult?.state === "aggregate" ? botanicalResult : null;
  // Presented into the snake_case vocabulary the three layer components were built against;
  // see src/lib/environmental/botanical-presentation.ts for why the two vocabularies differ.
  const botanicalFeatures = useMemo(
    () => (botanicalDetail?.features ?? []).map(presentBotanicalOccurrence),
    [botanicalDetail]
  );
  const botanicalCells = useMemo(
    () => (botanicalAggregate?.cells ?? []).map(presentBotanicalCell),
    [botanicalAggregate]
  );
  // `publishedAt` and the release id are threaded onto every drawn feature, not kept beside the
  // collection: the shared hover manager (`lib/map/hover-fields.ts`) reads MapLibre feature
  // properties and cannot reach a response object, and source + staleness on hover is the point.
  // Excludes GBIF's own collection_key: GBIF draws through its own component/toggle below, and
  // without this exclusion a reader with BOTH toggles on would see every GBIF point drawn twice
  // (once per source's independent MapLibre source/layer set). Any OTHER future collection_key
  // still falls through to this, the general layer -- only GBIF is carved out, because only GBIF
  // has its own sibling component so far.
  const botanicalOccurrencesGeoJSON = useMemo(
    () =>
      botanicalDetail === null
        ? null
        : botanicalOccurrencesToGeoJSON(
            botanicalFeatures.filter((feature) => feature.collection_key !== GBIF_COLLECTION_KEY),
            botanicalDetail.publishedAt
          ),
    [botanicalDetail, botanicalFeatures]
  );
  // GBIF draws through its OWN component/toggle (`GbifOccurrencesLayer`), independently
  // switchable from the UBC layer above, even though both read the same `botanicalFeatures`
  // response -- collection_key is the only thing that tells the two sources apart, so the split
  // happens here, once, on the shared feature list, rather than teaching either map component
  // about the other's source. See `GbifOccurrencesLayer.tsx`'s module doc for why this is a new
  // component rather than a parameterized mode of the UBC one.
  const gbifFeatures = useMemo(
    () => botanicalFeatures.filter((feature) => feature.collection_key === GBIF_COLLECTION_KEY),
    [botanicalFeatures]
  );
  const gbifOccurrencesGeoJSON = useMemo(
    () =>
      botanicalDetail === null
        ? null
        : botanicalOccurrencesToGeoJSON(gbifFeatures, botanicalDetail.publishedAt),
    [botanicalDetail, gbifFeatures]
  );
  const botanicalRichnessGeoJSON = useMemo(
    () =>
      botanicalAggregate === null
        ? null
        : botanicalRichnessToGeoJSON(
            botanicalCells,
            botanicalAggregate.publishedAt,
            botanicalAggregate.releaseSetId
          ),
    [botanicalAggregate, botanicalCells]
  );
  const botanicalEffortGeoJSON = useMemo(
    () =>
      botanicalAggregate === null
        ? null
        : botanicalEffortToGeoJSON(
            botanicalCells,
            botanicalAggregate.publishedAt,
            botanicalAggregate.releaseSetId
          ),
    [botanicalAggregate, botanicalCells]
  );
  // Published to the store so `BotanicalFilters` and the details panel describe the SAME answer
  // the map is drawing rather than issuing a second read of their own. Written in an effect
  // rather than during render because it is a store write; the dependency is the query result
  // object, which react-query keeps referentially stable until a new answer lands.
  useEffect(() => {
    if (botanicalResult === undefined) return;
    setBotanicalResponse({
      state: botanicalResult.state,
      releaseSetId: "releaseSetId" in botanicalResult ? botanicalResult.releaseSetId : null,
      publishedAt: "publishedAt" in botanicalResult ? botanicalResult.publishedAt : null,
    });
  }, [botanicalResult, setBotanicalResponse]);
  // The generation the answer was actually served from, published back into the store.
  //
  // `BotanicalFilters` was built expecting a reader to TYPE a `release_set_id` and gates its
  // whole form until one is set, because when it was written this plane had no pointer route on
  // the client. It does now: `getBotanicalOccurrences` resolves `/current` server-side and the
  // browser never names a generation. So the id flows the other way -- the answer reports which
  // generation it came from, and the panel displays it. Written from the RESPONSE rather than
  // from a second `/current` read, so the id the panel shows is provably the one the cells on
  // the map were read from and not a pointer that has since moved.
  const servedBotanicalReleaseSetId =
    botanicalDetail?.releaseSetId ?? botanicalAggregate?.releaseSetId ?? null;
  const setBotanicalReleaseSetId = useBotanicalOccurrenceStore((state) => state.setReleaseSetId);
  useEffect(() => {
    if (servedBotanicalReleaseSetId === null) return;
    setBotanicalReleaseSetId(servedBotanicalReleaseSetId);
  }, [servedBotanicalReleaseSetId, setBotanicalReleaseSetId]);
  // Clicking a specimen opens the details panel, the same way every other layer with a detail
  // surface does it: the layer reports an id, this component resolves it against the features it
  // already holds, and the store slice the panel reads is the only thing that changes. Resolved
  // here rather than in the layer because the layer only carries MapLibre feature properties --
  // four fields -- and the panel needs the whole record including rights and attribution.
  const handleSelectBotanicalOccurrence = useCallback(
    (occurrenceId: string) => {
      const selected = botanicalFeatures.find(
        (feature) => feature.occurrence_id === occurrenceId
      );
      setSelectedBotanicalFeature(selected ?? null);
    },
    [botanicalFeatures, setSelectedBotanicalFeature]
  );

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
    { bbox: bbox ?? "-180,-90,180,90", date: weatherDay.requestDate, zoom },
    {
      enabled: weatherEnabled && bbox !== null,
      staleTime: 15 * 60 * 1000,
      placeholderData: keepPreviousData,
    }
  );
  // Strict Parquet rows carry every required weather measurement; presentation only renames
  // fields for the existing browser-safe layer vocabulary.
  const weatherResultMatchesDay =
    weatherQuery.data === undefined ||
    weatherQuery.data.state === "upstream_unavailable" ||
    weatherDay.settledDate === null ||
    weatherQuery.data.requestedDay === weatherDay.settledDate;
  // Keep the query cache warm, but never paint a retained prior-day frame under a new selection.
  const weatherResult = weatherResultMatchesDay ? weatherQuery.data : undefined;
  // A withheld placeholder is blank on the canvas, so the drawn-day registry must not report it
  // as a retained frame to MapDateSummary.
  const weatherDrawQuery =
    weatherResult === undefined && weatherQuery.isPlaceholderData === true
      ? { ...weatherQuery, data: undefined, isPlaceholderData: false }
      : { ...weatherQuery, data: weatherResult };
  const weatherData = useMemo<WeatherPoint[]>(
    () => presentParquetWeather(weatherResult),
    [weatherResult]
  );
  // `fault` is an outage: nothing is drawn and the reason is upstream. `notice` is a true
  // statement ABOUT what is drawn -- a truncated read paints real cells that stop short of the
  // viewport, which must be said rather than left to look like the edge of the fire.
  // The five wave-C layers, folded rather than written out five times: it is one sentence (two,
  // counting the truncation notice below) about five lanes, and five copies is five places for
  // one wording to drift. `truncated` is read straight off each lane's own `ParquetReaderResult`
  // -- `mapEnvelope`/`getParquetBurnSeverity` already compute it server-side (see
  // `src/lib/server/services/parquet-trpc-readers.ts`), and every presenter in
  // `parquet-presentation.ts` maps `.data` into GeoJSON without ever reading it. Nothing else in
  // this component looked at it before this block did, so a capped read painted a partial
  // watershed set, burn-severity union, sensor roster, evacuation-zone snapshot or perimeter
  // snapshot with nothing on the map saying the drawn shapes stop short of the viewport --
  // the exact silent-refusal-as-absence this codebase's fire lane was already fixed against.
  const wavecLanes = [
    { layerId: "sensors" as const, isDrawn: sensorsEnabled, data: sensorsQuery.data, subject: "Sensor station readings" },
    {
      layerId: "evacuation-zones" as const,
      isDrawn: evacuationZonesEnabled,
      data: evacuationZonesQuery.data,
      subject: "Evacuation zones",
    },
    {
      layerId: "burn-severity" as const,
      isDrawn: burnSeverityEnabled,
      data: burnSeverityQuery.data,
      subject: "Burn history boundaries",
    },
    {
      layerId: "watersheds" as const,
      isDrawn: watershedsEnabled,
      data: watershedsQuery.data,
      subject: "Watershed boundaries",
    },
    // Missing from this list entirely until now: the fifth wave-C layer never surfaced an
    // upstream fault OR a truncation notice, so a failed or capped perimeter read looked exactly
    // like an ordinary quiet fire season.
    {
      layerId: "fire-perimeters" as const,
      isDrawn: firePerimetersEnabled,
      data: firePerimetersQuery.data,
      subject: "Fire perimeters",
    },
  ];

  const burnSnapshot = burnSeverityQuery.data?.state === "ready"
    ? burnSeverityQuery.data.mtbsSnapshot : undefined;
  const parquetLayerFaults = [
    burnSeverityEnabled && burnSnapshot
      ? {
          layerId: "burn-severity-capture",
          tone: "notice" as const,
          message: `MTBS captured ${burnSnapshot.capturedThrough}; available ${burnSnapshot.availableDay}. `
            + `Fire years ${burnSnapshot.coveredYears.from}–${burnSnapshot.coveredYears.to}. `
            + (burnSnapshot.partialFireYears.length
              ? `Mapping remains incomplete for ${burnSnapshot.partialFireYears.join(", ")}.`
              : "The captured query scope is complete."),
        }
      : null,
    ...wavecLanes.map((lane) =>
      lane.isDrawn && lane.data?.state === "upstream_unavailable"
        ? {
            layerId: lane.layerId,
            tone: "fault" as const,
            message: `${lane.subject} are temporarily unavailable from the data service.`,
          }
        : null
    ),
    // A `notice`, not a `fault`: the lane answered, and the answer is real geometry that stops
    // short of the row budget rather than an outage. Reusing the fire lane's own wording keeps
    // one sentence for "this shape is a subset" across every layer that can say it.
    ...wavecLanes.map((lane) =>
      lane.isDrawn && lane.data?.state === "ready" && lane.data.truncated
        ? {
            layerId: `${lane.layerId}-truncated`,
            tone: "notice" as const,
            message: lane.layerId === "burn-severity"
              ? "Burn history is incomplete because some history is unpublished or a read limit was reached. Available published burn history boundaries are shown."
              : `The Parquet row budget was reached. The ${lane.subject.toLowerCase()} drawn are a subset of this viewport.`,
          }
        : null
    ),
    vegetationEnabled && vegetationQuery.data?.state === "upstream_unavailable"
      ? {
          layerId: "vegetation",
          tone: "fault" as const,
          message:
            "Measured vegetation observations are temporarily unavailable from the data service.",
        }
      : null,
    weatherEnabled && weatherQuery.data?.state === "upstream_unavailable"
      ? {
          layerId: "weather",
          tone: "fault" as const,
          message: "Weather observations are temporarily unavailable from the data service.",
        }
      : null,
    layerVisibility.fire && fire.state === "upstream_unavailable"
      ? {
          layerId: "fire",
          tone: "fault" as const,
          message: "Fire detections are temporarily unavailable from the data service.",
        }
      : null,
    // The transport failed before the reader returned any state at all, so there is no typed
    // refusal to quote -- and an empty canvas beside a lit switch would read as "no fires".
    // A `fault` and not a `notice`: nothing about the lane was established.
    layerVisibility.fire && fire.state === "request_failed"
      ? {
          layerId: "fire-request-failed",
          tone: "fault" as const,
          message:
            "The fire detections request failed before returning a state. No fallback is shown.",
        }
      : null,
    // Every accepted fire answer is asserted un-truncated; a truncated one is surfaced here
    // instead of being quietly drawn as the whole viewport's detections.
    layerVisibility.fire && fire.truncated
      ? {
          layerId: "fire-truncated",
          tone: "notice" as const,
          message:
            "The Parquet row budget was reached. The fire detections drawn are a subset of this viewport.",
        }
      : null,
    // The two refusals an empty canvas cannot tell apart from "no fires burned here", and the
    // reason each is a `notice` rather than a `fault`: nothing is down. A governed absence is a
    // POSITIVE record that the upstream was checked and published nothing, so the reason it
    // carries is the evidence and is quoted verbatim -- the same sentence `FireDetails` shows,
    // because a reader looking at the map and a reader looking at the dock must not be told two
    // different things about one day.
    layerVisibility.fire && fire.state === "absent"
      ? {
          layerId: "fire-absent",
          tone: "notice" as const,
          message: `The fire lane recorded a governed absence for this day: ${
            fire.result?.state === "absent" ? fire.result.evidence.reason : "reason unavailable"
          }.`,
        }
      : null,
    // `not_generated` is the opposite claim: nobody checked. Named by which silence it is --
    // one day missing from a written lane, or a lane that has never been written at all --
    // because "no detections" would assert an observation neither one made.
    layerVisibility.fire && fire.state === "not_generated"
      ? {
          layerId: "fire-not-generated",
          tone: "notice" as const,
          message:
            fire.result?.state === "not_generated" && fire.result.reason === "lane_never_written"
              ? "The fire lane has never been written, so no detections can be drawn for any day."
              : "This day has not been written for the fire lane, so no detections can be drawn for it.",
        }
      : null,
    // The occurrence plane's own two non-answers, surfaced because an empty canvas beside a lit
    // switch reads as "no specimens were ever collected here" -- which is the one thing a
    // collection-bias layer must never imply.
    //
    // Both are a `notice`, not a `fault`, and the split is the same one the fire lane makes: the
    // service ANSWERED in both cases. `refused` is a governed refusal (a request the plane
    // declines to serve -- too wide a bbox, a filter combination it will not honour) and
    // `unavailable` is the plane reporting that no generation is published. Neither is an
    // outage, so neither is dressed as one. The service-authored `note` is quoted verbatim for
    // the same reason the fire lane quotes its evidence: the plane's own words are what a reader
    // can act on, and paraphrasing them would put this component in the business of explaining a
    // refusal it did not make. A genuine transport fault throws in the procedure instead and
    // reaches the map as a failed query, not as a state here.
    botanicalQueryEnabled && botanicalResult?.state === "refused"
      ? {
          layerId: "botanical-refused",
          tone: "notice" as const,
          message: `The specimen occurrence plane declined this request: ${botanicalResult.note}`,
        }
      : null,
    botanicalQueryEnabled && botanicalResult?.state === "unavailable"
      ? {
          layerId: "botanical-unavailable",
          tone: "notice" as const,
          message: `Specimen occurrences are not published: ${botanicalResult.note}`,
        }
      : null,
    botanicalQueryEnabled && botanicalQuery.isError === true
      ? {
          layerId: "botanical-request-failed",
          tone: "fault" as const,
          message: "The botanical and GBIF occurrence request failed. Current viewport results could not be verified.",
        }
      : null,
    // A `notice` for the same reason every other lane's is: the records drawn are real, they
    // just stop short of the viewport. Saying so is what keeps a capped read from looking like
    // a collecting gap -- which, for this plane specifically, is a claim about where botanists
    // have and have not been.
    botanicalQueryEnabled &&
    (botanicalDetail?.truncated === true || botanicalAggregate?.truncated === true)
      ? {
          layerId: "botanical-truncated",
          tone: "notice" as const,
          message:
            botanicalBand === "detail"
              ? "The specimen row budget was reached. The occurrences drawn are a subset of this viewport."
              : "The cell budget was reached. The support cells drawn are a subset of this viewport.",
        }
      : null,
    // Withheld records are a POSITIVE fact the plane reports and the map cannot show: a specimen
    // whose locality is protected has no dot, and without this line its absence is
    // indistinguishable from it never having been collected.
    botanicalQueryEnabled && (botanicalDetail?.counts.withheld ?? 0) > 0
      ? {
          layerId: "botanical-withheld",
          tone: "notice" as const,
          message: `${botanicalDetail?.counts.withheld} specimen records in this release have their locality withheld by the publisher and cannot be drawn anywhere.`,
        }
      : null,
    // The Occurrences toggle is switched on but the map is below the detail floor, so nothing is
    // drawn and nothing was even fetched (`botanicalQueryEnabled` is false in that case, since the
    // detail layer cannot draw at this band). Without this line a reader who turned the toggle on
    // at a continental zoom sees an empty map and no explanation -- indistinguishable from the
    // layer being broken.
    botanicalOccurrencesVisible && botanicalBand !== "detail"
      ? {
          layerId: "botanical-below-detail-floor",
          tone: "notice" as const,
          message: `Individual specimen points draw at zoom ${BOTANICAL_DETAIL_MIN_ZOOM} and above. Zoom in to see them, or turn on Documented Taxon Richness / Collection Evidence & Effort for this zoom.`,
        }
      : null,
    gbifOccurrencesVisible && botanicalBand !== "detail"
      ? {
          layerId: "gbif-below-detail-floor",
          tone: "notice" as const,
          message: `GBIF occurrence points draw at zoom ${BOTANICAL_DETAIL_MIN_ZOOM} and above. Zoom in to see published records.`,
        }
      : null,
    // Only the settled returned slice supports an empty notice; see AGENTS.md §GBIF feedback.
    gbifOccurrencesVisible &&
    botanicalBand === "detail" &&
    bbox !== null &&
    botanicalQuery.isSuccess === true &&
    botanicalQuery.isFetching !== true &&
    botanicalQuery.isPlaceholderData !== true &&
    botanicalDetail !== null &&
    gbifFeatures.length === 0
      ? {
          layerId: "gbif-empty",
          tone: "notice" as const,
          message: botanicalDetail.truncated
            ? "No GBIF occurrence points appear in this limited result. The row limit prevents a complete assessment of this viewport and its current filters."
            : "No GBIF occurrence points were returned for this viewport and current filters.",
        }
      : null,
  ].filter((fault): fault is NonNullable<typeof fault> => fault !== null);

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
      // The day the READ settled on, not a second lookup of the same row: one hook owns both.
      requestedDate: fire.settledDate,
      ...drawnDayFlagsFromQuery({ data: fire.result }, "typed"),
      // Already derived the way `parquetDrawnDayFlags` derives them, `upstream_unavailable`
      // downgrade included -- see `useParquetFireDetections`.
      isFetching: fire.isFetching,
      hasLandedForRequestedDate: fire.hasLandedForRequestedDate,
      isShowingPreviousDay: fire.isShowingPreviousDay,
    },
    {
      layerId: "drought",
      isDrawn: layerVisibility.drought,
      requestedDate: droughtDay.settledDate,
      ...parquetDrawnDayFlags(droughtQuery),
    },
    {
      // One row over two upstreams: the day is drawn only once BOTH have answered for it.
      layerId: "water",
      isDrawn: waterEnabled,
      requestedDate: waterDay.settledDate,
      isFetching: streamflowQuery.isFetching === true || groundwaterQuery.isFetching === true,
      hasLandedForRequestedDate:
        parquetDrawnDayFlags(streamflowQuery).hasLandedForRequestedDate &&
        drawnDayFlagsFromQuery(groundwaterQuery).hasLandedForRequestedDate,
      isShowingPreviousDay:
        streamflowQuery.isPlaceholderData === true || groundwaterQuery.isPlaceholderData === true,
    },
    {
      layerId: "vegetation",
      isDrawn: vegetationEnabled,
      requestedDate: vegetationDay.settledDate,
      ...parquetDrawnDayFlags(vegetationQuery),
    },
    {
      // No selected day; typed publication availability determines its drawn date.
      layerId: "soil-survey",
      isDrawn: soilSurveyVisible,
      requestedDate: null,
      ...drawnDayFlagsFromQuery(soilSurveyQuery, "typed"),
      isShowingPreviousDay: false,
    },
    {
      layerId: "soil-moisture",
      isDrawn: soilMoistureVisible,
      requestedDate: soilMoistureDay.settledDate,
      ...drawnDayFlagsFromQuery(soilMoistureQuery, "typed"),
    },
    {
      layerId: "soil-temperature",
      isDrawn: soilTemperatureVisible,
      requestedDate: soilTemperatureDay.settledDate,
      ...drawnDayFlagsFromQuery(soilTemperatureQuery, "typed"),
    },
    {
      layerId: "soil-vpd",
      isDrawn: soilVpdVisible,
      requestedDate: soilVpdDay.settledDate,
      ...drawnDayFlagsFromQuery(soilVpdQuery, "typed"),
    },
    {
      layerId: "weather",
      isDrawn: weatherEnabled,
      requestedDate: weatherDay.settledDate,
      ...parquetDrawnDayFlags(weatherDrawQuery),
    },
    // The four wave-C layers. They had no entry here while they were Martin tiles, because a tile
    // layer's day was applied as a style filter over bytes already in the browser -- there was no
    // request to be in flight and nothing to caption. Each now keys a real read on a day, so each
    // owes the same statement about what it is drawing; without it `keepPreviousData` above would
    // retain a previous day's frame with nothing saying so.
    {
      layerId: "sensors",
      isDrawn: sensorsEnabled,
      requestedDate: sensorsDay.settledDate,
      ...parquetDrawnDayFlags(sensorsQuery),
    },
    {
      layerId: "evacuation-zones",
      isDrawn: evacuationZonesEnabled,
      requestedDate: evacuationZonesDay.settledDate,
      ...parquetDrawnDayFlags(evacuationZonesQuery),
    },
    {
      layerId: "burn-severity",
      isDrawn: burnSeverityEnabled,
      requestedDate: burnSeverityDay.settledDate,
      ...parquetDrawnDayFlags(burnSeverityQuery),
    },
    {
      // The fifth wave layer, on the same rule as the three dated ones above: it now keys a real
      // read on a day, so it owes a statement about what it is drawing. `settledDate` is the day
      // ASKED for; the snapshot that answered it may be older, and that gap is the reader's
      // `servedDay` rather than anything a caption may restate as the drawn day.
      layerId: "fire-perimeters",
      isDrawn: firePerimetersEnabled,
      requestedDate: firePerimetersDay.settledDate,
      ...parquetDrawnDayFlags(firePerimetersQuery),
    },
    {
      // Static request; the typed answer supplies the actual snapshot release day.
      layerId: "watersheds",
      isDrawn: watershedsEnabled,
      requestedDate: null,
      ...parquetDrawnDayFlags(watershedsQuery),
      isShowingPreviousDay: false,
    },
  ];
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

  /**
   * The fourth applier, and the one the other three would be useless without: it writes the DATA a
   * style-baked layer has no component to hold for it.
   *
   * Guarded with `getSource` for the same reason `applyVisibility` guards with `getLayer`: running
   * against a half-built style must be a no-op per missing source rather than an error, and the
   * `styleReady` dependency on the effect below is what makes the pass repeat once the style
   * catches up. Deliberately NOT in the `styledata` handler's frame with the filter and the
   * opacity: those rebuild an expression per layer, while this re-serializes up to 9,396 basins,
   * and `styledata` fires as every tile lands.
   */
  const applyParquetFeatureData = useCallback(
    (
      mapInstance: NonNullable<typeof map>,
      collections: Record<ParquetFeatureSourceId, GeoJSON.FeatureCollection>
    ) => {
      for (const sourceId of PARQUET_FEATURE_SOURCE_IDS) {
        const source = mapInstance.getSource(sourceId);
        // Structural, not `instanceof`: a basemap swap can leave a same-named source of another
        // kind mid-rebuild, and calling setData on one would throw inside a style event handler.
        if (typeof (source as { setData?: unknown } | undefined)?.setData !== "function") continue;
        (source as GeoJSONSource).setData(collections[sourceId]);
      }
    },
    []
  );

  /**
   * The intervention-drafts source's own writer, kept separate from `applyParquetFeatureData`
   * above even though the mechanism (empty geojson source, filled by setData, re-applied on
   * style reload) is identical -- that source is explicitly NOT Parquet-fed (see sources.ts),
   * and folding it into `PARQUET_FEATURE_SOURCE_IDS` would misdescribe it for the next reader.
   * Guarded the same way: a missing/mid-rebuild source is a no-op, never a thrown error.
   */
  const applyInterventionDraftsData = useCallback(
    (mapInstance: NonNullable<typeof map>, geojson: GeoJSON.FeatureCollection) => {
      const source = mapInstance.getSource(INTERVENTION_DRAFTS_SOURCE_ID);
      if (typeof (source as { setData?: unknown } | undefined)?.setData !== "function") return;
      (source as GeoJSONSource).setData(geojson);
    },
    []
  );

  // Sync visibility of every style-baked layer (fire-perimeters, interventions, sensors,
  // evacuation-zones, burn-severity, watersheds) with activeLayers -- these are declared in the
  // style rather than mounted as React components, so they need setLayoutProperty instead of an
  // unmount/remount cycle. Whether the layer's source is a Martin tile source or one of the
  // Parquet-fed GeoJSON sources makes no difference here: this walks the registry.
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
      {/* The three herbarium rows off ONE read. Zoom-band exclusivity is enforced here, in the
          container, which is where `BotanicalOccurrencesLayer`'s docstring says it belongs --
          the component draws unconditionally once handed geojson.

          `visible` is gated on the RETURNED state rather than on the requested band: each layer
          is handed null geojson whenever the answer in hand is the other shape, so a retained
          aggregate frame cannot be drawn as specimens (or the reverse) while a zoom across the
          floor is in flight. Passing the band alone would draw the previous answer in the new
          band's layer for one round trip. The detail component ALSO re-checks the floor itself,
          which is belt-and-braces rather than duplication: it removes its layers below zoom 11
          whatever it was handed. */}
      <BotanicalOccurrencesLayer
        map={map}
        geojson={botanicalOccurrencesGeoJSON}
        zoom={zoom}
        visible={botanicalOccurrencesVisible && botanicalBand === "detail"}
        onSelectFeature={handleSelectBotanicalOccurrence}
      />
      {/* GBIF's own toggle over the SAME one query, filtered above to GBIF's collection_key --
          see `gbifOccurrencesGeoJSON`'s definition for why the split happens in this container
          rather than inside either map component. Independently switchable from the UBC layer
          just above: a reader can have UBC-only, GBIF-only, both, or neither on at once. */}
      <GbifOccurrencesLayer
        map={map}
        geojson={gbifOccurrencesGeoJSON}
        zoom={zoom}
        visible={gbifOccurrencesVisible && botanicalBand === "detail"}
        onSelectFeature={handleSelectBotanicalOccurrence}
      />
      {/* Richness is the primary aggregate read -- "how many taxa are documented here" -- and
          effort is the context layer UNDER it that says how hard anyone looked. They are two
          toggles over one response rather than one layer with a mode, because a reader
          interpreting a richness cell needs to be able to put the effort cell beside it; that
          is the whole point of shipping a collection-bias layer at all. Both draw across the
          entire aggregate band (every zoom below the detail floor) rather than splitting it
          between them: the plane returns ONE `aggregate` answer per viewport with both measures
          on the same cells, so there is no sub-band where one has data and the other does not,
          and inventing a split would hide the bias layer at exactly the coarse zooms where
          collecting bias is most visible.

          The opacity multiplier is a plain multiply rather than `scaleOpacityValue`: that helper
          returns `unknown` because it may emit a MapLibre `["*", ...]` expression for a
          style-authored base, and both of these components take a `number` prop and fold it into
          their own authored base themselves. Same rule as every other component-mounted layer --
          one writer per (layer, paint property) -- reached through the arithmetic these props
          allow. */}
      <BotanicalRichnessLayer
        map={map}
        geojson={botanicalRichnessGeoJSON}
        releaseSetId={botanicalAggregate?.releaseSetId ?? null}
        visible={botanicalRichnessVisible && botanicalBand === "aggregate"}
        opacity={0.75 * layerOpacity["botanical-richness"]}
      />
      <BotanicalCollectionEffortLayer
        map={map}
        geojson={botanicalEffortGeoJSON}
        measure={botanicalFilters.effort_measure}
        visible={botanicalEffortVisible && botanicalBand === "aggregate"}
        opacity={0.55 * layerOpacity["botanical-collection-effort"]}
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
      {parquetLayerFaults.length > 0 && (
        <div
          className="pointer-events-none absolute left-1/2 top-12 z-20 flex -translate-x-1/2 flex-col gap-1.5"
          aria-live="assertive"
        >
          {parquetLayerFaults.map((fault) => (
            <p
              key={fault.layerId}
              role="alert"
              className={
                fault.tone === "fault"
                  ? "rounded-md border border-red-500/40 bg-[hsl(var(--card))]/95 px-3 py-1.5 text-xs font-medium text-red-600 shadow-sm backdrop-blur dark:text-red-400"
                  : "rounded-md border border-amber-500/40 bg-[hsl(var(--card))]/95 px-3 py-1.5 text-xs font-medium text-amber-700 shadow-sm backdrop-blur dark:text-amber-400"
              }
              data-testid={`parquet-layer-unavailable-${fault.layerId}`}
            >
              {fault.message}
            </p>
          ))}
        </div>
      )}
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
