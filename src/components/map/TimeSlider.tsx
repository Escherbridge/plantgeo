"use client";

import { useMemo } from "react";
import { cn } from "@/lib/utils";
import type {
  ForecastVariant,
  MetricAtDateAvailability,
  SliderLayerCapability,
} from "@/types/time-slider";
import {
  addDays,
  clampDateToDomain,
  dayOffset,
  isCalendarDate,
  isFutureDate,
  layerAvailabilityAt,
  sliderDomain,
  sliderMaxOffset,
  todayOffset,
  useTimeSliderStore,
} from "@/stores/time-slider-store";

/**
 * Diagonal stripes over the days after the server's today. Forecast days must never be
 * mistaken for observed ones, so the future half of the track is marked, not just coloured.
 */
const FUTURE_HATCH_BACKGROUND =
  "repeating-linear-gradient(45deg, hsl(var(--muted-foreground) / 0.55) 0 3px, transparent 3px 6px)";

/**
 * Shared by the slider panel and its unavailable-state notice, so a fetch that later
 * succeeds does not reposition anything -- both render at the same anchor, sized the same.
 */
const TIME_SLIDER_CONTAINER_CLASSES =
  "absolute bottom-24 left-1/2 z-10 w-[min(34rem,calc(100vw-2rem))] -translate-x-1/2 rounded-(--radius) border border-[hsl(var(--border))] bg-[hsl(var(--card))]/90 p-3 shadow-lg backdrop-blur-sm";

/**
 * A local range input rather than `@/components/ui/slider`: that component paints an inline
 * gradient over its own track, which would bury the hatched future segment, and it forwards
 * no ARIA, so a screen reader would read the day offset instead of the date. Same `<style>`
 * idiom, same thumb, transparent track.
 */
const timeSliderStyles = `
  .time-slider-range {
    -webkit-appearance: none;
    appearance: none;
    background: transparent;
    height: 18px;
    outline: none;
    cursor: pointer;
    margin: 0;
  }
  .time-slider-range::-webkit-slider-runnable-track {
    background: transparent;
    height: 18px;
  }
  .time-slider-range::-moz-range-track {
    background: transparent;
    height: 18px;
  }
  .time-slider-range::-webkit-slider-thumb {
    -webkit-appearance: none;
    appearance: none;
    width: 18px;
    height: 18px;
    border-radius: 50%;
    background: hsl(var(--primary));
    border: 2px solid hsl(var(--background));
    cursor: pointer;
    box-shadow: 0 0 0 2px hsl(var(--primary) / 0.3);
    transition: box-shadow 150ms;
  }
  .time-slider-range:focus-visible::-webkit-slider-thumb {
    box-shadow: 0 0 0 4px hsl(var(--ring) / 0.5);
  }
  .time-slider-range::-moz-range-thumb {
    width: 18px;
    height: 18px;
    border-radius: 50%;
    background: hsl(var(--primary));
    border: 2px solid hsl(var(--background));
    cursor: pointer;
    box-shadow: 0 0 0 2px hsl(var(--primary) / 0.3);
    transition: box-shadow 150ms;
  }
  .time-slider-range:focus-visible::-moz-range-thumb {
    box-shadow: 0 0 0 4px hsl(var(--ring) / 0.5);
  }
`;

/** The forecast series a user may pick, and why one of them cannot be picked yet. */
const FORECAST_VARIANT_OPTIONS: ReadonlyArray<{
  value: ForecastVariant;
  label: string;
  /** Non-null when the option can never be chosen yet, whatever the date. */
  permanentlyUnavailableReason: string | null;
}> = [
  { value: "monte_carlo", label: "Monte Carlo", permanentlyUnavailableReason: null },
  // Rendered so the capability is visibly withheld rather than silently missing; there is
  // no trained model until phase 7.
  { value: "ml", label: "ML", permanentlyUnavailableReason: "no trained model yet" },
];

/** Shown while the selection is not in the future, where no forecast applies. */
const FORECASTS_ARE_FUTURE_ONLY = "forecasts apply to future dates";

/** "fire-detections" -> "Fire detections", so a geo.layers name can start a sentence. */
function humanizeLayerName(layerName: string): string {
  const spaced = layerName.replace(/-/g, " ");
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

/**
 * Row-scoped copy, deliberately not the store's `describeAvailability`: the row already
 * names the layer, and the horizon line has to quote that layer's own forecast horizon.
 */
function availabilityMessage(
  availability: MetricAtDateAvailability,
  layer: SliderLayerCapability,
  variantLabel: string
): string | null {
  switch (availability) {
    case "published":
      return null;
    case "not_yet_observed":
      return "Not yet observed at this date";
    case "not_forecastable":
      return layer.temporalKind === "event"
        ? `${humanizeLayerName(layer.layerName)} are events; no forecast exists`
        : `${humanizeLayerName(layer.layerName)} is not forecast beyond today`;
    case "beyond_horizon":
      return `No forecast beyond +${layer.forecastHorizonDays} days`;
    case "variant_unavailable":
      return `${variantLabel} forecast not available for this layer`;
    case "not_published":
      return "Nothing published for this date";
    case "request_failed":
      return "Could not be loaded — not a gap in the record";
  }
}

export interface TimeSliderProps {
  /** geo.layers names to list; defaults to every layer the server published a capability for. */
  layerNames?: string[];
  className?: string;
}

/**
 * Discrete one-day scrubber over the published history and the forecast horizon. The slider's
 * value is an integer day offset from the domain's first day; it becomes a YYYY-MM-DD string
 * only at the edges, because a fractional position would name a day nothing was observed on.
 * There is no play button and no tweening for the same reason.
 */
export default function TimeSlider({ layerNames, className }: TimeSliderProps) {
  const selectedDate = useTimeSliderStore((state) => state.selectedDate);
  const forecastVariant = useTimeSliderStore((state) => state.forecastVariant);
  const capabilities = useTimeSliderStore((state) => state.capabilities);
  const capabilitiesUnavailable = useTimeSliderStore((state) => state.capabilitiesUnavailable);
  const setSelectedDate = useTimeSliderStore((state) => state.setSelectedDate);
  const setForecastVariant = useTimeSliderStore((state) => state.setForecastVariant);

  const domain = sliderDomain(capabilities);

  const layerRows = useMemo(() => {
    if (capabilities === null || !isCalendarDate(selectedDate)) return [];
    const listed =
      layerNames === undefined
        ? capabilities.layers
        : capabilities.layers.filter((layer) => layerNames.includes(layer.layerName));
    return listed.map((layer) => ({
      layer,
      availability: layerAvailabilityAt(layer, selectedDate, forecastVariant, capabilities),
    }));
  }, [capabilities, layerNames, selectedDate, forecastVariant]);

  // The fetch failed and never succeeded even once: there is nothing else below that can be
  // computed honestly (no domain, no "today"), so this replaces the whole panel instead of
  // leaving it silent. Silence is what made the owner read a failing endpoint as "no slider
  // UI" in the first place -- see time-slider-store.ts's capabilitiesUnavailable doc comment.
  if (capabilities === null && capabilitiesUnavailable) {
    return (
      <div
        className={cn(TIME_SLIDER_CONTAINER_CLASSES, className)}
        role="alert"
        data-testid="time-slider-unavailable"
      >
        <p className="text-xs font-semibold uppercase tracking-wide text-[hsl(var(--destructive))]">
          Time range unavailable
        </p>
        <p className="mt-1 text-xs text-[hsl(var(--muted-foreground))]">
          The available dates could not be loaded. This is a loading failure, not a sign that
          nothing has been published, and it will retry automatically.
        </p>
      </div>
    );
  }

  // Both ends come from the payload. Without it there is no honest domain to draw, and
  // guessing one from the browser clock would put "today" on the wrong day. This also covers
  // the ordinary in-flight case (capabilities still null, capabilitiesUnavailable still
  // false): rendering nothing here, rather than a loading placeholder, is what keeps a normal
  // fast load from flashing a spinner in and back out.
  if (capabilities === null || domain === null || !isCalendarDate(selectedDate)) return null;

  const { firstDay, lastDay, today } = domain;
  const maxOffset = sliderMaxOffset(domain);
  const todayTickOffset = todayOffset(domain);
  const selectedOffset = dayOffset(firstDay, selectedDate);
  const isFuture = isFutureDate(selectedDate, capabilities);
  // Every map layer is still dateless (lane J): FireLayer, WaterLayer, the four LayerManager
  // queries, the Martin style layers and the GIBS raster all draw the latest published values
  // whatever day is selected. The control asserts a date in large type directly above a canvas
  // that is answering a different question, and the per-layer list below reports the WAREHOUSE
  // record -- so a day the map is painting points on can legitimately read "Not yet observed".
  // Say that here, where the date is asserted, rather than only in two side panels that fire
  // when they happen to be open. Delete this block when a layer reads `selectedDate`.
  const mapIsStillDateless = selectedDate !== today;
  const activeVariantLabel =
    FORECAST_VARIANT_OPTIONS.find((option) => option.value === forecastVariant)?.label ??
    forecastVariant;

  /** Track position of a day offset, in percent. Guards the single-day domain. */
  const percentOfOffset = (offset: number): number =>
    maxOffset === 0 ? 0 : (offset / maxOffset) * 100;

  /** Writes through on every tick so the thumb tracks the pointer; the query debounces. */
  const handleOffsetChange = (nextOffset: number) => {
    // Whole days only. A fractional position would name a day nothing was observed on.
    const wholeDayOffset = Math.round(nextOffset);
    setSelectedDate(clampDateToDomain(addDays(firstDay, wholeDayOffset), capabilities));
  };

  return (
    <div
      className={cn(TIME_SLIDER_CONTAINER_CLASSES, className)}
      data-testid="time-slider"
    >
      <style dangerouslySetInnerHTML={{ __html: timeSliderStyles }} />

      <div className="mb-2 flex items-start justify-between gap-3">
        <div className="flex flex-col">
          <span className="text-xs font-semibold uppercase tracking-wide text-[hsl(var(--muted-foreground))]">
            {isFuture ? "Forecast" : "Observed"}
          </span>
          <span
            className="text-sm font-medium text-[hsl(var(--foreground))]"
            data-testid="time-slider-selected-date"
          >
            {selectedDate}
          </span>
        </div>

        <div className="flex flex-col items-end">
          <div role="radiogroup" aria-label="Forecast variant" className="flex items-center gap-1">
            {FORECAST_VARIANT_OPTIONS.map((option) => {
              const disabledReason = isFuture
                ? option.permanentlyUnavailableReason
                : FORECASTS_ARE_FUTURE_ONLY;
              const isSelected = option.value === forecastVariant;
              return (
                <button
                  key={option.value}
                  type="button"
                  role="radio"
                  aria-checked={isSelected}
                  disabled={disabledReason !== null}
                  title={disabledReason ?? `Show the ${option.label} forecast`}
                  onClick={() => setForecastVariant(option.value)}
                  className={`rounded-(--radius) border px-2 py-1 text-xs disabled:cursor-not-allowed disabled:opacity-50 ${
                    isSelected
                      ? "border-[hsl(var(--primary))] bg-[hsl(var(--primary))]/15 text-[hsl(var(--foreground))]"
                      : "border-[hsl(var(--border))] text-[hsl(var(--muted-foreground))]"
                  }`}
                >
                  {option.label}
                </button>
              );
            })}
          </div>
          {/* The reason is on the page, not only in a title: a disabled button is not focusable. */}
          <span className="map-popup-meta" data-testid="time-slider-variant-hint">
            {isFuture ? "ML: no trained model yet" : FORECASTS_ARE_FUTURE_ONLY}
          </span>
        </div>
      </div>

      {mapIsStillDateless && (
        <p
          className="mb-2 rounded-(--radius) border border-[hsl(var(--border))] bg-[hsl(var(--secondary))] px-2 py-1.5 text-[11px] text-[hsl(var(--foreground))]"
          data-testid="time-slider-dateless-map-notice"
        >
          The map is still showing the latest published values, not values for {selectedDate}.
          Only the record below is read at this date.
        </p>
      )}

      <div className="relative flex h-4.5 items-center">
        <div className="absolute inset-x-0 h-1.5 rounded-full bg-[hsl(var(--secondary))]" />
        <div
          className="absolute left-0 h-1.5 rounded-full bg-[hsl(var(--primary))]"
          style={{ width: `${percentOfOffset(selectedOffset)}%` }}
        />
        {todayTickOffset < maxOffset && (
          <div
            aria-hidden="true"
            data-testid="time-slider-future-hatch"
            className="absolute right-0 h-1.5 rounded-r-full"
            style={{
              left: `${percentOfOffset(todayTickOffset)}%`,
              backgroundImage: FUTURE_HATCH_BACKGROUND,
            }}
          />
        )}
        <div
          aria-hidden="true"
          data-testid="time-slider-today-tick"
          className="absolute h-3.5 w-px -translate-x-1/2 bg-[hsl(var(--foreground))]"
          style={{ left: `${percentOfOffset(todayTickOffset)}%` }}
        />
        <input
          type="range"
          className="time-slider-range absolute inset-x-0 w-full"
          min={0}
          max={maxOffset}
          step={1}
          value={selectedOffset}
          aria-label="Selected date"
          aria-valuetext={selectedDate}
          onChange={(event) => handleOffsetChange(Number(event.target.value))}
        />
      </div>

      <div className="relative h-4">
        <span
          className="map-popup-meta absolute whitespace-nowrap"
          style={{ left: `${percentOfOffset(todayTickOffset)}%`, transform: "translateX(-50%)" }}
          data-testid="time-slider-today-label"
        >
          Today {today}
        </span>
      </div>
      <div className="flex justify-between">
        <span className="map-popup-meta">{firstDay}</span>
        <span className="map-popup-meta">{lastDay}</span>
      </div>
      {/* The bucketing rule is stated, not assumed. A tick is the calendar day the PUBLISHER
          stamped an observation with, which for USGS gauges is their own local day and not
          UTC -- 37.5% of stored gauge readings fall on a different UTC day than the one their
          timestamp names, so an unlabelled axis would silently disagree with the source a
          user cross-checks. "Today" is still the server's UTC day; the tick marks it. */}
      <p className="map-popup-meta" data-testid="time-slider-axis-rule">
        One tick is one calendar day as the data publisher dated it.
      </p>

      {/* Names what the list is about. Without it a row reading "Not yet observed at this
          date" beside a map drawing points is read as a claim about those points. */}
      <p className="map-popup-meta mt-2" data-testid="time-slider-record-heading">
        Warehouse record at this date
      </p>

      <ul className="mt-1 flex flex-col gap-1" aria-label="Layer availability">
        {layerRows.map(({ layer, availability }) => {
          const message = availabilityMessage(availability, layer, activeVariantLabel);
          const isPublished = availability === "published";
          return (
            <li
              key={layer.layerName}
              data-testid={`layer-availability-${layer.layerName}`}
              data-availability={availability}
              className={`flex items-baseline justify-between gap-2 text-xs ${
                isPublished ? "text-[hsl(var(--foreground))]" : "opacity-60"
              }`}
            >
              <span
                className={isPublished ? undefined : "text-[hsl(var(--muted-foreground))]"}
              >
                {layer.layerName}
              </span>
              {message !== null && (
                <span className="map-popup-meta text-right">{message}</span>
              )}
            </li>
          );
        })}
      </ul>

      {isFuture && (
        <div
          className="mt-2 border-t border-[hsl(var(--border))] pt-2"
          data-testid="forecast-band-key"
        >
          <span className="text-xs font-semibold uppercase tracking-wide text-[hsl(var(--muted-foreground))]">
            Forecast band
          </span>
          <div className="mt-1 flex items-center gap-2">
            <span
              aria-hidden="true"
              className="h-3 w-8 shrink-0 rounded-sm bg-[hsl(var(--primary))]"
            />
            <span className="map-popup-meta">Narrow band, drawn solid: a confident forecast</span>
          </div>
          <div className="mt-1 flex items-center gap-2">
            <span
              aria-hidden="true"
              className="h-3 w-8 shrink-0 rounded-sm bg-[hsl(var(--primary))]/25"
            />
            <span className="map-popup-meta">Wide band, washed out: an uncertain forecast</span>
          </div>
          <p className="map-popup-meta">
            Colour is the median value; opacity is (high - low) normalised, so a wide
            uncertainty band fades the feature out.
          </p>
          <p className="map-popup-meta">
            Forecast days draw a dashed outline where observed days draw a solid one, and carry
            no isolines: a contour would claim a precision the band does not support.
          </p>
        </div>
      )}
    </div>
  );
}
