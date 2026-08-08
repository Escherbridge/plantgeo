"use client";

import { useId, useMemo, useState } from "react";
import { layerLabel, toggleIdForWarehouseLayerName } from "@/lib/map/layer-registry";
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
  findLayerCapability,
  focusedResourceDomain,
  isCalendarDate,
  isFutureDate,
  layerAvailabilityAt,
  sliderDomain,
  sliderMaxOffset,
  todayOffset,
  useTimeSliderStore,
} from "@/stores/time-slider-store";

/**
 * The band drawn right of the today tick: a flat grey wash with diagonal stripes over it.
 *
 * Colour AND texture, deliberately both. Colour is what the eye reads first and is what makes
 * the two halves of the track legible at a glance; the stripes are what survives a monochrome
 * display and the red-green deficiencies that would otherwise flatten this grey against the
 * green progress fill beside it. Neither alone is sufficient.
 *
 * Grey rather than a warning colour: right of today means "the record stops here", which is a
 * statement about coverage, not a fault. It is also the one hue in the palette that cannot be
 * confused with the observed fill -- `--accent` resolves to exactly `--primary` in both themes,
 * so an accent-coloured band would be invisible as a distinction.
 */
const FUTURE_BAND_BACKGROUND_COLOR = "hsl(var(--muted-foreground) / 0.35)";
const FUTURE_BAND_HATCH_IMAGE =
  "repeating-linear-gradient(45deg, hsl(var(--muted-foreground) / 0.55) 0 3px, transparent 3px 6px)";

/**
 * Shared by the slider panel and its unavailable-state notice, so a fetch that later
 * succeeds does not reposition anything -- both render at the same anchor, sized the same.
 *
 * Carries no positioning of its own any more. This was `absolute bottom-24 left-1/2
 * -translate-x-1/2` -- a card floating over the middle of the canvas -- until 2026-08-05,
 * when the slider became the global time marker pinned at the top of the right-hand panel
 * region. Since 2026-08-08 the card lives in the left dock's Time section, at the top of the
 * dock's one scroller; the anchor belongs to that scroller, which is also what makes the
 * invariant above hold: both states are `w-full` inside one column, so neither can be
 * positioned independently of the other by accident.
 *
 * No `overflow-*` and no `max-h-*`, deliberately: nested in the dock either would be the
 * second-scrollbar defect `layer-panel/panel-scroll.ts` rule 2 names, and the one drag control
 * on this card is exactly what a nested scroller makes unreachable on a phone.
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
  /* Without this the UA paints the calendar glyph and the spin controls for a light page,
     which on the default dark theme is a near-black icon on a near-black field -- the picker
     is there but invisible, so the field reads as plain text and nobody clicks it. The token
     is the same one the theme already switches on, so this follows light mode too. */
  .time-slider-date-input {
    color-scheme: light dark;
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
 * The name a reader already knows a resource by, resolved through the layer registry.
 *
 * The picker below sits inches above the dock's own layer tree, which reads every row's name
 * from `LAYER_REGISTRY[toggleId].label`. Humanizing the warehouse name here instead would put
 * "Fire perimeters" in this list beside "Active Fire Perimeters" in that one, for the same
 * layer -- a second display vocabulary for a thing that already has a canonical name.
 *
 * `humanizeLayerName` stays as the fallback rather than being dropped: a capability row is a
 * `geo.layers` publication, and not every publication has a renderer to carry a registry label
 * (`toggleIdForWarehouseLayerName` returns null for those). Such a layer is still on the axis
 * and must still be nameable.
 */
function resourceLabel(layerName: string): string {
  const toggleId = toggleIdForWarehouseLayerName(layerName);
  return toggleId === null ? humanizeLayerName(layerName) : layerLabel(toggleId);
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
 *
 * Since 2026-08-08 the axis is optionally per resource: the picker at the top re-ranges the
 * track to one layer's actuals plus that layer's own forecast horizon. It is a view over the
 * CONTROL, never a filter over the map -- `selectedDate` still applies to every layer, and the
 * caption beside the ends says so. The card is presentational and holds no tRPC of its own, so
 * it renders against a fixture without a provider; `TimeSliderCapabilitiesLoader` is the one
 * component that reads capabilities and the one writer of them.
 */
export default function TimeSlider({ layerNames, className }: TimeSliderProps) {
  const selectedDate = useTimeSliderStore((state) => state.selectedDate);
  const forecastVariant = useTimeSliderStore((state) => state.forecastVariant);
  const capabilities = useTimeSliderStore((state) => state.capabilities);
  const capabilitiesUnavailable = useTimeSliderStore((state) => state.capabilitiesUnavailable);
  const setSelectedDate = useTimeSliderStore((state) => state.setSelectedDate);
  const setForecastVariant = useTimeSliderStore((state) => state.setForecastVariant);
  // Which resource's record the axis is drawn for; null is "All resources", the whole-warehouse
  // axis this control has always drawn. It re-ranges the TRACK and nothing else -- see the
  // caption below, and the store field's own doc comment.
  const focusedLayerName = useTimeSliderStore((state) => state.focusedLayerName);
  const setFocusedLayerName = useTimeSliderStore((state) => state.setFocusedLayerName);
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
  const dateInputId = useId();
  const resourceSelectId = useId();
  const resetToToday = useTimeSliderStore((state) => state.resetToToday);
  // The date field's own value while it is being edited. A `<input type="date">` reports "" for
  // every incomplete entry, so a directly-bound store value would push the map to a clamped day
  // on the first keystroke of a typed date. This holds the in-progress text; only a complete,
  // in-domain day is committed through to the store.
  const [draftDate, setDraftDate] = useState(selectedDate);
  // Adjusted during render rather than in an effect, which is React's own prescription for
  // state that has to follow an external value: an effect would paint one frame of a stale
  // field first, and re-running on every pointer tick of a scrub is exactly the cascade the
  // lint rule warns about. Tracking the last day seen is what makes this fire only on a real
  // change -- a scrub, the Today button, the clamp a late capabilities payload applies -- and
  // never on the re-renders caused by editing the field itself.
  const [lastSeenSelectedDate, setLastSeenSelectedDate] = useState(selectedDate);
  if (selectedDate !== lastSeenSelectedDate) {
    setLastSeenSelectedDate(selectedDate);
    setDraftDate(selectedDate);
  }

  const domain = sliderDomain(capabilities, focusedLayerName);
  // Resolved rather than trusted: a focus can name a layer this payload no longer carries (an
  // ingest run dropped it, or a stale session held the name across a redeploy), in which case
  // the axis is already back on the global domain and the picker must read "All resources"
  // rather than showing a blank <select> for an option that does not exist.
  const focusedLayer =
    focusedLayerName === null ? null : findLayerCapability(capabilities, focusedLayerName);

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
  // Whether the focus actually moved the ends, asked of the one function that decides it rather
  // than by re-testing its conditions here -- a resource that defines no axis of its own (a
  // snapshot, one with no observed record, or, absurdly, one whose first day is after the
  // server's today) leaves the global axis drawn, and neither the caption nor the band key below
  // may claim otherwise. Computed after the guard above, where `capabilities` is known non-null.
  const focusedAxisApplies =
    focusedLayerName !== null && focusedResourceDomain(capabilities, focusedLayerName) !== null;
  // Whether a future day can be answered at all. Both halves are required: a horizon with no
  // variants and a variant with no horizon are each unreachable, and either alone would put a
  // picker on screen that can never change what is drawn.
  //
  // Scoped to the focused resource only when that resource's axis is the one drawn, because this
  // claim legends the band ON SCREEN: with the focused axis applied the band is that layer's own
  // horizon, so answering "Forecast" from some OTHER layer's capability would legend a band the
  // focused resource does not publish. When the focus defined no axis the track fell back to the
  // global domain, and the band drawn there is the whole warehouse's -- scoping the claim to the
  // focused layer would then legend a real, publishable future band "Nothing published".
  const forecastCandidates =
    focusedAxisApplies && focusedLayer !== null ? [focusedLayer] : capabilities.layers;
  const publishesAnyForecast = forecastCandidates.some(
    (layer) => layer.forecastHorizonDays > 0 && layer.forecastVariants.length > 0
  );

  /** Track position of a day offset, in percent. Guards the single-day domain. */
  const percentOfOffset = (offset: number): number =>
    maxOffset === 0 ? 0 : (offset / maxOffset) * 100;

  /** Writes through on every tick so the thumb tracks the pointer; the query debounces. */
  const handleOffsetChange = (nextOffset: number) => {
    // Whole days only. A fractional position would name a day nothing was observed on.
    const wholeDayOffset = Math.round(nextOffset);
    setSelectedDate(
      clampDateToDomain(addDays(firstDay, wholeDayOffset), capabilities, focusedLayerName)
    );
  };

  /**
   * Commits a typed day, and only a complete in-domain one.
   *
   * The draft always takes the keystroke, so the field stays editable; the store only hears
   * about days that are actually well formed. `min`/`max` on the input already refuse an
   * out-of-range day in every browser that implements them, but the clamp stays as the
   * authority -- the attributes are a hint the DOM can be driven around, and the domain is not.
   */
  const handleDateInputChange = (nextValue: string) => {
    setDraftDate(nextValue);
    if (!isCalendarDate(nextValue)) return;
    setSelectedDate(clampDateToDomain(nextValue, capabilities, focusedLayerName));
  };

  return (
    <div
      className={cn(TIME_SLIDER_CONTAINER_CLASSES, className)}
      data-testid="time-slider"
    >
      <style dangerouslySetInnerHTML={{ __html: timeSliderStyles }} />

      {/* What the selected day IS, and nothing else. This row carried an `<h2>Map date</h2>`
          until the card moved into the dock on 2026-08-08; the Time section's own header now
          says exactly that, one line above, so the heading was the same title printed twice.
          The chip stays because it is not a title: it is live state -- whether the day on this
          map is inside the record or past the end of it -- and it keeps the top-right corner
          it has always been read from. Right-aligned on its own row rather than folded beside
          the date field, which at 19rem already holds the field and the Today button. */}
      <div className="mb-2 flex items-baseline justify-end gap-2">
        <span
          data-testid="time-slider-day-kind"
          className={`rounded-(--radius) px-1.5 py-0.5 text-[0.6875rem] font-semibold uppercase tracking-wide ${
            isFuture
              ? "bg-[hsl(var(--muted-foreground))]/20 text-[hsl(var(--muted-foreground))]"
              : "bg-[hsl(var(--primary))]/15 text-[hsl(var(--primary))]"
          }`}
        >
          {isFuture ? "Beyond the record" : "Observed"}
        </span>
      </div>

      {/* Which resource's record the track spans. Before this, the axis was assembled from
          every published layer at once -- earliest observation anywhere on the left, longest
          horizon anywhere on the right -- so a reader asking "how far back does soil moisture
          go?" was shown four years of vegetation history with no way to tell the two apart.
          Picking a resource re-ranges the axis to that layer's own actuals plus its own
          forecast horizon; the caption below states, in the card, that this changes the RANGE
          and not what the map draws. */}
      <div className="mb-2 flex items-center gap-2">
        <label className="sr-only" htmlFor={resourceSelectId}>
          Resource
        </label>
        <select
          id={resourceSelectId}
          // Styled off the date field beside it, because they are two halves of one act:
          // which record, and which day of it. `color-scheme` comes along for the same reason
          // -- a UA-painted dropdown arrow drawn for a light page is invisible on the dark theme.
          className="time-slider-date-input min-w-0 flex-1 rounded-(--radius) border border-[hsl(var(--border))] bg-[hsl(var(--background))] px-2 py-1 text-sm font-medium text-[hsl(var(--foreground))] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[hsl(var(--ring))] max-sm:min-h-11"
          // The resolved layer, never the raw store value: a focus this payload does not carry
          // is already drawing the global axis, so the picker has to say "All resources" too.
          value={focusedLayer?.layerName ?? ""}
          data-testid="time-slider-resource-select"
          onChange={(event) =>
            setFocusedLayerName(event.target.value === "" ? null : event.target.value)
          }
        >
          <option value="">All resources</option>
          {capabilities.layers.map((layer) => (
            // Labelled off the registry, valued off the payload: the option text is the name the
            // dock's layer rows use for the same layer, while the value stays the raw
            // geo.layers name, which is the only vocabulary the capabilities payload speaks.
            <option key={layer.layerName} value={layer.layerName}>
              {resourceLabel(layer.layerName)}
            </option>
          ))}
        </select>
      </div>

      <div className="mb-2 flex items-center gap-2">
        {/* A real date field, not a read-out. Typing a day is the only practical way to reach
            one four years back: at ~384px wide the track gives roughly a quarter-pixel per
            day, so the thumb cannot address a specific date at all. `min`/`max` are the
            domain's own ends, so the browser refuses an out-of-range day before the store has
            to clamp one. */}
        <label className="sr-only" htmlFor={dateInputId}>
          Map date, YYYY-MM-DD
        </label>
        <input
          id={dateInputId}
          type="date"
          className="time-slider-date-input min-w-0 flex-1 rounded-(--radius) border border-[hsl(var(--border))] bg-[hsl(var(--background))] px-2 py-1 text-sm font-medium text-[hsl(var(--foreground))] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[hsl(var(--ring))] max-sm:min-h-11"
          value={draftDate}
          min={firstDay}
          max={lastDay}
          data-testid="time-slider-date-input"
          onChange={(event) => handleDateInputChange(event.target.value)}
          // A partially typed day leaves the input empty; restore the committed day on the way
          // out so the field can never be left blank next to a map that is drawing some date.
          onBlur={() => setDraftDate(selectedDate)}
        />
        <button
          type="button"
          onClick={() => resetToToday()}
          disabled={selectedDate === today}
          title={`Jump to ${today}`}
          className="shrink-0 rounded-(--radius) border border-[hsl(var(--border))] px-2 py-1 text-xs text-[hsl(var(--muted-foreground))] disabled:cursor-not-allowed disabled:opacity-50 max-sm:min-h-11 max-sm:px-3"
          data-testid="time-slider-today-button"
        >
          Today
        </button>
      </div>

      {/* Hidden entirely when nothing forecasts. Rendering two dead buttons was meant to show
          the capability being withheld, but it cost more than it bought: in production every
          layer reports `forecastVariants: []`, so the picker was permanently inert AND was the
          most prominent thing in the card. The withholding is still stated -- in the future
          band's key below, in words -- rather than as a control that cannot be operated. */}
      {publishesAnyForecast && (
        <div className="mb-2 flex flex-col items-end">
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
      )}

      {/* h-7 matches the 28px range hit area; max-sm:h-11 matches the 44px it grows to under
          the (max-width: 640px) rule in globals.css, so the row still contains its own input
          on a phone rather than letting it overflow the track. */}
      <div className="relative flex h-7 items-center max-sm:h-11">
        <div className="absolute inset-x-0 h-1.5 rounded-full bg-[hsl(var(--secondary))]" />
        {/* Painted before the progress fills so those draw on top of it. */}
        {todayTickOffset < maxOffset && (
          <div
            aria-hidden="true"
            data-testid="time-slider-future-hatch"
            className="absolute right-0 h-1.5 rounded-r-full"
            style={{
              left: `${percentOfOffset(todayTickOffset)}%`,
              backgroundColor: FUTURE_BAND_BACKGROUND_COLOR,
              backgroundImage: FUTURE_BAND_HATCH_IMAGE,
            }}
          />
        )}
        {/* Stops at today even when the selection is past it. The green fill means "observed",
            so running it into the future band would paint the one claim this control exists to
            avoid -- that there is a record over there. The scrubbed distance past today is
            shown by the grey fill below instead, in the band's own colour. */}
        <div
          aria-hidden="true"
          data-testid="time-slider-observed-fill"
          className="absolute left-0 h-1.5 rounded-l-full bg-[hsl(var(--primary))]"
          style={{
            width: `${percentOfOffset(Math.min(selectedOffset, todayTickOffset))}%`,
          }}
        />
        {selectedOffset > todayTickOffset && (
          <div
            aria-hidden="true"
            data-testid="time-slider-future-fill"
            className="absolute h-1.5 bg-[hsl(var(--muted-foreground))]"
            style={{
              left: `${percentOfOffset(todayTickOffset)}%`,
              width: `${percentOfOffset(selectedOffset) - percentOfOffset(todayTickOffset)}%`,
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

      {/* Says what the picker did, next to the ends it moved. Without it a narrowed axis reads
          as a filter -- "I picked soil moisture, so the map is showing me soil moisture" --
          which is exactly the claim this control must not make: there is one day on this map
          and every layer draws as of it. The second sentence is the whole point of the line.

          The second branch covers every resource that narrows nothing, and there are two kinds:
          one that has observed nothing at all, and a SNAPSHOT, which may carry a perfectly real
          publication date and still define no axis (watersheds' 2013 WBD loaddate is one state
          of the world, not six years of record). "No day-by-day record of its own" is true of
          both, where "has not observed anything" would be a falsehood about the second. */}
      {focusedLayer !== null && (
        <p className="map-popup-meta" data-testid="time-slider-focus-caption">
          {focusedAxisApplies
            ? `Range shown for ${resourceLabel(focusedLayer.layerName)}. The selected date still applies to every layer.`
            : `${resourceLabel(focusedLayer.layerName)} has no day-by-day record of its own, so the full range is shown. The selected date still applies to every layer.`}
        </p>
      )}

      {/* Names both halves of the track in words. The colours alone are a convention a first-
          time reader has no way to look up, and "is the right-hand end a prediction?" is
          precisely the question this control was failing to answer. Rendered whenever a future
          band exists at all, not only while the selection sits in it: the band is on screen
          either way, so its meaning has to be too. */}
      {todayTickOffset < maxOffset && (
        <div
          className="mt-1 flex items-center gap-3"
          data-testid="time-slider-track-key"
        >
          <span className="flex items-center gap-1.5">
            <span
              aria-hidden="true"
              className="h-1.5 w-4 shrink-0 rounded-full bg-[hsl(var(--primary))]"
            />
            <span className="map-popup-meta">Observed</span>
          </span>
          <span className="flex items-center gap-1.5">
            <span
              aria-hidden="true"
              className="h-1.5 w-4 shrink-0 rounded-full"
              style={{
                backgroundColor: FUTURE_BAND_BACKGROUND_COLOR,
                backgroundImage: FUTURE_BAND_HATCH_IMAGE,
              }}
            />
            <span className="map-popup-meta">
              {publishesAnyForecast ? "Forecast" : "Nothing published"}
            </span>
          </span>
        </div>
      )}

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

      {/* Nothing forecasts, and the selection is past today. The forecast-band key below would
          be a straight falsehood here -- it describes median colour and uncertainty opacity for
          a band no producer emits -- so this replaces it rather than sitting beside it. Saying
          it plainly is also the whole reason the two dead variant buttons could be dropped from
          the header: the capability is still visibly withheld, in a sentence that is true. */}
      {isFuture && !publishesAnyForecast && (
        <p
          className="mt-2 border-t border-[hsl(var(--border))] pt-2 text-xs text-[hsl(var(--muted-foreground))]"
          data-testid="time-slider-no-forecast-notice"
        >
          Nothing is published after {today}. This platform records what was measured and does
          not forecast, so every layer is empty here — that is the state of the record, not a
          failure to load.
        </p>
      )}

      {isFuture && publishesAnyForecast && (
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
