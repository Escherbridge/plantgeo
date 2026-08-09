import { create } from "zustand";
import { devtools } from "zustand/middleware";
import {
  LAYER_REGISTRY,
  isLayerToggleId,
  type LayerToggleId,
} from "@/lib/map/layer-registry";
import type {
  ForecastVariant,
  MetricAtDateAvailability,
  MetricVariant,
  SliderCapabilities,
  SliderLayerCapability,
} from "@/types/time-slider";

/** A layer's day when nothing can name one yet; never a valid YYYY-MM-DD. */
export const UNINITIALIZED_DATE = "uninitialized";

const CALENDAR_DATE_PATTERN = /^(\d{4})-(\d{2})-(\d{2})$/;
const MILLISECONDS_PER_DAY = 86_400_000;

/** The day range one layer's slider spans; every end derived from the payload, never a literal. */
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

/** The capability row for a geo.layers name, or null when the server published none. */
export function findLayerCapability(
  capabilities: SliderCapabilities | null,
  layerName: string
): SliderLayerCapability | null {
  if (capabilities === null) return null;
  return capabilities.layers.find((layer) => layer.layerName === layerName) ?? null;
}

/**
 * ONE layer's axis: its own actuals, plus the span drawn to the right of today. Null when the
 * layer defines no axis at all.
 *
 * Every layer row carries its own slider now, so there is no whole-warehouse axis left to fall
 * back to and this returns null rather than borrowing another layer's ends. A row whose domain
 * is null draws no track, which is the honest state: the alternative -- ranging a layer over
 * days belonging to some other layer's record -- was exactly the confusion that made the single
 * global axis unreadable, because most layers rendered empty across most of it.
 *
 * `lastDay` is the further of this layer's forecast horizon and the axis's own future span, and
 * the max is deliberate: the horizon says how far this layer can be ANSWERED for, `futureAxisDays`
 * says how far its track is DRAWN. Today they disagree -- every horizon is 0 while the axis
 * extends 30 days -- so the band right of today is drawn but answers nothing, which is the
 * honest state. Keeping the padding per row matters MORE than it did on the global axis: with
 * every horizon at 0, a row without it would end exactly at today and draw no observed/future
 * boundary at all, so "the record stops here" would again be a fact stated only in words.
 *
 * Null in four cases, none of them a throw:
 *
 * - The layer is unknown in this payload (a stale toggle, or a warehouse layer not yet published).
 * - It is a SNAPSHOT. A snapshot does not define a time axis: it is one state of the world that
 *   happens to carry a publication date, so the date says when it was PUBLISHED, not that
 *   anything was observed changing from then on. Measured when `watersheds` was persisted (0017):
 *   96% of its 9,396 HUC12 basins carry a single WBD loaddate of 2013-01-18 and the rest are
 *   scattered ones and twos through 2018. Ranging its row over those years would advertise six
 *   years of scrubbing across a static boundary set that draws identically on every one of them.
 * - It has observed nothing yet, so there is no record to range over.
 * - Its earliest observed day is somehow after the server's today, which would put `firstDay`
 *   past `lastDay` and invert every offset on the track.
 */
export function sliderDomain(
  capabilities: SliderCapabilities | null,
  layerName: string
): SliderDomain | null {
  if (capabilities === null) return null;
  const layer = findLayerCapability(capabilities, layerName);
  if (layer === null) return null;
  if (layer.temporalKind === "snapshot") return null;
  const firstDay = layer.earliestObservedDate;
  if (firstDay === null || !isCalendarDate(firstDay)) return null;
  if (firstDay > capabilities.serverCurrentDate) return null;
  // Both guarded rather than trusted: a payload from an older server carries no futureAxisDays
  // at all, and a negative value on either would put lastDay before today and invert the axis.
  const futureAxisDays = Math.max(0, capabilities.futureAxisDays ?? 0);
  const forecastHorizonDays = Math.max(0, layer.forecastHorizonDays);
  return {
    firstDay,
    today: capabilities.serverCurrentDate,
    lastDay: addDays(
      capabilities.serverCurrentDate,
      Math.max(forecastHorizonDays, futureAxisDays)
    ),
  };
}

/** The largest integer day offset from firstDay this layer's slider may take. */
export function sliderMaxOffset(domain: SliderDomain): number {
  return dayOffset(domain.firstDay, domain.lastDay);
}

/** The integer day offset of the server's today; where the hatched future region starts. */
export function todayOffset(domain: SliderDomain): number {
  return dayOffset(domain.firstDay, domain.today);
}

/**
 * Holds a date inside ONE layer's axis. Returns the input when that layer has no axis to
 * clamp to, which is the same answer as having no capabilities at all.
 */
export function clampDateToDomain(
  date: string,
  capabilities: SliderCapabilities | null,
  layerName: string
): string {
  const domain = sliderDomain(capabilities, layerName);
  if (domain === null || !isCalendarDate(date)) return date;
  if (date < domain.firstDay) return domain.firstDay;
  if (date > domain.lastDay) return domain.lastDay;
  return date;
}

/** Strictly after the server's today. Drives hatching and the variant toggle's enablement. */
export function isFutureDate(date: string, capabilities: SliderCapabilities): boolean {
  return date > capabilities.serverCurrentDate;
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

/**
 * True when `date` falls inside a range the layer reports as unpublished.
 *
 * Ranges are closed at both ends, as `DayRange` states, so a one-day gap arrives as
 * `{ from: d, to: d }` and both comparisons must be inclusive. String comparison is exact on
 * zero-padded YYYY-MM-DD and avoids re-parsing a value the server already validated.
 */
export function isWithinCoverageGap(layer: SliderLayerCapability, date: string): boolean {
  // Guarded rather than trusted: a payload from a server that predates the field carries no
  // array at all, and a missing gap list must read as "no gap known", never as a crash.
  const coverageGaps = layer.coverageGaps ?? [];
  return coverageGaps.some((gap) => date >= gap.from && date <= gap.to);
}

/**
 * True when this layer's `coverageGaps`/`thinRanges` describe `date`, so their SILENCE about it
 * is evidence rather than an absence of evidence.
 *
 * Ask this before turning "not in coverageGaps" into "published on that day". The server caps
 * both lists at the newest MAX_REPORTED_DAY_RANGES entries and reports the boundary in
 * `describedFromDay`; below it a dropped gap is indistinguishable from continuous coverage, and
 * reading the list anyway is what let the map paint an uningested day as solid and let the
 * agent be told "This is an observed absence: you may say there was none here".
 *
 * Guarded rather than trusted, like the range lists themselves: a payload from a server that
 * predates the field carries no boundary, and a missing boundary must read as "the whole axis
 * is described" -- which is what that older server meant -- never as a crash.
 */
export function isDayDescribed(layer: SliderLayerCapability, date: string): boolean {
  const describedFromDay = layer.describedFromDay ?? null;
  return describedFromDay === null || date >= describedFromDay;
}

/** The warehouse stream name behind a toggle, or null when no stream backs it. */
export function warehouseLayerNameFor(layerId: LayerToggleId): string | null {
  return LAYER_REGISTRY[layerId].warehouseLayerName;
}

/**
 * Whether this layer has a day a user can choose, and therefore both a control and a filter.
 *
 * THE RULE, and it is one rule on purpose: true exactly when the registry names a warehouse
 * stream for the toggle AND `sliderDomain` returns an axis for that stream -- i.e. the payload
 * carries the stream's capability, the stream is not a `snapshot`, it has an
 * `earliestObservedDate`, and that day is not after the server's today. False in every other
 * case, including while capabilities are still loading, because a layer whose axis nobody knows
 * has no day to offer.
 *
 * Both sides of the feature must ask THIS function and nothing else. They disagreed before:
 * whether a layer got a slider was snapshot-aware while whether its map read was date-filtered
 * was snapshot-blind, so a snapshot layer was filtered to a day its row gave no way to change,
 * and there was no single place where that could be noticed. A layer that is date-FILTERED on
 * the map must have a control; a layer with a control must be date-filtered. Never one without
 * the other.
 */
export function hasSelectableDay(
  capabilities: SliderCapabilities | null,
  layerId: LayerToggleId
): boolean {
  const layerName = warehouseLayerNameFor(layerId);
  if (layerName === null) return false;
  return sliderDomain(capabilities, layerName) !== null;
}

/**
 * The newest day this toggle's layer has published, or null when no capability names one.
 *
 * Null is a real answer and must stay distinguishable from a date: a toggle may name no
 * warehouse stream at all (the SoilGrids raster and the SSURGO proxy still do), and a stream the
 * warehouse has not published yet has no newest day either. Callers that would otherwise mark a
 * layer "not on its latest" must not make that claim about a layer whose latest nobody knows.
 *
 * Drought used to be the headline example here and no longer is: it is published as the
 * `drought-areas` stream, so it has a newest day like every other dated layer.
 */
export function latestObservedDateFor(
  capabilities: SliderCapabilities | null,
  layerId: LayerToggleId
): string | null {
  const layerName = warehouseLayerNameFor(layerId);
  if (layerName === null) return null;
  const capability = findLayerCapability(capabilities, layerName);
  if (capability === null) return null;
  const latestObservedDate = capability.latestObservedDate;
  if (latestObservedDate === null || !isCalendarDate(latestObservedDate)) return null;
  return latestObservedDate;
}

/**
 * The day ONE layer draws as of: its own override if it has one, else its own newest day.
 *
 * The fallback order is the whole feature. Before per-layer dates every layer shared "today",
 * and since only vegetation has four years of depth most layers correctly-but-confusingly
 * rendered empty; following `latestObservedDate` instead opens every layer on data.
 *
 * The last resort is the server's today, for a layer whose newest day nobody knows -- no
 * warehouse stream at all, or a stream that has published nothing. That is the day such a layer
 * already drew before this change, and it is the only day we can name without reading the
 * browser clock. `UNINITIALIZED_DATE` only when even that is unavailable, i.e. before the
 * capabilities payload lands; it is not a calendar date, so every consumer reports no day
 * rather than guessing one.
 */
export function resolveLayerDate(
  layerDates: Record<string, string>,
  capabilities: SliderCapabilities | null,
  layerId: LayerToggleId
): string {
  const ownDate = layerDates[layerId];
  if (ownDate !== undefined) return ownDate;
  const latestObservedDate = latestObservedDateFor(capabilities, layerId);
  if (latestObservedDate !== null) return latestObservedDate;
  return capabilities?.serverCurrentDate ?? UNINITIALIZED_DATE;
}

/** Holds a date inside one TOGGLE's axis, crossing to the warehouse name through the registry. */
function clampToLayerAxis(
  date: string,
  capabilities: SliderCapabilities | null,
  layerId: LayerToggleId
): string {
  const layerName = warehouseLayerNameFor(layerId);
  if (layerName === null) return date;
  return clampDateToDomain(date, capabilities, layerName);
}

/**
 * Holds every override inside its own layer's axis after that axis may have moved.
 *
 * Returns the SAME record when nothing moved, so the five-minute capabilities poll does not
 * hand every per-layer subscriber a fresh object for an answer that did not change.
 */
function clampLayerDatesToDomains(
  layerDates: Record<string, string>,
  capabilities: SliderCapabilities
): Record<string, string> {
  let anyDateMoved = false;
  const clamped: Record<string, string> = {};
  for (const [layerId, date] of Object.entries(layerDates)) {
    // A key that is not a registry toggle cannot be clamped -- there is no axis to clamp it to
    // -- but it is carried through rather than dropped, because dropping it here would silently
    // undo a user-uploaded layer's own day.
    const nextDate = isLayerToggleId(layerId)
      ? clampToLayerAxis(date, capabilities, layerId)
      : date;
    if (nextDate !== date) anyDateMoved = true;
    clamped[layerId] = nextDate;
  }
  return anyDateMoved ? clamped : layerDates;
}

/**
 * Per-layer days recovered from a previously stored blob, with a legacy global day REFUSED.
 *
 * Nothing persists this store today -- verified 2026-08-09 across every persistence surface the
 * app has: `plantgeo-layer-opacity` (layer-store) holds opacities keyed by toggle id,
 * `plantgeo-search` holds recent searches, and the IndexedDB `plantgeo-query-cache` behind
 * src/lib/cache/query-persister.ts keys entries by a tRPC queryHash that already embeds the day,
 * so a per-layer day hits or misses it exactly as the global day did. None of them ever carried
 * `selectedDate`. This function exists so that stays true by construction: it is the single
 * supported way to read a stored blob back into `layerDates`, and any persistence added later
 * must go through it rather than deserializing straight into state.
 *
 * A legacy `{ selectedDate }` blob is DROPPED, never fanned out across the layers. Fanning it out
 * would pin every layer to one stale day -- which is precisely the state this feature removes,
 * and it would arrive silently, as an override no one set. Dropping it returns every layer to
 * following its own newest published day, which is the documented default.
 */
export function readPersistedLayerDates(persisted: unknown): Record<string, string> {
  if (typeof persisted !== "object" || persisted === null) return {};
  const storedLayerDates = (persisted as { layerDates?: unknown }).layerDates;
  if (typeof storedLayerDates !== "object" || storedLayerDates === null) return {};
  const recovered: Record<string, string> = {};
  for (const [layerId, date] of Object.entries(storedLayerDates as Record<string, unknown>)) {
    if (!isLayerToggleId(layerId)) continue;
    if (typeof date !== "string" || !isCalendarDate(date)) continue;
    recovered[layerId] = date;
  }
  return recovered;
}

export interface TimeSliderState {
  /**
   * Each layer's own day, keyed by LayerToggleId. SPARSE by contract: an ABSENT entry means the
   * layer follows its own `latestObservedDate`, and that is the default.
   *
   * Never eagerly populated from capabilities. An eager copy is correct for exactly one payload:
   * the next refresh moves a layer's newest day and the copied value has silently become an
   * override, so the layer stops following its own record without anyone having touched its
   * slider -- and it stops in the one direction that hurts, pinned behind the live edge.
   */
  layerDates: Record<string, string>;
  forecastVariant: ForecastVariant;
  capabilities: SliderCapabilities | null;
  /**
   * True only when getSliderCapabilities has never once succeeded and its most recent
   * attempt failed -- e.g. the read-model 500. TimeSliderCapabilitiesLoader is the sole writer
   * and deliberately does NOT set this on a background-refetch failure once capabilities
   * already exist: the last known-good payload keeps every slider working, and flipping working
   * sliders into an error state over a transient poll would be worse than the silence this
   * flag exists to fix.
   */
  capabilitiesUnavailable: boolean;

  setLayerDate: (layerId: LayerToggleId, date: string) => void;
  /** Drop the override so the layer follows its own latest observed day again. */
  resetLayerDate: (layerId: LayerToggleId) => void;
  /** Return every layer to following its own newest day; the successor of `resetToToday`. */
  resetAllLayerDates: () => void;
  setForecastVariant: (variant: ForecastVariant) => void;
  setCapabilities: (capabilities: SliderCapabilities) => void;
  setCapabilitiesUnavailable: (unavailable: boolean) => void;
  /** The single supported path from a stored blob into `layerDates`. */
  hydratePersistedLayerDates: (persisted: unknown) => void;
}

export const useTimeSliderStore = create<TimeSliderState>()(
  devtools((set) => ({
    layerDates: {},
    forecastVariant: "monte_carlo",
    capabilities: null,
    capabilitiesUnavailable: false,

    // Clamped here rather than at the call site: this is the one writer, so nothing downstream
    // -- including a rehydrated blob replayed through it -- can leave a layer's thumb off its
    // own track. Mirrors layer-store's opacity clamp, for the same reason.
    setLayerDate: (layerId, date) =>
      set((state) => {
        // A day that is not a day cannot be honoured, and storing it would blank the layer --
        // which on the map is indistinguishable from a gap in the record. Dropping the override
        // instead returns the layer to its own newest day, a state the row can actually show.
        if (!isCalendarDate(date)) {
          if (state.layerDates[layerId] === undefined) return state;
          const withoutLayer = { ...state.layerDates };
          delete withoutLayer[layerId];
          return { layerDates: withoutLayer };
        }
        const clampedDate = clampToLayerAxis(date, state.capabilities, layerId);
        if (state.layerDates[layerId] === clampedDate) return state;
        return { layerDates: { ...state.layerDates, [layerId]: clampedDate } };
      }),

    // Deletes the key rather than writing today's date: "following its own latest" and
    // "explicitly pinned to a day that happens to be its latest" are different facts, and only
    // the first keeps tracking the live edge as later payloads land.
    resetLayerDate: (layerId) =>
      set((state) => {
        if (state.layerDates[layerId] === undefined) return state;
        const withoutLayer = { ...state.layerDates };
        delete withoutLayer[layerId];
        return { layerDates: withoutLayer };
      }),

    resetAllLayerDates: () =>
      set((state) =>
        Object.keys(state.layerDates).length === 0 ? state : { layerDates: {} }
      ),

    setForecastVariant: (variant) => set({ forecastVariant: variant }),

    // A later payload can move a layer's axis out from under a day the user picked, so clamp
    // rather than reset -- and clamp ONLY the overrides. Layers with no entry need no migration
    // at all: they resolve through the new payload's `latestObservedDate` on the next read,
    // which is exactly what keeps the sparse default following the live edge.
    setCapabilities: (capabilities) =>
      set((state) => ({
        capabilities,
        // A payload landed, however late: whatever the fetch history was, it is no longer
        // true that the sliders have nothing to show.
        capabilitiesUnavailable: false,
        layerDates: clampLayerDatesToDomains(state.layerDates, capabilities),
      })),

    setCapabilitiesUnavailable: (unavailable) => set({ capabilitiesUnavailable: unavailable }),

    hydratePersistedLayerDates: (persisted) =>
      set((state) => {
        const recovered = readPersistedLayerDates(persisted);
        return {
          layerDates:
            state.capabilities === null
              ? recovered
              : clampLayerDatesToDomains(recovered, state.capabilities),
        };
      }),
  }))
);
