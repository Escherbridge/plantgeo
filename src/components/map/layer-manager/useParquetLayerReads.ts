"use client";

/**
 * LayerManager's Parquet-plane reads, one hook per layer family, each returning the query and the
 * collection its layer draws. Called from LayerManager in the order the reads were always issued.
 * See src/components/map/AGENTS.md §LayerManager structure.
 *
 * `placeholderData: keepPreviousData` on every dated feed below: each keys on a day AND a
 * bbox, so without it every settled scrub and every pan blanked the layer for a full round
 * trip. Legal only because `usePublishedDrawnLayerDays` (via `buildLiveLayerDayReports`) labels
 * the retained frame -- see src/components/map/AGENTS.md "A layer must not blank between days".
 * Never one without the other.
 */

import { useMemo } from "react";
import { keepPreviousData } from "@tanstack/react-query";
import type { DebouncedMapDay, LayerVisibility } from "@/lib/map/layer-toggle-context";
import { trpc } from "@/lib/trpc/client";
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
import type { ParquetLaneReport } from "@/components/map/layer-manager/parquet-layer-faults";
import { WORLD_EXTENT_BBOX } from "@/lib/map/world-extent";

/** The viewport every read here keys on, from the one `useViewportBounds()` derivation. */
export interface ParquetReadViewport {
  bbox: string | null;
  zoom: number;
}

export function useDroughtRead(
  layerVisibility: LayerVisibility,
  { bbox, zoom }: ParquetReadViewport,
  droughtDay: DebouncedMapDay
) {
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
  return { droughtQuery, droughtGeoJSON };
}

export type DroughtRead = ReturnType<typeof useDroughtRead>;

/**
 * The five style-baked layers whose source DATA LayerManager owns, plus the fault/notice report
 * each lane owes the banner.
 */
export function useStyleBakedParquetReads(
  layerVisibility: LayerVisibility,
  { bbox, zoom }: ParquetReadViewport,
  {
    sensorsDay,
    evacuationZonesDay,
    burnSeverityDay,
    firePerimetersDay,
  }: {
    sensorsDay: DebouncedMapDay;
    evacuationZonesDay: DebouncedMapDay;
    burnSeverityDay: DebouncedMapDay;
    firePerimetersDay: DebouncedMapDay;
  }
) {
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
  const wavecLanes: ParquetLaneReport[] = [
    {
      layerId: "sensors",
      isDrawn: sensorsEnabled,
      state: sensorsQuery.data?.state,
      truncated: sensorsQuery.data?.state === "ready" && sensorsQuery.data.truncated === true,
      subject: "Sensor station readings",
    },
    {
      layerId: "evacuation-zones",
      isDrawn: evacuationZonesEnabled,
      state: evacuationZonesQuery.data?.state,
      truncated:
        evacuationZonesQuery.data?.state === "ready" && evacuationZonesQuery.data.truncated === true,
      subject: "Evacuation zones",
    },
    {
      layerId: "burn-severity",
      isDrawn: burnSeverityEnabled,
      state: burnSeverityQuery.data?.state,
      truncated:
        burnSeverityQuery.data?.state === "ready" && burnSeverityQuery.data.truncated === true,
      subject: "Burn history boundaries",
    },
    {
      layerId: "watersheds",
      isDrawn: watershedsEnabled,
      state: watershedsQuery.data?.state,
      truncated: watershedsQuery.data?.state === "ready" && watershedsQuery.data.truncated === true,
      subject: "Watershed boundaries",
    },
    // Missing from this list entirely until 2026-09-18: the fifth wave-C layer never surfaced an
    // upstream fault OR a truncation notice, so a failed or capped perimeter read looked exactly
    // like an ordinary quiet fire season.
    {
      layerId: "fire-perimeters",
      isDrawn: firePerimetersEnabled,
      state: firePerimetersQuery.data?.state,
      truncated:
        firePerimetersQuery.data?.state === "ready" && firePerimetersQuery.data.truncated === true,
      subject: "Fire perimeters",
    },
  ];

  const burnSnapshot = burnSeverityQuery.data?.state === "ready"
    ? burnSeverityQuery.data.mtbsSnapshot : undefined;

  return {
    sensorsEnabled,
    sensorsQuery,
    sensorsGeoJSON,
    evacuationZonesEnabled,
    evacuationZonesQuery,
    evacuationZonesGeoJSON,
    burnSeverityEnabled,
    burnSeverityQuery,
    burnSeverityGeoJSON,
    firePerimetersEnabled,
    firePerimetersQuery,
    firePerimetersGeoJSON,
    watershedsEnabled,
    watershedsQuery,
    watershedsGeoJSON,
    wavecLanes,
    burnSnapshot,
  };
}

export type StyleBakedParquetReads = ReturnType<typeof useStyleBakedParquetReads>;

/** Streamflow gauges and groundwater wells: two upstreams under the one `water` toggle. */
export function useWaterReads(
  layerVisibility: LayerVisibility,
  { bbox, zoom }: ParquetReadViewport,
  waterDay: DebouncedMapDay
) {
  const waterEnabled = layerVisibility.water;
  // Both feeds take `water`'s day, because both are drawn by the one `water` toggle and so by
  // the one row that carries a slider for them. Gauges and wells sharing a day is a property of
  // there being a single control over them, not an assumption about the two upstreams.
  const streamflowQuery = trpc.environmental.getStreamflow.useQuery(
    { bbox: bbox ?? WORLD_EXTENT_BBOX, date: waterDay.requestDate, zoom },
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
    { bbox: bbox ?? WORLD_EXTENT_BBOX, date: waterDay.requestDate },
    {
      enabled: waterEnabled && bbox !== null,
      staleTime: 60 * 60 * 1000,
      placeholderData: keepPreviousData,
    }
  );
  return { waterEnabled, streamflowQuery, waterPresentation, groundwaterQuery };
}

export type WaterReads = ReturnType<typeof useWaterReads>;

export function useVegetationRead(
  layerVisibility: LayerVisibility,
  { bbox, zoom }: ParquetReadViewport,
  vegetationDay: DebouncedMapDay
) {
  const vegetationEnabled = layerVisibility.vegetation;
  // The measured NDVI grid, read from the warehouse: one cell per sampling-grid square,
  // carrying that cell's newest Sentinel-2 reading. Distinct from the GIBS raster
  // VegetationLayer also draws -- that one is a global 8-day composite this platform
  // proxies, this one is the 184,409-row series this platform ingested. Sentinel-2 yields
  // at most one clear reading per cell every few days, so the hour-long staleTime matches
  // the groundwater/watershed cadence rather than the 15-minute observation feeds. A named
  // day slides that per-cell window to end there instead of at now.
  const vegetationQuery = trpc.environmental.getVegetationIndex.useQuery(
    { bbox: bbox ?? WORLD_EXTENT_BBOX, date: vegetationDay.requestDate, zoom },
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
  return { vegetationEnabled, vegetationQuery, vegetationGeoJSON };
}

export type VegetationRead = ReturnType<typeof useVegetationRead>;

export function useWeatherRead(
  layerVisibility: LayerVisibility,
  { bbox, zoom }: ParquetReadViewport,
  weatherDay: DebouncedMapDay
) {
  const weatherEnabled = layerVisibility.weather;
  // Reads every published observation across the viewport bbox -- not just the
  // nearest one -- so the wind layer reflects the full spread of
  // warehouse-backed samples instead of a single point.
  const weatherQuery = trpc.wildfire.getWeatherForBbox.useQuery(
    { bbox: bbox ?? WORLD_EXTENT_BBOX, date: weatherDay.requestDate, zoom },
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
  return { weatherEnabled, weatherQuery, weatherDrawQuery, weatherData };
}

export type WeatherRead = ReturnType<typeof useWeatherRead>;
