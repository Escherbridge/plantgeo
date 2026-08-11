"use client";

/**
 * The layer toggle context: the one place the map and the sliders agree on what is switched
 * on, what mode it draws in, and which day EACH layer draws. It composes the existing Zustand
 * stores and owns no state of its own -- see src/components/map/AGENTS.md
 * "The layer toggle is the only source of layer visibility".
 *
 * Every day here is per layer. There is no map-wide day left to ask for: each row scrubs its
 * own axis and defaults to its own `latestObservedDate`, so a hook that answered "the map's
 * day" could only answer for one row and would silently mislabel the rest.
 */

import { useEffect, useMemo, useState } from "react";
import {
  LAYER_REGISTRY,
  LAYER_TOGGLE_IDS,
  type LayerToggleId,
} from "@/lib/map/layer-registry";
import { DEFAULT_LAYER_OPACITY } from "@/lib/map/layer-opacity";
import { climateRenderForm, useClimateStore } from "@/stores/climate-store";
import { useLayerStore } from "@/stores/layer-store";
import { useMapStore } from "@/stores/map-store";
import { useSoilStore } from "@/stores/soil-store";
import { SCRUB_SETTLE_MS } from "@/stores/useMetricAtDate";
import {
  describeAvailability,
  findLayerCapability,
  isCalendarDate,
  latestObservedDateFor,
  layerAvailabilityAt,
  resolveLayerDate,
  resolveVariant,
  useTimeSliderStore,
} from "@/stores/time-slider-store";
import { useVegetationStore } from "@/stores/vegetation-store";
import { resolveGibsNdviDate } from "@/lib/vegetation";
import type { SoilFieldDepth, SoilFieldMeasure } from "@/lib/environmental/soil-field";
import type {
  AirTemperatureVariant,
  ClimateFieldSignalId,
  ClimateRenderForm,
} from "@/lib/environmental/climate-field";
import type { SoilProperty } from "@/components/map/layers/SoilLayer";
import type { VegetationMode } from "@/components/map/layers/VegetationLayer";
import type {
  ForecastVariant,
  MetricAtDateAvailability,
  MetricVariant,
} from "@/types/time-slider";

/** Switch position of every registry layer, keyed by toggle id. */
export type LayerVisibility = Record<LayerToggleId, boolean>;

/** The whole toggle list, including user-uploaded layer ids the registry does not know. */
export function useActiveLayerToggles(): string[] {
  return useMapStore((state) => state.activeLayers);
}

/** The one writer of layer visibility. */
export function useToggleLayer(): (layerId: string) => void {
  return useMapStore((state) => state.toggleLayer);
}

/** One layer's switch position. Subscribes to that layer alone, not to the whole list. */
export function useLayerToggle(layerId: string): boolean {
  return useMapStore((state) => state.activeLayers.includes(layerId));
}

/**
 * Switch positions for every registry layer. A permanently withheld layer reads false even
 * if its id somehow reaches `activeLayers`, so governance cannot be bypassed by a stray write.
 */
export function useLayerVisibility(): LayerVisibility {
  const activeLayers = useActiveLayerToggles();
  return useMemo(() => {
    const visibility = {} as LayerVisibility;
    for (const toggleId of LAYER_TOGGLE_IDS) {
      visibility[toggleId] =
        LAYER_REGISTRY[toggleId].permanentlyUnavailableReason === null &&
        activeLayers.includes(toggleId);
    }
    return visibility;
  }, [activeLayers]);
}

/**
 * One layer's opacity multiplier. Subscribes to that number alone, so dragging one row's
 * slider re-renders one row rather than the whole tree.
 */
export function useLayerOpacity(layerId: LayerToggleId): number {
  return useLayerStore((state) => state.layerOpacity[layerId] ?? DEFAULT_LAYER_OPACITY);
}

/**
 * The multiplier for every registry layer, dense and memoized -- LayerManager's single read.
 *
 * Dense rather than sparse so consumers never repeat the `?? 1` fallback, and memoized on the
 * sparse record so a toggle that no one has moved hands back a referentially stable object
 * and does not re-run the applier effect.
 */
export function useLayerOpacities(): Record<LayerToggleId, number> {
  const layerOpacity = useLayerStore((state) => state.layerOpacity);
  return useMemo(() => {
    const opacities = {} as Record<LayerToggleId, number>;
    for (const toggleId of LAYER_TOGGLE_IDS) {
      opacities[toggleId] = layerOpacity[toggleId] ?? DEFAULT_LAYER_OPACITY;
    }
    return opacities;
  }, [layerOpacity]);
}

/** The one writer of layer opacity. Clamps in the store, never at the call site. */
export function useSetLayerOpacity(): (layerId: LayerToggleId, opacity: number) => void {
  return useLayerStore((state) => state.setLayerOpacity);
}

/** The day ONE layer draws as of, and which series that day reads from. */
export interface MapDay {
  /** YYYY-MM-DD, or null until something can name this layer's day. */
  selectedDate: string | null;
  /** Server UTC today; the only definition of "today". Null before capabilities arrive. */
  serverCurrentDate: string | null;
  /** True when this layer's day is some day other than the server's today. */
  isOffServerToday: boolean;
  /** This layer's own newest published day, or null when no capability names one. */
  latestObservedDate: string | null;
  /**
   * True only when this layer is PROVABLY behind its own newest published day.
   *
   * The map is a mixed-time composite now, so this drives the "not on its latest" mark on the
   * row -- and that mark is a positive claim. A layer whose newest day nobody knows (drought
   * has no geo.layers row; a layer the warehouse has not published yet has no latest either)
   * therefore reads false: there is nothing measured for it to be behind, and marking it would
   * assert staleness the client cannot see.
   */
  isBehindLatestObservedDate: boolean;
  /** The forecast series a future day would read from. */
  forecastVariant: ForecastVariant;
  /** What this layer's day actually reads from: observations up to today, forecast after. */
  variant: MetricVariant;
}

/**
 * One layer's own day. Subscribes to that day alone, so scrubbing fire's row does not
 * re-render vegetation's.
 *
 * The selector returns a STRING rather than the `layerDates` record: the record's identity
 * changes on every write to any layer, so selecting it would re-render every layer's consumers
 * on every pointer tick of any one layer's scrub. Zustand compares the selected value with
 * Object.is, and a day that did not move is the same string.
 */
export function useLayerDay(layerId: LayerToggleId): MapDay {
  const selectedDate = useTimeSliderStore((state) =>
    resolveLayerDate(state.layerDates, state.capabilities, layerId)
  );
  const forecastVariant = useTimeSliderStore((state) => state.forecastVariant);
  const capabilities = useTimeSliderStore((state) => state.capabilities);

  return useMemo(() => {
    const day = isCalendarDate(selectedDate) ? selectedDate : null;
    const serverCurrentDate = capabilities?.serverCurrentDate ?? null;
    const latestObservedDate = latestObservedDateFor(capabilities, layerId);
    return {
      selectedDate: day,
      serverCurrentDate,
      isOffServerToday:
        day !== null && serverCurrentDate !== null && day !== serverCurrentDate,
      latestObservedDate,
      isBehindLatestObservedDate:
        day !== null && latestObservedDate !== null && day < latestObservedDate,
      forecastVariant,
      variant:
        day !== null && capabilities !== null
          ? resolveVariant(day, capabilities, forecastVariant)
          : "observed",
    };
  }, [layerId, selectedDate, forecastVariant, capabilities]);
}

/** One layer's day read straight off the store, for the imperative paths below. */
function readLayerDateNow(layerId: LayerToggleId): string {
  const state = useTimeSliderStore.getState();
  return resolveLayerDate(state.layerDates, state.capabilities, layerId);
}

/**
 * Every registry layer's day in LAYER_TOGGLE_IDS order, joined into one comparable string.
 *
 * A joined key rather than a record because it is used as BOTH the change test and a
 * `useMemo` dependency, and neither works on an object minted per read. "|" cannot occur in a
 * YYYY-MM-DD day, so the join is lossless and the days split back out exactly.
 */
function readEveryLayerDateKey(): string {
  const state = useTimeSliderStore.getState();
  return LAYER_TOGGLE_IDS.map((toggleId) =>
    resolveLayerDate(state.layerDates, state.capabilities, toggleId)
  ).join("|");
}

/**
 * Every layer's day at once, re-rendered only once ALL scrubbing has settled.
 *
 * One subscription and one timer for the whole set, rather than `useSettledLayerDate` per
 * layer: hooks may not be called in a loop, and nothing is lost by settling them together
 * because the only consumer -- `useViewedLayerDays` -- reports every layer and would re-render
 * on any of them anyway. It settles on the same SCRUB_SETTLE_MS boundary from the same store
 * writes as the per-layer hook, so the two cannot disagree once motion stops; mid-scrub this
 * one may lag by up to one settle window, which is what "settled" means.
 */
function useSettledEveryLayerDateKey(): string {
  const [settledKey, setSettledKey] = useState(readEveryLayerDateKey);
  useEffect(() => {
    let settleTimer: ReturnType<typeof setTimeout> | null = null;
    let lastSeenKey = readEveryLayerDateKey();
    // Same first-paint race as `useSettledLayerDate`; see the comment there.
    setSettledKey(lastSeenKey);
    const unsubscribe = useTimeSliderStore.subscribe(() => {
      const nextKey = readEveryLayerDateKey();
      if (nextKey === lastSeenKey) return;
      lastSeenKey = nextKey;
      if (settleTimer !== null) clearTimeout(settleTimer);
      settleTimer = setTimeout(() => setSettledKey(readEveryLayerDateKey()), SCRUB_SETTLE_MS);
    });
    return () => {
      if (settleTimer !== null) clearTimeout(settleTimer);
      unsubscribe();
    };
  }, []);
  return settledKey;
}

/**
 * One layer's day, re-rendered only once THAT layer's scrub has settled.
 *
 * Deliberately NOT `useDebounce(useLayerDay(layerId).selectedDate, …)`. That subscribes to the
 * raw day, so every consumer re-renders on every pointer tick of a day-granular scrub even
 * though only the settled value is ever read -- and `LayerManager` has ~8 layer children behind
 * it. Reading the store imperatively and setting state only after the settle window gives one
 * render per settle instead of one per tick, on the SAME boundary `useMetricAtDate` debounces
 * to. That reasoning is unchanged by per-layer dates; only its scope narrows.
 *
 * The subscription still fires for every field of the store, because zustand has no per-key
 * subscribe. What is new is the comparison inside it: the subscriber resolves THIS layer's day
 * and returns immediately when it did not move, so a scrub on one row arms one timer instead of
 * one per mounted layer. Resolving on every notification rather than reading `layerDates` is
 * what makes an absent entry keep following `latestObservedDate` across a capabilities refresh.
 */
function useSettledLayerDate(layerId: LayerToggleId): string {
  const [settledDate, setSettledDate] = useState(() => readLayerDateNow(layerId));
  useEffect(() => {
    let settleTimer: ReturnType<typeof setTimeout> | null = null;
    let lastSeenDate = readLayerDateNow(layerId);
    // Adopts immediately rather than settling, and this line has to stay. It covers `layerId`
    // changing -- where waiting out a settle window would report the PREVIOUS layer's day for a
    // row nobody touched -- and, on first paint, a store write that lands BETWEEN this hook's
    // initial render and this subscription: `TimeSliderCapabilitiesLoader`'s effect can call
    // `setCapabilities` on the same commit that mounts a row, and if it runs first the row
    // would hold a pre-capabilities day that no later notification ever corrects.
    setSettledDate(lastSeenDate);
    const unsubscribe = useTimeSliderStore.subscribe(() => {
      const nextDate = readLayerDateNow(layerId);
      if (nextDate === lastSeenDate) return;
      lastSeenDate = nextDate;
      if (settleTimer !== null) clearTimeout(settleTimer);
      settleTimer = setTimeout(() => setSettledDate(readLayerDateNow(layerId)), SCRUB_SETTLE_MS);
    });
    return () => {
      if (settleTimer !== null) clearTimeout(settleTimer);
      unsubscribe();
    };
  }, [layerId]);
  return settledDate;
}

/** One layer's day as a request carries it: settled, and absent at the server's today. */
export interface DebouncedMapDay {
  /** YYYY-MM-DD, or null until something can name this layer's day. */
  settledDate: string | null;
  /** Server UTC today; the only definition of "today". Null before capabilities arrive. */
  serverCurrentDate: string | null;
  /** True when this layer's settled day is some day other than the server's today. */
  isOffServerToday: boolean;
  /**
   * What a reader's optional `date` input must carry, and `undefined` whenever the settled day
   * IS the server's today.
   *
   * The server treats an omitted day and today's date identically, so sending the date
   * explicitly would only mint a SECOND react-query entry for the same answer and make first
   * paint fetch it twice. `undefined` also covers "capabilities have not arrived": without them
   * nothing knows which day is today, and guessing from the browser clock is exactly the
   * timezone disagreement `serverCurrentDate` exists to prevent.
   *
   * Set far more often than it used to be, and that is correct: a layer now opens on its own
   * `latestObservedDate`, which for most layers is not today, so the day must ride along.
   */
  requestDate: string | undefined;
}

/**
 * The day one layer's warehouse-backed viewport queries key on. One hook per layer, so a
 * layer's map feed and its panel settle on the same boundary and land on the same cache entry
 * -- see src/lib/server/AGENTS.md §slider-day.
 */
export function useDebouncedLayerDay(layerId: LayerToggleId): DebouncedMapDay {
  const settledSelection = useSettledLayerDate(layerId);
  const capabilities = useTimeSliderStore((state) => state.capabilities);

  return useMemo(() => {
    const settledDate = isCalendarDate(settledSelection) ? settledSelection : null;
    const serverCurrentDate = capabilities?.serverCurrentDate ?? null;
    const isOffServerToday =
      settledDate !== null &&
      serverCurrentDate !== null &&
      settledDate !== serverCurrentDate;
    return {
      settledDate,
      serverCurrentDate,
      isOffServerToday,
      requestDate: isOffServerToday && settledDate !== null ? settledDate : undefined,
    };
  }, [settledSelection, capabilities]);
}

/** One visible layer's settled day, as the agent payload and the mixed-date report carry it. */
export interface ViewedLayerDay {
  layerId: LayerToggleId;
  /** The `geo.layers.name` behind this toggle, or null when no warehouse layer backs it. */
  warehouseLayerName: string | null;
  /** YYYY-MM-DD. Only layers whose day can actually be named appear in the list. */
  date: string;
  /** False ONLY when the layer is provably behind its own newest published day. */
  isOnLatest: boolean;
}

/**
 * Every visible layer's settled day, for the agent payload and for mixed-date reporting.
 *
 * The map is a mixed-time composite now: fire on 2026-08-07 beside vegetation on 2025-06-14
 * looks like one moment in a screenshot. Anything that describes what is on screen -- the agent
 * most of all -- has to be told which day each layer is actually showing, or it will answer
 * about a moment that never existed.
 *
 * A layer with no nameable day is OMITTED rather than reported with a sentinel: the list says
 * what is being drawn as of when, and "uninitialized" is not a day anyone is looking at.
 *
 * `isOnLatest` is true whenever the layer is not provably behind, including when its newest day
 * is unknown -- see `MapDay.isBehindLatestObservedDate` for why that asymmetry is deliberate.
 */
export function useViewedLayerDays(): ViewedLayerDay[] {
  const visibility = useLayerVisibility();
  const capabilities = useTimeSliderStore((state) => state.capabilities);
  const settledDateKey = useSettledEveryLayerDateKey();
  // Collapsed to the one fact this list reads, so toggling a layer that was already hidden
  // cannot rebuild it.
  const visibleLayerKey = LAYER_TOGGLE_IDS.filter((toggleId) => visibility[toggleId]).join("|");

  return useMemo(() => {
    // Both keys are re-expanded here rather than closed over, so every value this list is built
    // from is a declared dependency and no render can serve a day from a previous one.
    const settledDatesInRegistryOrder = settledDateKey.split("|");
    const visibleLayerIds = new Set(visibleLayerKey.split("|"));
    const viewed: ViewedLayerDay[] = [];
    LAYER_TOGGLE_IDS.forEach((toggleId, index) => {
      if (!visibleLayerIds.has(toggleId)) return;
      const date = settledDatesInRegistryOrder[index];
      if (date === undefined || !isCalendarDate(date)) return;
      const latestObservedDate = latestObservedDateFor(capabilities, toggleId);
      viewed.push({
        layerId: toggleId,
        warehouseLayerName: LAYER_REGISTRY[toggleId].warehouseLayerName,
        date,
        isOnLatest: latestObservedDate === null || date >= latestObservedDate,
      });
    });
    return viewed;
  }, [settledDateKey, visibleLayerKey, capabilities]);
}

/** Everything one layer needs to decide whether and how to draw. */
export interface LayerRenderState {
  toggleId: LayerToggleId;
  /** The `geo.layers.name` behind this toggle, or null when no warehouse layer backs it. */
  warehouseLayerName: string | null;
  /** The user's switch position. */
  isToggledOn: boolean;
  /** True when the layer should draw: switched on and not withheld. */
  shouldRender: boolean;
  /** The day this layer draws as of, or null until something can name one for it. */
  selectedDate: string | null;
  /** This layer's own newest published day, or null when no capability names one. */
  latestObservedDate: string | null;
  /** False unless the layer is provably behind its own newest day; drives the mixed-time mark. */
  isBehindLatestObservedDate: boolean;
  variant: MetricVariant;
  /** Whether the warehouse can answer for that day. */
  availability: MetricAtDateAvailability;
  /** Why the layer would be empty at this day, or null when it would not be. */
  unavailableReason: string | null;
  /** Set when governance withholds the layer at every date. */
  permanentlyUnavailableReason: string | null;
}

/**
 * Availability is advisory: it says why a day would come back empty, and `shouldRender`
 * deliberately ignores it. A layer that goes dark because the server has not published a
 * capability yet is indistinguishable from one the user switched off, which is the confusion
 * `describeAvailability` exists to prevent.
 */
export function useLayerRenderState(layerId: LayerToggleId): LayerRenderState {
  const isToggledOn = useLayerToggle(layerId);
  const mapDay = useLayerDay(layerId);
  const capabilities = useTimeSliderStore((state) => state.capabilities);

  return useMemo(() => {
    const entry = LAYER_REGISTRY[layerId];
    const capability =
      entry.warehouseLayerName === null
        ? null
        : findLayerCapability(capabilities, entry.warehouseLayerName);
    // An unknown availability must read as published. Before the server publishes
    // capabilities there is nothing to withhold on, and treating silence as "no data"
    // would caption every layer with a claim about history nobody has measured.
    const availability: MetricAtDateAvailability =
      capabilities === null || capability === null || mapDay.selectedDate === null
        ? "published"
        : layerAvailabilityAt(
            capability,
            mapDay.selectedDate,
            mapDay.forecastVariant,
            capabilities
          );

    return {
      toggleId: layerId,
      warehouseLayerName: entry.warehouseLayerName,
      isToggledOn,
      shouldRender: isToggledOn && entry.permanentlyUnavailableReason === null,
      selectedDate: mapDay.selectedDate,
      latestObservedDate: mapDay.latestObservedDate,
      isBehindLatestObservedDate: mapDay.isBehindLatestObservedDate,
      variant: mapDay.variant,
      availability,
      unavailableReason: describeAvailability(
        availability,
        entry.warehouseLayerName ?? layerId
      ),
      permanentlyUnavailableReason: entry.permanentlyUnavailableReason,
    };
  }, [layerId, isToggledOn, mapDay, capabilities]);
}

/** Month labels for the composite period the vegetation raster is addressed by. */
const COMPOSITE_MONTH_ABBREVIATIONS = [
  "Jan", "Feb", "Mar", "Apr", "May", "Jun",
  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
] as const;

/**
 * Why NASA GIBS publishes no NDVI composite for a period, or null when it publishes one.
 *
 * `resolveGibsNdviDate` refuses a month outside the product's published extent instead of
 * emitting a tile URL that would 404 (see src/lib/vegetation.ts), so without this the layer
 * would just go quietly blank as someone scrubbed off the archive. "Today" comes from the
 * capabilities payload, never the browser clock: across New Year the two disagree by a whole
 * year, which is the same trap `serverCurrentDate` exists to close everywhere else.
 */
function describeCompositeGap(
  period: string,
  year: number,
  month: number,
  serverCurrentDate: string | null
): string | null {
  const asOf =
    serverCurrentDate === null ? new Date() : new Date(`${serverCurrentDate}T00:00:00Z`);
  if (resolveGibsNdviDate(year, month, asOf) !== null) return null;
  return (
    `No NDVI composite exists for ${period}: it falls outside the NASA GIBS ` +
    `MODIS/Terra 8-Day NDVI product's published extent. Scrub to a covered date to see it.`
  );
}

/**
 * The vegetation layer's selected mode, as the renderer consumes it.
 *
 * `year`, `month` and `compositePeriod` are a PROJECTION of the time slider's day, never
 * state of their own: the GIBS raster is addressed by a month, so the selected day is
 * narrowed to the month containing it. The vegetation store held its own `year`/`month`
 * behind two panel sliders until 2026-08-05, which gave the app two disagreeing notions of
 * "when" -- do not reintroduce them. See src/components/map/AGENTS.md "One time control,
 * projected per layer".
 */
export interface VegetationDisplayMode {
  mode: VegetationMode;
  /** Calendar year of the slider's settled day; null until capabilities supply a day. */
  year: number | null;
  /** Month (1-12) of the slider's settled day; null until capabilities supply a day. */
  month: number | null;
  /** "Aug 2026" -- the composite period actually drawn; null with no day. */
  compositePeriod: string | null;
  /** Why GIBS publishes nothing for that period, or null when it does. */
  compositeUnavailableReason: string | null;
  ndviMode: "absolute" | "anomaly";
  showNDWI: boolean;
}

/**
 * Read-only view of the vegetation store plus the slider day projected onto a composite
 * month; the panel keeps the store for its display setters.
 *
 * Reads the SETTLED day, not the raw one: this feeds `LayerManager`, which sits above ~8
 * layer children, and the returned object is memoized on the (year, month) pair rather than
 * on the day -- so scrubbing 30 days inside one month leaves the raster's tile URL, and every
 * prop `VegetationLayer` keys its `setTiles` effect on, referentially unchanged. A month-
 * granular product must not re-request per day.
 *
 * The day is `vegetation`'s OWN, not the map's: with per-layer dates there is no map-wide day
 * left to project, and reading another row's would caption this raster with a month nobody
 * selected for it.
 */
export function useVegetationDisplayMode(): VegetationDisplayMode {
  const mode = useVegetationStore((state) => state.mode);
  const ndviMode = useVegetationStore((state) => state.ndviMode);
  const showNDWI = useVegetationStore((state) => state.showNDWI);
  const { settledDate, serverCurrentDate } = useDebouncedLayerDay("vegetation");

  // Plain slices of a YYYY-MM-DD string the store already validated with `isCalendarDate`;
  // `new Date(day)` would reintroduce a timezone shift on the very value that exists to
  // avoid one.
  const year = settledDate === null ? null : Number(settledDate.slice(0, 4));
  const month = settledDate === null ? null : Number(settledDate.slice(5, 7));

  return useMemo(() => {
    const compositePeriod =
      year === null || month === null
        ? null
        : `${COMPOSITE_MONTH_ABBREVIATIONS[month - 1]} ${year}`;
    return {
      mode,
      year,
      month,
      compositePeriod,
      compositeUnavailableReason:
        compositePeriod === null || year === null || month === null
          ? null
          : describeCompositeGap(compositePeriod, year, month, serverCurrentDate),
      ndviMode,
      showNDWI,
    };
  }, [mode, year, month, serverCurrentDate, ndviMode, showNDWI]);
}

/**
 * The soil layer's selected mode, as the renderer consumes it.
 *
 * No `opacity`: one scalar here drove the SoilGrids raster AND both ERA5-Land fields
 * (LayerManager passed `soilMode.opacity` to all three), which made per-layer opacity
 * structurally impossible for them. Each of `soil`, `soil-moisture` and `soil-temperature`
 * now carries its own multiplier in `layer-store.layerOpacity`.
 */
export interface SoilDisplayMode {
  property: SoilProperty;
  /**
   * The ECMWF layer each soil field draws, per measure. A depth, never a date -- both fields
   * take their day from `useDebouncedLayerDay` like every other warehouse-backed feed.
   */
  fieldDepth: Record<SoilFieldMeasure, SoilFieldDepth>;
}

/** Read-only view of the soil store; the panel keeps the store for its setters. */
export function useSoilDisplayMode(): SoilDisplayMode {
  const property = useSoilStore((state) => state.property);
  const fieldDepth = useSoilStore((state) => state.fieldDepth);

  return useMemo(() => ({ property, fieldDepth }), [property, fieldDepth]);
}

/**
 * The climate rows' selected modes, as the renderers consume them.
 *
 * Forms and a statistic, never a date: each NASA POWER row takes its own day from
 * `useDebouncedLayerDay` like every other warehouse-backed feed.
 *
 * No `signal`. Which signals are drawn is `layerVisibility` now that each has a row, and a
 * `signal` here would be a second, disagreeing answer to the same question.
 */
export interface ClimateDisplayMode {
  /** Sparse; read through `renderFormFor`, which applies each signal's own default. */
  renderForms: Partial<Record<ClimateFieldSignalId, ClimateRenderForm>>;
  /** The form a given signal is drawn in, with the stale-value guard already applied. */
  renderFormFor: (signal: ClimateFieldSignalId) => ClimateRenderForm;
  airTemperatureVariant: AirTemperatureVariant;
}

/**
 * Read-only view of the climate store; the panel keeps the store for its setters.
 *
 * Memoized because `LayerManager` sits above ~9 layer children and would otherwise hand every
 * mounted `ClimateFieldLayer` a fresh object on each unrelated re-render.
 */
export function useClimateDisplayMode(): ClimateDisplayMode {
  const renderForms = useClimateStore((state) => state.renderForms);
  const airTemperatureVariant = useClimateStore((state) => state.airTemperatureVariant);

  return useMemo(
    () => ({
      renderForms,
      renderFormFor: (signal: ClimateFieldSignalId) =>
        climateRenderForm({ renderForms }, signal),
      airTemperatureVariant,
    }),
    [renderForms, airTemperatureVariant]
  );
}
