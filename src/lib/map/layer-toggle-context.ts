"use client";

/**
 * The layer toggle context: the one place the map and the slider agree on what is switched
 * on, what mode it draws in, and which day it draws. It composes the existing Zustand
 * stores and owns no state of its own -- see src/components/map/AGENTS.md
 * "The layer toggle is the only source of layer visibility".
 */

import { useEffect, useMemo, useState } from "react";
import {
  LAYER_REGISTRY,
  LAYER_TOGGLE_IDS,
  type LayerToggleId,
} from "@/lib/map/layer-registry";
import { useMapStore } from "@/stores/map-store";
import { useSoilStore } from "@/stores/soil-store";
import { SCRUB_SETTLE_MS } from "@/stores/useMetricAtDate";
import {
  describeAvailability,
  findLayerCapability,
  isCalendarDate,
  layerAvailabilityAt,
  resolveVariant,
  useTimeSliderStore,
} from "@/stores/time-slider-store";
import { useVegetationStore } from "@/stores/vegetation-store";
import { resolveGibsNdviDate } from "@/lib/vegetation";
import type { SoilFieldDepth, SoilFieldMeasure } from "@/lib/environmental/soil-field";
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

/** The day the map draws as of, and which series that day reads from. */
export interface MapDay {
  /** YYYY-MM-DD, or null until the capabilities payload supplies one. */
  selectedDate: string | null;
  /** Server UTC today; the only definition of "today". Null before capabilities arrive. */
  serverCurrentDate: string | null;
  /** True when the selection is some day other than the server's today. */
  isOffServerToday: boolean;
  /** The forecast series a future day would read from. */
  forecastVariant: ForecastVariant;
  /** What the selected day actually reads from: observations up to today, forecast after. */
  variant: MetricVariant;
}

/** The slider's day, shared by the map and every panel so they can never disagree. */
export function useMapDay(): MapDay {
  const selectedDate = useTimeSliderStore((state) => state.selectedDate);
  const forecastVariant = useTimeSliderStore((state) => state.forecastVariant);
  const capabilities = useTimeSliderStore((state) => state.capabilities);

  return useMemo(() => {
    const day = isCalendarDate(selectedDate) ? selectedDate : null;
    const serverCurrentDate = capabilities?.serverCurrentDate ?? null;
    return {
      selectedDate: day,
      serverCurrentDate,
      isOffServerToday:
        day !== null && serverCurrentDate !== null && day !== serverCurrentDate,
      forecastVariant,
      variant:
        day !== null && capabilities !== null
          ? resolveVariant(day, capabilities, forecastVariant)
          : "observed",
    };
  }, [selectedDate, forecastVariant, capabilities]);
}

/**
 * The slider's day, re-rendered only once a scrub has settled.
 *
 * Deliberately NOT `useDebounce(useMapDay().selectedDate, …)`. That subscribes to the raw day,
 * so every consumer re-renders on every pointer tick of a day-granular scrub even though only
 * the settled value is ever read -- and `LayerManager` has ~8 layer children behind it. Reading
 * the store imperatively and setting state only after the settle window gives one render per
 * settle instead of one per tick, on the SAME boundary `useMetricAtDate` debounces to.
 *
 * The store fires for every field, not just the day; the timer re-reads the current day when
 * it finally elapses, and setting the same string is a React no-op.
 */
function useSettledSelectedDate(): string {
  const [settledDate, setSettledDate] = useState(
    () => useTimeSliderStore.getState().selectedDate
  );
  useEffect(() => {
    let settleTimer: ReturnType<typeof setTimeout> | null = null;
    const unsubscribe = useTimeSliderStore.subscribe(() => {
      if (settleTimer !== null) clearTimeout(settleTimer);
      settleTimer = setTimeout(
        () => setSettledDate(useTimeSliderStore.getState().selectedDate),
        SCRUB_SETTLE_MS
      );
    });
    return () => {
      if (settleTimer !== null) clearTimeout(settleTimer);
      unsubscribe();
    };
  }, []);
  return settledDate;
}

/** The slider's day as a request carries it: settled, and absent at the server's today. */
export interface DebouncedMapDay {
  /** YYYY-MM-DD, or null until the capabilities payload supplies one. */
  settledDate: string | null;
  /** Server UTC today; the only definition of "today". Null before capabilities arrive. */
  serverCurrentDate: string | null;
  /** True when the settled selection is some day other than the server's today. */
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
   */
  requestDate: string | undefined;
}

/**
 * The day every warehouse-backed viewport query keys on. One hook, so the map and the panels
 * settle on the same boundary and land on the same cache entry -- see
 * src/lib/server/AGENTS.md §slider-day.
 */
export function useDebouncedMapDay(): DebouncedMapDay {
  const settledSelection = useSettledSelectedDate();
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

/** Everything one layer needs to decide whether and how to draw. */
export interface LayerRenderState {
  toggleId: LayerToggleId;
  /** The `geo.layers.name` behind this toggle, or null when no warehouse layer backs it. */
  warehouseLayerName: string | null;
  /** The user's switch position. */
  isToggledOn: boolean;
  /** True when the layer should draw: switched on and not withheld. */
  shouldRender: boolean;
  /** The day this layer draws as of, or null until capabilities arrive. */
  selectedDate: string | null;
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
  const mapDay = useMapDay();
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
  opacity: number;
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
 */
export function useVegetationDisplayMode(): VegetationDisplayMode {
  const mode = useVegetationStore((state) => state.mode);
  const ndviMode = useVegetationStore((state) => state.ndviMode);
  const showNDWI = useVegetationStore((state) => state.showNDWI);
  const opacity = useVegetationStore((state) => state.opacity);
  const { settledDate, serverCurrentDate } = useDebouncedMapDay();

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
      opacity,
    };
  }, [mode, year, month, serverCurrentDate, ndviMode, showNDWI, opacity]);
}

/** The soil layer's selected mode, as the renderer consumes it. */
export interface SoilDisplayMode {
  property: SoilProperty;
  opacity: number;
  /**
   * The ECMWF layer each soil field draws, per measure. A depth, never a date -- both fields
   * take their day from `useDebouncedMapDay` like every other warehouse-backed feed.
   */
  fieldDepth: Record<SoilFieldMeasure, SoilFieldDepth>;
}

/** Read-only view of the soil store; the panel keeps the store for its setters. */
export function useSoilDisplayMode(): SoilDisplayMode {
  const property = useSoilStore((state) => state.property);
  const opacity = useSoilStore((state) => state.opacity);
  const fieldDepth = useSoilStore((state) => state.fieldDepth);

  return useMemo(() => ({ property, opacity, fieldDepth }), [property, opacity, fieldDepth]);
}
