"use client";

/**
 * Map clicks as a query point, for the panels that ask the warehouse about ONE place.
 * See `src/components/map/AGENTS.md` "Picking a point to query" for why capture is armed
 * by the panel rather than always on.
 */

import { useEffect } from "react";
import type { Map as MapLibreMap, MapMouseEvent } from "maplibre-gl";
import { useMapStore, type MapQueryPoint } from "@/stores/map-store";

/** Degrees within which a second click counts as "the same place", and so clears the pin. */
const SAME_POINT_TOLERANCE_DEGREES = 0.0005;

export interface MapQueryPointCapture {
  /** The picked place, or null when nothing is picked. */
  queryPoint: MapQueryPoint | null;
  /** Drops the pin without waiting for another click; the panel's explicit escape hatch. */
  clearQueryPoint: () => void;
}

/** True when two picks name the same place to within the click tolerance. */
function isSamePoint(a: MapQueryPoint, b: MapQueryPoint): boolean {
  return (
    Math.abs(a.lat - b.lat) < SAME_POINT_TOLERANCE_DEGREES &&
    Math.abs(a.lon - b.lon) < SAME_POINT_TOLERANCE_DEGREES
  );
}

/**
 * Captures map clicks as a query point while `active`.
 *
 * Cancellable three ways, because a pin the user cannot get rid of is worse than no pin:
 * clicking the pin again, pressing Escape, and `active` going false (the panel closing).
 * Clicking anywhere else MOVES the pin rather than clearing it -- that is what a second
 * question about a second place looks like.
 *
 * Arming also sets `isCapturingQueryPoint`, which `MapView`'s own click handler reads so a
 * single click never both drops a pin and opens the agent popup.
 */
export function useMapQueryPoint(
  map: MapLibreMap | null,
  active: boolean
): MapQueryPointCapture {
  const queryPoint = useMapStore((state) => state.queryPoint);
  const setQueryPoint = useMapStore((state) => state.setQueryPoint);
  const setCapturingQueryPoint = useMapStore((state) => state.setCapturingQueryPoint);

  useEffect(() => {
    if (!map || !active) return;
    setCapturingQueryPoint(true);

    const onClick = (event: MapMouseEvent) => {
      const picked: MapQueryPoint = { lat: event.lngLat.lat, lon: event.lngLat.lng };
      // Read the store rather than the closed-over value: this listener is registered once
      // per (map, active) pair and must not re-register on every pick.
      const current = useMapStore.getState().queryPoint;
      setQueryPoint(current !== null && isSamePoint(current, picked) ? null : picked);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setQueryPoint(null);
    };

    map.on("click", onClick);
    window.addEventListener("keydown", onKeyDown);
    return () => {
      map.off("click", onClick);
      window.removeEventListener("keydown", onKeyDown);
      setCapturingQueryPoint(false);
    };
  }, [map, active, setQueryPoint, setCapturingQueryPoint]);

  return { queryPoint, clearQueryPoint: () => setQueryPoint(null) };
}
