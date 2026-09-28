"use client";

import { keepPreviousData } from "@tanstack/react-query";
import { CloudRain, Droplets, MapPin, Thermometer, Wind } from "lucide-react";
import { useEffect, useMemo, useRef } from "react";
import { useDebouncedLayerDay } from "@/lib/map/layer-toggle-context";
import type {
  ParquetBrowserReaderResult,
  ParquetBrowserWeatherObservation,
} from "@/lib/environmental/parquet-presentation";
import { haversineDistance } from "@/lib/map/measurement";
import { trpc } from "@/lib/trpc/client";
import { useMapStore } from "@/stores/map-store";

interface WeatherHistoryReportProps {
  bbox?: string;
  zoom: number;
}

type WeatherResult = ParquetBrowserReaderResult<readonly ParquetBrowserWeatherObservation[]>;

const NOTICE_CLASS_NAME =
  "rounded-md border border-amber-500/40 bg-amber-500/10 p-3 text-xs text-[hsl(var(--foreground))]";

function finite(values: readonly number[]): number[] {
  return values.filter(Number.isFinite);
}

function mean(values: readonly number[]): number | null {
  const measured = finite(values);
  return measured.length === 0
    ? null
    : measured.reduce((sum, value) => sum + value, 0) / measured.length;
}

function range(values: readonly number[]): [number, number] | null {
  const measured = finite(values);
  return measured.length === 0 ? null : [Math.min(...measured), Math.max(...measured)];
}

/** Compass label for a meteorological direction: the direction wind comes from. */
export function windDirectionLabel(degrees: number | null): string | null {
  if (degrees === null || !Number.isFinite(degrees)) return null;
  const points = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"] as const;
  const normalized = ((degrees % 360) + 360) % 360;
  return `${points[Math.round(normalized / 45) % points.length]} (${Math.round(normalized)}°)`;
}

function reading(value: number | null, decimals: number, unit: string): string {
  return value === null || !Number.isFinite(value) ? "Not reported" : `${value.toFixed(decimals)}${unit}`;
}

function SummaryCard({
  icon,
  label,
  value,
  detail,
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
  detail?: string;
}) {
  return (
    <div className="rounded-lg border border-[hsl(var(--border))] bg-[hsl(var(--card))] p-2.5">
      <div className="mb-1 flex items-center gap-1.5 text-[10px] font-medium uppercase tracking-wide text-[hsl(var(--muted-foreground))]">
        {icon}
        <span>{label}</span>
      </div>
      <p className="text-base font-semibold tabular-nums text-[hsl(var(--foreground))]">{value}</p>
      {detail && <p className="mt-0.5 text-[10px] text-[hsl(var(--muted-foreground))]">{detail}</p>}
    </div>
  );
}

function weatherStateNotice(result: WeatherResult | undefined): string | null {
  if (result === undefined) return null;
  if (result.state === "absent") {
    // Weather is a window-policy reader: it requests one day, but a governed absence can cover a
    // whole unpublished window. A servedDay other than requestedDay means this "no readings"
    // answer does not actually describe the requested day, so presenting it as a same-day
    // confirmation would misreport a day that was never itself checked. Ported from the window-
    // absence-date validation in archive/codex/reader-ui-contract-20260912 (commit ee81a641),
    // inlined here rather than through that commit's ParquetDayPolicy/useParquetDayContract
    // abstraction -- weather is the only window-policy reader left, so a one-file check covers
    // it without reintroducing the generic (superseded) machinery.
    if (result.servedDay !== result.requestedDay) {
      return `The governed absence for ${result.requestedDay} was recorded against ${result.servedDay}, not one partition day. Nothing is drawn.`;
    }
    return `The governed record confirms no weather readings for ${result.servedDay}. Nothing is drawn.`;
  }
  if (result.state === "not_generated") {
    return result.reason === "day_not_written"
      ? `No weather data was published for ${result.requestedDay}. Nothing is drawn.`
      : "The historical weather lane has not published any day yet. Nothing is drawn.";
  }
  if (result.state === "upstream_unavailable") {
    return "Historical weather is temporarily unavailable from the data service. No fallback frame is shown.";
  }
  if (result.data.length === 0) {
    return `Weather was published for ${result.servedDay}, but no weather support intersects this view.`;
  }
  return null;
}

/** A weather-forecast-style reading of the selected historical sample day. */
export function WeatherHistoryReport({ bbox, zoom }: WeatherHistoryReportProps) {
  // `settledDate` (debounced) rather than a second, raw `useLayerDay` subscription: `requestDate`
  // below is itself derived from `settledDate`, so comparing the fetched response against the
  // SAME settled value keeps the label above and the data below always describing one day. A raw
  // immediate selection would desync the two mid-scrub -- the label would name the day the user
  // is dragging toward while the data (or its "Loading" placeholder) still lagged behind it.
  const { requestDate, settledDate } = useDebouncedLayerDay("weather");
  const queryPoint = useMapStore((state) => state.queryPoint);
  const setQueryPoint = useMapStore((state) => state.setQueryPoint);
  const viewport = useMapStore((state) => state.viewport);
  const ownedPointRef = useRef<{ lat: number; lon: number } | null>(null);

  useEffect(
    () => () => {
      const owned = ownedPointRef.current;
      const current = useMapStore.getState().queryPoint;
      if (
        owned !== null &&
        current !== null &&
        current !== undefined &&
        current.lat === owned.lat &&
        current.lon === owned.lon
      ) {
        useMapStore.getState().setQueryPoint(null);
      }
    },
    []
  );

  const query = trpc.wildfire.getWeatherForBbox.useQuery(
    { bbox: bbox ?? "", date: requestDate, zoom },
    { enabled: bbox !== undefined, placeholderData: keepPreviousData }
  );

  const exactResult = query.data;
  const resultMatchesSettledDay =
    exactResult === undefined ||
    exactResult.state === "upstream_unavailable" ||
    settledDate === null ||
    exactResult.requestedDay === settledDate;
  // Gated on `!query.isError` too: React Query v5 retains the last successful `data` across a
  // failed refetch by default, so without this an errored request would still read as matching
  // the settled day and the notice below would render next to stale, no-longer-verified rows.
  const presentedResult =
    !query.isError && resultMatchesSettledDay ? exactResult : undefined;
  const rows = presentedResult?.state === "ready" ? presentedResult.data : [];

  const weatherPoint =
    queryPoint !== null &&
    ownedPointRef.current !== null &&
    queryPoint.lat === ownedPointRef.current.lat &&
    queryPoint.lon === ownedPointRef.current.lon
      ? queryPoint
      : null;
  const anchor = weatherPoint ?? { lat: viewport.latitude, lon: viewport.longitude };
  const representative = useMemo(
    () =>
      rows.reduce<ParquetBrowserWeatherObservation | null>((nearest, row) => {
        if (nearest === null) return row;
        return haversineDistance([anchor.lon, anchor.lat], [row.longitude, row.latitude]) <
          haversineDistance([anchor.lon, anchor.lat], [nearest.longitude, nearest.latitude])
          ? row
          : nearest;
      }, null),
    [anchor.lat, anchor.lon, rows]
  );
  const representativeDistance =
    representative === null
      ? null
      : haversineDistance(
          [anchor.lon, anchor.lat],
          [representative.longitude, representative.latitude]
        );

  const temperatureRange = range(rows.map((row) => row.temperatureC));
  const averageTemperature = mean(rows.map((row) => row.temperatureC));
  const averageHumidity = mean(rows.map((row) => row.relativeHumidityPct));
  const averageWind = mean(rows.map((row) => row.windSpeedMs));
  const maximumWind = rows.length === 0 ? null : Math.max(...rows.map((row) => row.windSpeedMs));
  const wetReadings = rows.filter((row) => Number.isFinite(row.precipitationMm) && row.precipitationMm > 0).length;
  const pointReadingCount = rows.filter((row) => row.support.supportKind === "raw_point").length;
  const aggregateCellCount = rows.length - pointReadingCount;
  const stateNotice = weatherStateNotice(presentedResult);
  // Same lane as `upstream_unavailable`: a governed absence whose servedDay doesn't match the
  // requested day is a contract violation, not a confirmed empty day, and is just as worth
  // retrying.
  const isGovernedAbsenceWindowMismatch =
    presentedResult?.state === "absent" && presentedResult.servedDay !== presentedResult.requestedDay;
  const canRetry =
    query.isError ||
    presentedResult?.state === "upstream_unavailable" ||
    isGovernedAbsenceWindowMismatch;
  const staleSettledDay =
    exactResult !== undefined &&
    exactResult.state !== "upstream_unavailable" &&
    !resultMatchesSettledDay;

  return (
    <section aria-labelledby="historical-weather-heading" className="flex flex-col gap-2.5">
      <div>
        <p id="historical-weather-heading" className="text-xs font-semibold text-[hsl(var(--foreground))]">
          Historical weather
        </p>
        <p className="mt-0.5 text-[10px] leading-relaxed text-[hsl(var(--muted-foreground))]">
          {settledDate ?? "Latest published day"} · Open-Meteo historical estimates · SI units
        </p>
      </div>

      {(query.isFetching || staleSettledDay) && presentedResult === undefined && (
        <p role="status" aria-live="polite" className="text-xs text-[hsl(var(--muted-foreground))]">
          Loading {settledDate ?? "the latest published weather"}; no earlier frame is shown.
        </p>
      )}
      {query.isError && (
        <div role="alert" className={NOTICE_CLASS_NAME}>
          <p>Historical weather could not be loaded. No cached fallback frame is shown.</p>
          <button
            type="button"
            onClick={() => void query.refetch()}
            disabled={query.isFetching}
            className="mt-2 min-h-11 rounded px-2 text-xs font-medium underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-500 disabled:opacity-50"
          >
            {query.isFetching ? "Retrying…" : "Retry weather"}
          </button>
        </div>
      )}
      {stateNotice && (
        <div role="status" className={NOTICE_CLASS_NAME}>
          <p>{stateNotice}</p>
          {canRetry && (
            <button
              type="button"
              onClick={() => void query.refetch()}
              disabled={query.isFetching}
              className="mt-2 min-h-11 rounded px-2 text-xs font-medium underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-500 disabled:opacity-50"
            >
              {query.isFetching ? "Retrying…" : "Retry weather"}
            </button>
          )}
        </div>
      )}

      {rows.length > 0 && (
        <>
          <div className="grid grid-cols-2 gap-2">
            <SummaryCard
              icon={<Thermometer aria-hidden="true" className="h-3.5 w-3.5" />}
              label="Temperature"
              value={reading(averageTemperature, 1, " °C")}
              detail={temperatureRange ? `${temperatureRange[0].toFixed(1)} to ${temperatureRange[1].toFixed(1)} °C in view` : undefined}
            />
            <SummaryCard
              icon={<Wind aria-hidden="true" className="h-3.5 w-3.5" />}
              label="Wind"
              value={reading(averageWind, 1, " m/s")}
              detail={maximumWind === null ? undefined : `${maximumWind.toFixed(1)} m/s maximum`}
            />
            <SummaryCard
              icon={<Droplets aria-hidden="true" className="h-3.5 w-3.5" />}
              label="Humidity"
              value={reading(averageHumidity, 0, "%")}
              detail="Mean across visible readings"
            />
            <SummaryCard
              icon={<CloudRain aria-hidden="true" className="h-3.5 w-3.5" />}
              label="Precipitation"
              value={`${wetReadings} of ${rows.length}`}
              detail="Visible readings above 0 mm"
            />
          </div>

          {representative && (
            <div className="rounded-lg border border-[hsl(var(--border))] bg-[hsl(var(--card))] p-3">
              <div className="flex items-start justify-between gap-2">
                <div>
                  <p className="text-xs font-semibold text-[hsl(var(--foreground))]">
                    {weatherPoint ? "Nearest to selected point" : "Nearest to view center"}
                  </p>
                  <p className="text-[10px] text-[hsl(var(--muted-foreground))]">
                    {representativeDistance?.toFixed(1)} km from {weatherPoint ? "the selected point" : "map center"}
                  </p>
                </div>
                <button
                  type="button"
                  aria-label={weatherPoint ? "Clear selected weather point" : "Mark nearest weather reading on map"}
                  className="flex min-h-11 items-center gap-1 rounded-md border border-[hsl(var(--border))] px-2 text-[10px] font-medium text-[hsl(var(--foreground))]"
                  onClick={() => {
                    if (weatherPoint) {
                      ownedPointRef.current = null;
                      setQueryPoint(null);
                      return;
                    }
                    const point = {
                      lat: representative.latitude,
                      lon: representative.longitude,
                    };
                    ownedPointRef.current = point;
                    setQueryPoint(point);
                  }}
                >
                  <MapPin aria-hidden="true" className="h-3.5 w-3.5" />
                  {weatherPoint ? "Clear" : "Mark"}
                </button>
              </div>
              <dl className="mt-2 grid grid-cols-2 gap-x-3 gap-y-2">
                {[
                  ["Temperature", reading(representative.temperatureC, 1, " °C")],
                  ["Humidity", reading(representative.relativeHumidityPct, 0, "% RH")],
                  ["Wind speed", reading(representative.windSpeedMs, 1, " m/s")],
                  ["Wind direction", windDirectionLabel(representative.windDirectionDeg) ?? "Not reported"],
                  ["Precipitation", reading(representative.precipitationMm, 1, " mm")],
                  ["Support", representative.support.supportKind === "aggregate_cell" ? "Declared aggregate-cell mean" : "Sampled point reading"],
                ].map(([label, value]) => (
                  <div key={label}>
                    <dt className="text-[9px] font-medium uppercase tracking-wide text-[hsl(var(--muted-foreground))]">{label}</dt>
                    <dd className="text-[11px] text-[hsl(var(--foreground))]">{value}</dd>
                  </div>
                ))}
              </dl>
            </div>
          )}

          <p className="text-[10px] leading-relaxed text-[hsl(var(--muted-foreground))]">
            {pointReadingCount} point {pointReadingCount === 1 ? "reading" : "readings"} and {aggregateCellCount} declared aggregate {aggregateCellCount === 1 ? "cell" : "cells"} in view. Colored squares are aggregate support only; gaps are unmeasured, not interpolated. For an admitted continuous historical field, use Air temperature and its Filled or Contours form above.
          </p>
          {presentedResult?.state === "ready" && presentedResult.truncated && (
            <p role="status" className={NOTICE_CLASS_NAME}>
              This view reached the response limit. Zoom in before comparing its weather summary.
            </p>
          )}
        </>
      )}
    </section>
  );
}
