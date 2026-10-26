/**
 * The drawn-day report LayerManager publishes for every layer it reads, built as plain data from
 * the reads it already holds. See src/components/map/AGENTS.md §LayerManager structure.
 */

import type { LayerVisibility } from "@/lib/map/layer-toggle-context";
import type { useParquetFireDetections } from "@/hooks/useParquetFireDetections";
import { drawnDayReadState } from "@/hooks/useViewportProxiedLayers";
import {
  drawnDayFlagsFromQuery,
  type LiveLayerDayReport,
  type QueryReadState,
} from "@/stores/useMetricAtDate";
import type { SettledLayerDays } from "@/components/map/layer-manager/useSettledLayerDays";
import type {
  DroughtRead,
  StyleBakedParquetReads,
  VegetationRead,
  WaterReads,
  WeatherRead,
} from "@/components/map/layer-manager/useParquetLayerReads";
import type { SoilLayerReads } from "@/components/map/layer-manager/useSoilLayerReads";

function parquetDrawnDayFlags(query: QueryReadState) {
  const flags = drawnDayFlagsFromQuery(query, "typed");
  const data = query.data as { state?: string } | undefined;
  return data?.state === "upstream_unavailable"
    ? { ...flags, hasLandedForRequestedDate: false }
    : flags;
}

export interface LiveLayerDayReads {
  layerVisibility: LayerVisibility;
  days: SettledLayerDays;
  fire: ReturnType<typeof useParquetFireDetections>;
  drought: DroughtRead;
  styleBaked: StyleBakedParquetReads;
  water: WaterReads;
  vegetation: VegetationRead;
  soil: SoilLayerReads;
  weather: WeatherRead;
}

// What each live layer is actually DRAWING, for the surfaces that caption the map. The other
// half of `keepPreviousData` above; see src/components/map/AGENTS.md "A layer must not blank
// between days". `settledDate`, not `requestDate` -- a caption cannot state an omission.
//
// Nine ids, never the nine NASA POWER signals: each `ClimateSignalLayer` publishes its own,
// because it owns its own read. Publishers must stay disjoint.
export function buildLiveLayerDayReports({
  layerVisibility,
  days,
  fire,
  drought: { droughtQuery },
  styleBaked,
  water: { waterEnabled, streamflowQuery, groundwaterQuery },
  vegetation: { vegetationEnabled, vegetationQuery },
  soil,
  weather: { weatherEnabled, weatherDrawQuery },
}: LiveLayerDayReads): LiveLayerDayReport[] {
  return [
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
      requestedDate: days.droughtDay.settledDate,
      ...parquetDrawnDayFlags(droughtQuery),
    },
    {
      // One row over two upstreams: the day is drawn only once BOTH have answered for it.
      layerId: "water",
      isDrawn: waterEnabled,
      requestedDate: days.waterDay.settledDate,
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
      requestedDate: days.vegetationDay.settledDate,
      ...parquetDrawnDayFlags(vegetationQuery),
    },
    {
      // No selected day; typed publication availability determines its drawn date.
      layerId: "soil-survey",
      isDrawn: soil.soilSurveyVisible,
      requestedDate: null,
      ...drawnDayFlagsFromQuery(drawnDayReadState(soil.soilSurveyRead), "typed"),
      isShowingPreviousDay: false,
    },
    {
      layerId: "soil-moisture",
      isDrawn: soil.soilMoistureVisible,
      requestedDate: days.soilMoistureDay.settledDate,
      ...drawnDayFlagsFromQuery(drawnDayReadState(soil.soilMoistureRead), "typed"),
    },
    {
      layerId: "soil-temperature",
      isDrawn: soil.soilTemperatureVisible,
      requestedDate: days.soilTemperatureDay.settledDate,
      ...drawnDayFlagsFromQuery(drawnDayReadState(soil.soilTemperatureRead), "typed"),
    },
    {
      layerId: "soil-vpd",
      isDrawn: soil.soilVpdVisible,
      requestedDate: days.soilVpdDay.settledDate,
      ...drawnDayFlagsFromQuery(drawnDayReadState(soil.soilVpdRead), "typed"),
    },
    {
      layerId: "weather",
      isDrawn: weatherEnabled,
      requestedDate: days.weatherDay.settledDate,
      ...parquetDrawnDayFlags(weatherDrawQuery),
    },
    // The four wave-C layers. They had no entry here while they were Martin tiles, because a tile
    // layer's day was applied as a style filter over bytes already in the browser -- there was no
    // request to be in flight and nothing to caption. Each now keys a real read on a day, so each
    // owes the same statement about what it is drawing; without it `keepPreviousData` above would
    // retain a previous day's frame with nothing saying so.
    {
      layerId: "sensors",
      isDrawn: styleBaked.sensorsEnabled,
      requestedDate: days.sensorsDay.settledDate,
      ...parquetDrawnDayFlags(styleBaked.sensorsQuery),
    },
    {
      layerId: "evacuation-zones",
      isDrawn: styleBaked.evacuationZonesEnabled,
      requestedDate: days.evacuationZonesDay.settledDate,
      ...parquetDrawnDayFlags(styleBaked.evacuationZonesQuery),
    },
    {
      layerId: "burn-severity",
      isDrawn: styleBaked.burnSeverityEnabled,
      requestedDate: days.burnSeverityDay.settledDate,
      ...parquetDrawnDayFlags(styleBaked.burnSeverityQuery),
    },
    {
      // The fifth wave layer, on the same rule as the three dated ones above: it now keys a real
      // read on a day, so it owes a statement about what it is drawing. `settledDate` is the day
      // ASKED for; the snapshot that answered it may be older, and that gap is the reader's
      // `servedDay` rather than anything a caption may restate as the drawn day.
      layerId: "fire-perimeters",
      isDrawn: styleBaked.firePerimetersEnabled,
      requestedDate: days.firePerimetersDay.settledDate,
      ...parquetDrawnDayFlags(styleBaked.firePerimetersQuery),
    },
    {
      // Static request; the typed answer supplies the actual snapshot release day.
      layerId: "watersheds",
      isDrawn: styleBaked.watershedsEnabled,
      requestedDate: null,
      ...parquetDrawnDayFlags(styleBaked.watershedsQuery),
      isShowingPreviousDay: false,
    },
  ];
}
