"use client";

import { useEffect, type ReactNode } from "react";
import { trpc } from "@/lib/trpc/client";
import { useTimeSliderStore } from "@/stores/time-slider-store";
import TimeSlider from "./TimeSlider";

/**
 * The connected time slider and the right-hand panel region that anchors it: the one place
 * `environmental.getSliderCapabilities` is read and fed into the store, and the one place the
 * slider's position is decided.
 *
 * The region is ALWAYS mounted, with no dependence on an open panel: the selected day applies
 * to every layer on the map, so the marker for it cannot be something a user has to open a
 * panel to reach. Only the body below it is conditional. The region draws no chrome of its
 * own, so while capabilities are in flight it is an empty, invisible box rather than a
 * placeholder frame.
 *
 * Nothing else may set capabilities. Before this existed the slider had no mount point at
 * all, so `setCapabilities` was called only from tests, the store's `selectedDate` never left
 * `UNINITIALIZED_DATE`, and both server procedures were unreachable code. TimeSlider itself
 * stays presentational so it can be rendered against a fixture without a tRPC provider.
 *
 * The server's payload is the ONLY source of the axis and of "today"; there is no fallback
 * domain, because a browser-clock guess would put "today" on the wrong day for anyone outside
 * UTC and invent an axis nobody measured. Until the payload lands, TimeSlider renders nothing
 * -- unless the fetch failed outright (see capabilitiesUnavailable below), because a failed
 * request and a slow-but-healthy one must not look identical: the former was mistaken for a
 * UI bug once already (the read-model's `invalid input syntax for type bigint: "0.01"` 500).
 */
/** Matches CAPABILITIES_CACHE_TTL_MS in environmental-read-model.ts, so a poll is a cache hit. */
const CAPABILITIES_REFRESH_MS = 5 * 60_000;

/**
 * The right-hand panel region's anchor. This is the whole of the slider's positioning, moved
 * out of `TIME_SLIDER_CONTAINER_CLASSES` when the slider stopped being a card floating over
 * the middle of the canvas (2026-08-05) and became the region's global time marker.
 *
 * Column geometry deliberately echoes the panel sheets' (`max-w-sm`, right-aligned) so the
 * slider reads as the top of the right-hand panel region rather than as one more floating
 * widget. The two offsets are both collision avoidance, not taste: `right-16` clears
 * MapLibre's own top-right control stack (29px wide, 44px under the (max-width: 640px) rule in
 * globals.css), and `top-16` clears the row above -- MapControls' centred `top-4` toolbar,
 * whose centred half-width reaches into this column on a ~1024px viewport, and SearchBar's
 * button on a phone. Width is capped against the viewport, never fixed, so the column can
 * never exceed the screen.
 *
 * `overflow-y-auto` on the region with a `sticky` time section means one scroller, not two:
 * a short viewport scrolls the region's body under a pinned slider instead of nesting a
 * scroller inside a scroller, which on a phone is how a control becomes unreachable.
 */
const PANEL_REGION_CLASSES =
  "absolute right-16 top-16 z-10 flex max-h-[calc(100%-5rem)] w-[min(24rem,calc(100vw-5rem))] flex-col overflow-y-auto overscroll-contain";

export interface TimeSliderPanelProps {
  /**
   * The region's body, scrolling below the pinned time section. Optional and conditional --
   * the region and its time marker are always mounted, because the day applies to every layer
   * whether or not a panel is open, but the body only exists when there is something to dock
   * there. Panel sheets currently render themselves as overlays and do not use this.
   */
  children?: ReactNode;
}

export default function TimeSliderPanel({ children }: TimeSliderPanelProps) {
  const setCapabilities = useTimeSliderStore((state) => state.setCapabilities);
  const setCapabilitiesUnavailable = useTimeSliderStore(
    (state) => state.setCapabilitiesUnavailable
  );
  // The layer list moves only when an ingest run lands a new day, so refetching on every
  // focus would spend the whole-warehouse scan for an answer that has not changed.
  //
  // But `serverCurrentDate` rides in the same payload and it DOES change, once a day, at an
  // instant no user action coincides with. This panel is mounted inside MapView and never
  // unmounts, so without a timer a tab open across UTC midnight keeps reporting yesterday as
  // today: `sliderDomain` pins `lastDay` there, `clampDateToDomain` refuses to scrub past it,
  // the axis is labelled "Today <yesterday>", and anything ingested for the new day is
  // unreachable until reload. The interval bounds that staleness to CAPABILITIES_REFRESH_MS
  // and costs nothing upstream: `readLayerCapabilities` memoizes for the same 5 minutes behind
  // a single-flight guard, so the poll is a server-side cache hit that re-stamps the date.
  const capabilitiesQuery = trpc.environmental.getSliderCapabilities.useQuery(undefined, {
    staleTime: CAPABILITIES_REFRESH_MS,
    refetchInterval: CAPABILITIES_REFRESH_MS,
    refetchOnWindowFocus: false,
  });

  const capabilities = capabilitiesQuery.data;
  const isError = capabilitiesQuery.isError;

  useEffect(() => {
    if (capabilities === undefined) return;
    setCapabilities(capabilities);
  }, [capabilities, setCapabilities]);

  // Only "never got a payload at all" is worth interrupting the panel for. `data` stays the
  // last successful payload across a background refetch failure (react-query does not clear
  // it), so once one fetch has succeeded a later transient failure leaves this false and
  // TimeSlider keeps drawing from the stale-but-real capabilities already in the store.
  useEffect(() => {
    setCapabilitiesUnavailable(capabilities === undefined && isError);
  }, [capabilities, isError, setCapabilitiesUnavailable]);

  return (
    <section
      className={PANEL_REGION_CLASSES}
      aria-label="Map time and panel region"
      data-testid="map-panel-region"
    >
      {/* Pinned: the day is the one marker every layer draws as of, so it stays put while the
          body below scrolls. `sticky` rather than a fixed-height split so the section is
          exactly as tall as the slider, whose height changes with its two disclosures. */}
      <div className="sticky top-0 shrink-0" data-testid="map-panel-region-time">
        <TimeSlider />
      </div>
      {children !== undefined && (
        <div className="mt-2 shrink-0" data-testid="map-panel-region-body">
          {children}
        </div>
      )}
    </section>
  );
}
