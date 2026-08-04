"use client";

/**
 * The layer toggle context: the one place the map and the slider agree on what is switched
 * on, what mode it draws in, and which day it draws. It composes the existing Zustand
 * stores and owns no state of its own -- see src/components/map/AGENTS.md
 * "The layer toggle is the only source of layer visibility".
 */

import { useEffect, useMemo, useRef } from "react";
import {
  LAYER_REGISTRY,
  LAYER_TOGGLE_IDS,
  type LayerToggleId,
} from "@/lib/map/layer-registry";
import { useMapStore } from "@/stores/map-store";
import { useSoilStore } from "@/stores/soil-store";
import {
  describeAvailability,
  findLayerCapability,
  isCalendarDate,
  layerAvailabilityAt,
  resolveVariant,
  useTimeSliderStore,
} from "@/stores/time-slider-store";
import { useVegetationStore } from "@/stores/vegetation-store";
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
 * The selected day without subscribing to it. Style.load handlers and other non-render
 * readers use this so a day-granular scrub never re-renders the layer tree, and so the
 * handler's registration effect never lists the day in its deps -- see
 * src/components/map/AGENTS.md "Style.load listener order".
 */
export function useSelectedMapDateRef() {
  const selectedDateRef = useRef(useTimeSliderStore.getState().selectedDate);
  useEffect(
    () =>
      useTimeSliderStore.subscribe((state) => {
        selectedDateRef.current = state.selectedDate;
      }),
    []
  );
  return selectedDateRef;
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

/** The vegetation layer's selected mode, as the renderer consumes it. */
export interface VegetationDisplayMode {
  mode: VegetationMode;
  year: number;
  month: number;
  ndviMode: "absolute" | "anomaly";
  showNDWI: boolean;
  opacity: number;
}

/** Read-only view of the vegetation store; the panel keeps the store for its setters. */
export function useVegetationDisplayMode(): VegetationDisplayMode {
  const mode = useVegetationStore((state) => state.mode);
  const year = useVegetationStore((state) => state.year);
  const month = useVegetationStore((state) => state.month);
  const ndviMode = useVegetationStore((state) => state.ndviMode);
  const showNDWI = useVegetationStore((state) => state.showNDWI);
  const opacity = useVegetationStore((state) => state.opacity);

  return useMemo(
    () => ({ mode, year, month, ndviMode, showNDWI, opacity }),
    [mode, year, month, ndviMode, showNDWI, opacity]
  );
}

/** The soil layer's selected mode, as the renderer consumes it. */
export interface SoilDisplayMode {
  property: SoilProperty;
  opacity: number;
}

/** Read-only view of the soil store; the panel keeps the store for its setters. */
export function useSoilDisplayMode(): SoilDisplayMode {
  const property = useSoilStore((state) => state.property);
  const opacity = useSoilStore((state) => state.opacity);

  return useMemo(() => ({ property, opacity }), [property, opacity]);
}
