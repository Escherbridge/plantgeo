"use client";

import { useEffect, useState } from "react";
import type { Map as MapLibreMap } from "maplibre-gl";

/**
 * A retriggerable "the current style is actually ready" signal, per
 * `map.isStyleLoaded()` -- not merely "style.load fired once". See
 * `src/components/map/AGENTS.md` ("Style.load listener order" /
 * "Custom-added layers: style.load is not enough on its own") for why a
 * plain `once("style.load", …)` misses layers entirely: `setStyle()`'s diff
 * path fires `style.load` *synchronously*, and `isStyleLoaded()` also
 * requires every source's tiles to be in, so it can still read `false` the
 * instant `style.load` fires. A `once` handler registered against that
 * instant never runs again, and the layers are never added.
 *
 * This hook subscribes with `on` (not `once`) to both `style.load` and
 * `styledata` and recomputes from the live map on each, so the returned
 * value keeps flipping false -> true across every future style load or
 * basemap swap, not just the first one. Listeners are registered with a
 * `[map]`-only dependency so they keep their place in the listener queue
 * (see the AGENTS.md section on registration order) and are torn down on
 * unmount / map change.
 *
 * Consumers should NOT trust the boolean value returned mid-render as the
 * gate for their add-layers call -- put it in a `useEffect` dependency array
 * to force a re-run, then re-read `map.isStyleLoaded()` live inside that
 * effect. That sidesteps any staleness between this hook's internal state
 * update and the consumer's render, and matches the pattern already used in
 * `LayerManager.tsx`'s `styleReady` state.
 */
export function useStyleReady(map: MapLibreMap | null): boolean {
  const [styleReady, setStyleReady] = useState(false);

  useEffect(() => {
    if (!map) {
      setStyleReady(false);
      return;
    }

    // `isStyleLoaded()` is typed `boolean | void`; coerce so this stays a boolean state.
    const recompute = () => setStyleReady(!!map.isStyleLoaded());
    // Recompute immediately too: the effect can run after the style already
    // finished loading (e.g. this hook mounting on an existing map, or `map`
    // itself changing to an already-loaded instance), in which case no
    // future "style.load"/"styledata" event will fire to tell us.
    recompute();

    map.on("style.load", recompute);
    map.on("styledata", recompute);
    return () => {
      map.off("style.load", recompute);
      map.off("styledata", recompute);
    };
  }, [map]);

  return styleReady;
}
