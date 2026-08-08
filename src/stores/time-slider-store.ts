import { create } from "zustand";
import { devtools } from "zustand/middleware";
import type {
  ForecastVariant,
  MetricAtDateAvailability,
  MetricVariant,
  SliderCapabilities,
  SliderLayerCapability,
} from "@/types/time-slider";

/** selectedDate before the server supplies capabilities; never a valid YYYY-MM-DD. */
export const UNINITIALIZED_DATE = "uninitialized";

const CALENDAR_DATE_PATTERN = /^(\d{4})-(\d{2})-(\d{2})$/;
const MILLISECONDS_PER_DAY = 86_400_000;

/** The day range the slider spans; every end derived from the payload, never a literal. */
export interface SliderDomain {
  firstDay: string;
  today: string;
  lastDay: string;
}

/** True when the string is a well-formed YYYY-MM-DD calendar date. */
export function isCalendarDate(date: string): boolean {
  return CALENDAR_DATE_PATTERN.test(date);
}

/** UTC midnight of a YYYY-MM-DD string, or NaN when it is not one. */
function toUtcMilliseconds(date: string): number {
  const parts = CALENDAR_DATE_PATTERN.exec(date);
  if (parts === null) return Number.NaN;
  return Date.UTC(Number(parts[1]), Number(parts[2]) - 1, Number(parts[3]));
}

/** Adds whole days in UTC. Returns the input unchanged when it is not a calendar date. */
export function addDays(date: string, dayCount: number): string {
  const startMilliseconds = toUtcMilliseconds(date);
  if (Number.isNaN(startMilliseconds)) return date;
  return new Date(startMilliseconds + dayCount * MILLISECONDS_PER_DAY)
    .toISOString()
    .slice(0, 10);
}

/** Whole days from fromDate to toDate in UTC. Returns 0 when either is not a calendar date. */
export function dayOffset(fromDate: string, toDate: string): number {
  const fromMilliseconds = toUtcMilliseconds(fromDate);
  const toMilliseconds = toUtcMilliseconds(toDate);
  if (Number.isNaN(fromMilliseconds) || Number.isNaN(toMilliseconds)) return 0;
  return Math.round((toMilliseconds - fromMilliseconds) / MILLISECONDS_PER_DAY);
}

/**
 * Both ends of the slider, derived from the payload; null when no layer has history.
 *
 * `lastDay` is the further of the longest forecast horizon and the axis's own future span.
 * Those are two different claims and the max is deliberate: the horizon says how far a layer
 * can be ANSWERED for, `futureAxisDays` says how far the axis is DRAWN. Today they disagree --
 * every horizon is 0 while the axis extends 30 days -- so the band right of today is drawn but
 * answers nothing, which is the honest state. When a forecast producer lands and raises a
 * horizon past the axis span, the axis grows to contain it rather than truncating it.
 */
export function sliderDomain(capabilities: SliderCapabilities | null): SliderDomain | null {
  if (capabilities === null) return null;
  const startsOf = (layers: SliderLayerCapability[]): string[] =>
    layers
      .map((layer) => layer.earliestObservedDate)
      .filter((date): date is string => date !== null && isCalendarDate(date));

  // A snapshot does not define a time axis. It is one state of the world that happens to carry a
  // publication date, so the date says when it was PUBLISHED, not that anything was observed
  // changing from then on -- and letting it set the axis start advertises years the slider cannot
  // actually show anything moving across.
  //
  // Measured when `watersheds` was persisted (0017): 96% of the HUC12 basins carry a single WBD
  // loaddate of 2013-01-18 and the rest are scattered ones and twos through 2018. Counting those
  // would have pulled the axis from 2022-08-05 back past 2018 -- roughly doubling it -- for a
  // static boundary set that draws identically on every one of those days.
  //
  // Falls back to every layer when nothing varies over time, because a domain derived from
  // snapshots alone is still better than no slider at all.
  const timeVaryingStarts = startsOf(
    capabilities.layers.filter((layer) => layer.temporalKind !== "snapshot")
  );
  const observedStarts = timeVaryingStarts.length > 0 ? timeVaryingStarts : startsOf(capabilities.layers);
  if (observedStarts.length === 0) return null;
  const firstDay = observedStarts.reduce((earliest, candidate) =>
    candidate < earliest ? candidate : earliest
  );
  const longestHorizonDays = capabilities.layers.reduce(
    (longest, layer) => Math.max(longest, layer.forecastHorizonDays),
    0
  );
  // Guarded rather than trusted: a payload from an older server carries no futureAxisDays at
  // all, and a negative one would put lastDay before today and invert the whole axis.
  const futureAxisDays = Math.max(0, capabilities.futureAxisDays ?? 0);
  return {
    firstDay,
    today: capabilities.serverCurrentDate,
    lastDay: addDays(
      capabilities.serverCurrentDate,
      Math.max(longestHorizonDays, futureAxisDays)
    ),
  };
}

/** The largest integer day offset from firstDay the slider may take. */
export function sliderMaxOffset(domain: SliderDomain): number {
  return dayOffset(domain.firstDay, domain.lastDay);
}

/** The integer day offset of the server's today; where the hatched future region starts. */
export function todayOffset(domain: SliderDomain): number {
  return dayOffset(domain.firstDay, domain.today);
}

/** Holds a date inside the domain. Returns the input when there is no domain to clamp to. */
export function clampDateToDomain(
  date: string,
  capabilities: SliderCapabilities | null
): string {
  const domain = sliderDomain(capabilities);
  if (domain === null || !isCalendarDate(date)) return date;
  if (date < domain.firstDay) return domain.firstDay;
  if (date > domain.lastDay) return domain.lastDay;
  return date;
}

/** Strictly after the server's today. Drives hatching and the variant toggle's enablement. */
export function isFutureDate(date: string, capabilities: SliderCapabilities): boolean {
  return date > capabilities.serverCurrentDate;
}

/** The capability row for a geo.layers name, or null when the server published none. */
export function findLayerCapability(
  capabilities: SliderCapabilities | null,
  layerName: string
): SliderLayerCapability | null {
  if (capabilities === null) return null;
  return capabilities.layers.find((layer) => layer.layerName === layerName) ?? null;
}

/** Which series a date reads from: observations up to today, the chosen forecast after. */
export function resolveVariant(
  date: string,
  capabilities: SliderCapabilities,
  forecastVariant: ForecastVariant
): MetricVariant {
  return isFutureDate(date, capabilities) ? forecastVariant : "observed";
}

/**
 * Availability decided client-side before any request, so an event layer under a future
 * date short-circuits instead of round-tripping. Most specific reason wins.
 */
export function layerAvailabilityAt(
  layer: SliderLayerCapability,
  date: string,
  variant: ForecastVariant,
  capabilities: SliderCapabilities
): MetricAtDateAvailability {
  // No versions at all means no date has been observed yet.
  if (layer.earliestObservedDate === null) return "not_yet_observed";
  if (date < layer.earliestObservedDate) return "not_yet_observed";
  if (!isFutureDate(date, capabilities)) return "published";
  if (layer.temporalKind === "event") return "not_forecastable";
  if (layer.forecastHorizonDays === 0) return "not_forecastable";
  if (date > addDays(capabilities.serverCurrentDate, layer.forecastHorizonDays)) {
    return "beyond_horizon";
  }
  if (!layer.forecastVariants.includes(variant)) return "variant_unavailable";
  return "published";
}

/** Human-readable reason a layer is empty, so an empty layer never reads as a hidden one. */
export function describeAvailability(
  availability: MetricAtDateAvailability,
  layerName: string
): string | null {
  switch (availability) {
    case "published":
      return null;
    case "not_yet_observed":
      return `${layerName} has no observations this far back.`;
    case "not_forecastable":
      return `${layerName} is not forecast beyond today.`;
    case "beyond_horizon":
      return `${layerName} is not forecast this far ahead.`;
    case "variant_unavailable":
      return `${layerName} does not publish this forecast variant.`;
    case "not_published":
      return `${layerName} has nothing published for this date.`;
    case "request_failed":
      return `${layerName} could not be loaded for this date. This is a loading failure, not a gap in the record.`;
  }
}

interface TimeSliderState {
  /** YYYY-MM-DD, or UNINITIALIZED_DATE until capabilities arrive. */
  selectedDate: string;
  forecastVariant: ForecastVariant;
  capabilities: SliderCapabilities | null;
  /**
   * True only when getSliderCapabilities has never once succeeded and its most recent
   * attempt failed -- e.g. the read-model 500. TimeSliderPanel is the sole writer and
   * deliberately does NOT set this on a background-refetch failure once capabilities already
   * exist: the last known-good payload keeps the slider working, and flipping a working
   * slider into an error state over a transient poll would be worse than the silence this
   * flag exists to fix. See TimeSlider's early-return branch that reads it.
   */
  capabilitiesUnavailable: boolean;

  setSelectedDate: (date: string) => void;
  setForecastVariant: (variant: ForecastVariant) => void;
  setCapabilities: (capabilities: SliderCapabilities) => void;
  setCapabilitiesUnavailable: (unavailable: boolean) => void;
  resetToToday: () => void;
}

export const useTimeSliderStore = create<TimeSliderState>()(
  devtools((set) => ({
    selectedDate: UNINITIALIZED_DATE,
    forecastVariant: "monte_carlo",
    capabilities: null,
    capabilitiesUnavailable: false,

    setSelectedDate: (date) => set({ selectedDate: date }),
    setForecastVariant: (variant) => set({ forecastVariant: variant }),

    // The payload is the only source of an initial selectedDate; a later payload can
    // move the domain out from under an existing selection, so clamp rather than reset.
    setCapabilities: (capabilities) =>
      set((state) => ({
        capabilities,
        // A payload landed, however late: whatever the fetch history was, it is no longer
        // true that the slider has nothing to show.
        capabilitiesUnavailable: false,
        selectedDate: clampDateToDomain(
          state.selectedDate === UNINITIALIZED_DATE
            ? capabilities.serverCurrentDate
            : state.selectedDate,
          capabilities
        ),
      })),

    setCapabilitiesUnavailable: (unavailable) => set({ capabilitiesUnavailable: unavailable }),

    resetToToday: () =>
      set((state) =>
        state.capabilities === null
          ? state
          : { selectedDate: state.capabilities.serverCurrentDate }
      ),
  }))
);
