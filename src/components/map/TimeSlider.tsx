"use client";

import { useId, useMemo, useState } from "react";
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
 *
 * Carries no positioning of its own any more. This was `absolute bottom-24 left-1/2
 * -translate-x-1/2` -- a card floating over the middle of the canvas -- until 2026-08-05,
 * when the slider became the global time marker pinned at the top of the right-hand panel
 * region. The anchor now belongs to that region's shell (`TimeSliderPanel`), which is also
 * what makes the invariant above hold: both states are `w-full` inside one anchored column,
 * so neither can be positioned independently of the other by accident.
 */
const TIME_SLIDER_CONTAINER_CLASSES =
  "w-full rounded-(--radius) border border-[hsl(var(--border))] bg-[hsl(var(--card))]/90 p-3 shadow-lg backdrop-blur-sm";

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
    /* Taller than the 18px visual track/thumb on purpose: this is the touch hit area for
       the one drag control in a compact dock, so it grows without changing what's drawn.
       The (max-width: 640px) block in src/styles/globals.css raises it to 44px on a phone. */
    height: 28px;
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

/**
 * Horizontal anchor for a label pinned to a percentage along the track. Centred in the middle
 * of the range; at either end the label's own edge is anchored to the tick instead, so it can
 * never overhang the panel. The 12%/88% cutoffs are where a ~80px label stops fitting either
 * side of the tick in the 384px right-hand region -- the narrowest place this renders.
 */
function todayLabelTransform(percent: number): string {
  if (percent >= 88) return "translateX(-100%)";
  if (percent <= 12) return "translateX(0)";
  return "translateX(-50%)";
}

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
  // Collapsed by default: the always-visible part is the date, observed/forecast state, the
  // track and the today tick. The per-layer record is real but secondary detail, and living
  // behind a disclosure is what keeps this a compact top section of the right-hand panel
  // region rather than a column-length list the panel body has to scroll past.
  const [isRecordListExpanded, setIsRecordListExpanded] = useState(false);
  const recordListId = useId();
  // `null` means "no explicit choice yet": the band key then defaults to expanded whenever
  // the selection is in the future, so someone who has actively picked a forecast day is not
  // made to find an extra disclosure just to read what the band means. Once toggled, the
  // choice sticks across further date changes -- an escape hatch to reclaim the vertical
  // space this block costs on a short viewport, not a one-shot animation.
  const [bandKeyExpandedOverride, setBandKeyExpandedOverride] = useState<boolean | null>(null);
  const bandKeyId = useId();

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
  const isBandKeyExpanded = bandKeyExpandedOverride ?? isFuture;
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
                  // max-sm:min-h-11 -- 44px tap target at small viewports, same rule the
                  // panel rail and the sheet's close button follow. Desktop stays compact.
                  className={`rounded-(--radius) border px-2.5 py-1.5 text-xs disabled:cursor-not-allowed disabled:opacity-50 max-sm:min-h-11 max-sm:px-3 ${
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

      {/* h-7 matches the 28px range hit area; max-sm:h-11 matches the 44px it grows to under
          the (max-width: 640px) rule in globals.css, so the row still contains its own input
          on a phone rather than letting it overflow the track. */}
      <div className="relative flex h-7 items-center max-sm:h-11">
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

      {/* The label is centred on the today tick, EXCEPT near either end, where centring would
          hang half of it outside the panel. Measured in production at 384px wide with today at
          the domain's right edge: it overflowed by 27px and gave the whole region a horizontal
          scrollbar. Anchoring its trailing edge to the tick instead keeps it attached to the
          thing it names while staying inside -- clipping it would hide the date, and letting
          the panel scroll sideways is what this replaces. */}
      <div className="relative h-4">
        <span
          className="map-popup-meta absolute whitespace-nowrap"
          style={{
            left: `${percentOfOffset(todayTickOffset)}%`,
            transform: todayLabelTransform(percentOfOffset(todayTickOffset)),
          }}
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

      {/* Collapsed by default so the time section stays compact: the per-layer record is
          real detail, not part of the always-visible marker (date, observed/forecast state,
          track, today tick). A real <button> with aria-expanded/aria-controls keeps this
          keyboard- and screen-reader-operable rather than a hover-only affordance. Naming
          what the list is about matters even collapsed: without it a row reading "Not yet
          observed at this date" beside a map drawing points would be read as a claim about
          those points once expanded. */}
      <button
        type="button"
        aria-expanded={isRecordListExpanded}
        aria-controls={recordListId}
        onClick={() => setIsRecordListExpanded((expanded) => !expanded)}
        className="map-popup-meta mt-2 flex w-full items-center gap-1 py-1 text-left max-sm:min-h-11"
        data-testid="time-slider-record-heading"
      >
        <span
          aria-hidden="true"
          className={`inline-block transition-transform ${isRecordListExpanded ? "rotate-90" : ""}`}
        >
          &#9656;
        </span>
        Warehouse record at this date
      </button>

      <ul
        id={recordListId}
        data-testid="time-slider-record-list"
        // Not the native `hidden` attribute plus a `flex` class: Tailwind's `.flex` utility
        // is an author-origin rule, the UA's `[hidden]{display:none}` is user-agent-origin,
        // and author beats user-agent at equal specificity -- `flex` would win and the list
        // would stay visible. Swapping the whole className instead means only one display
        // rule is ever present.
        className={isRecordListExpanded ? "mt-1 flex flex-col gap-1" : "hidden"}
        aria-label="Layer availability"
      >
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
        <div className="mt-2 border-t border-[hsl(var(--border))] pt-2">
          {/* Real <button>, keyboard- and screen-reader-operable, same as the warehouse
              record disclosure above. It defaults open on a future date (isBandKeyExpanded
              follows isFuture until the user overrides it) so the content is not hidden from
              someone who has actively chosen a forecast day -- the toggle exists so it can be
              collapsed to reclaim vertical space on a short viewport, not to hide it by
              default. */}
          <button
            type="button"
            aria-expanded={isBandKeyExpanded}
            aria-controls={bandKeyId}
            onClick={() => setBandKeyExpandedOverride(!isBandKeyExpanded)}
            className="flex w-full items-center gap-1 py-1 text-left text-xs font-semibold uppercase tracking-wide text-[hsl(var(--muted-foreground))] max-sm:min-h-11"
            data-testid="forecast-band-key-heading"
          >
            <span
              aria-hidden="true"
              className={`inline-block transition-transform ${isBandKeyExpanded ? "rotate-90" : ""}`}
            >
              &#9656;
            </span>
            Forecast band
          </button>
          <div
            id={bandKeyId}
            data-testid="forecast-band-key"
            // Same className-swap idiom as the warehouse record list: the native `hidden`
            // attribute would lose to an author-origin `flex` utility at equal specificity,
            // so only one display rule is ever present.
            className={isBandKeyExpanded ? "mt-1 flex flex-col gap-1" : "hidden"}
          >
            <div className="flex items-center gap-2">
              <span
                aria-hidden="true"
                className="h-3 w-8 shrink-0 rounded-sm bg-[hsl(var(--primary))]"
              />
              <span className="map-popup-meta">Narrow band, drawn solid: a confident forecast</span>
            </div>
            <div className="flex items-center gap-2">
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
              Forecast days draw a dashed outline where observed days draw a solid one, and
              carry no isolines: a contour would claim a precision the band does not support.
            </p>
          </div>
        </div>
      )}
    </div>
  );
}
