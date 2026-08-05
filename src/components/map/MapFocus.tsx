"use client";

import { useSearchParams } from "next/navigation";
import { useEffect, useRef } from "react";
import { readMapFocus } from "@/lib/map/focus-params";
import { useMap } from "@/lib/map/map-context";

/**
 * Drives the camera from the URL so a place can be linked to from off the map
 * (the `/feed` "view on the map" links). Renders nothing.
 *
 * MapView reads the store viewport once, in an init effect with an empty
 * dependency list, so seeding the store is not enough on a client-side
 * navigation into an already-mounted map. This moves the camera directly and
 * lets MapView's own `moveend` handler write the result back to the store,
 * which keeps a single writer for viewport state.
 */
export function MapFocus() {
  const map = useMap();
  const searchParams = useSearchParams();
  const appliedTargetRef = useRef<string | null>(null);

  useEffect(() => {
    if (!map) return;

    const focus = readMapFocus(searchParams);
    if (!focus) {
      // Clearing lets the same target be re-applied after the user pans away
      // and follows the identical link again.
      appliedTargetRef.current = null;
      return;
    }

    const target = `${focus.longitude},${focus.latitude},${focus.zoom}`;
    if (appliedTargetRef.current === target) return;
    appliedTargetRef.current = target;

    const prefersReducedMotion = window.matchMedia(
      "(prefers-reduced-motion: reduce)"
    ).matches;

    const camera = {
      center: [focus.longitude, focus.latitude] as [number, number],
      zoom: focus.zoom,
    };

    if (prefersReducedMotion) {
      map.jumpTo(camera);
      return;
    }
    map.flyTo({ ...camera, duration: 1200, essential: true });
  }, [map, searchParams]);

  return null;
}
