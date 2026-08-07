"use client";

import { useEffect, useId, useState, type ReactNode } from "react";
import { CalendarDays, ChevronDown } from "lucide-react";
import { cn } from "@/lib/utils";
import { trpc } from "@/lib/trpc/client";
import {
  isCalendarDate,
  isFutureDate,
  useTimeSliderStore,
} from "@/stores/time-slider-store";
import TimeSlider from "./TimeSlider";

/**
 * The connected time slider and the right-hand panel region that anchors it: the one place
 * `environmental.getSliderCapabilities` is read and fed into the store, and the one place the
 * slider's position is decided.
 *
 * The region is ALWAYS mounted, with no dependence on an open panel: the selected day applies
 * to every layer on the map, so the marker for it cannot be something a user has to open a
 * panel to reach. Since 2026-08-06 the always-visible marker is a compact date pill in the
 * top bar, not the whole scrubber card: the card is detail a user opens, the pill is the one
 * claim -- "the map draws this day" -- that must never leave the screen. Only the body below
 * it is conditional. The region draws no chrome of its own, so while capabilities are in
 * flight it is an empty, invisible box rather than a placeholder frame.
 *
 * Nothing else may set capabilities. Before this existed the slider had no mount point at
 * all, so `setCapabilities` was called only from tests, the store's `selectedDate` never left
 * `UNINITIALIZED_DATE`, and both server procedures were unreachable code. TimeSlider itself
 * stays presentational so it can be rendered against a fixture without a tRPC provider.
 *
 * The server's payload is the ONLY source of the axis and of "today"; there is no fallback
 * domain, because a browser-clock guess would put "today" on the wrong day for anyone outside
 * UTC and invent an axis nobody measured. Until the payload lands, the time section renders
 * nothing -- unless the fetch failed outright (see capabilitiesUnavailable below), because a
 * failed request and a slow-but-healthy one must not look identical: the former was mistaken
 * for a UI bug once already (the read-model's `invalid input syntax for type bigint: "0.01"`
 * 500).
 */
/** Matches CAPABILITIES_CACHE_TTL_MS in environmental-read-model.ts, so a poll is a cache hit. */
const CAPABILITIES_REFRESH_MS = 5 * 60_000;

/**
 * The right-hand panel region's anchor, forming the right end of the top bar: SearchBar holds
 * `top-4 left-4`, this region holds `top-4` on the right, and MapLibre's own control stack
 * keeps the far right edge. `right-16` clears that stack (29px wide, 44px under the
 * (max-width: 640px) rule in globals.css). Width is capped against the viewport, never fixed,
 * so the column can never exceed the screen.
 *
 * `pointer-events-none` on the region with `pointer-events-auto` on its children: collapsed,
 * the region is a mostly-empty 24rem column floating over the canvas, and without the pass-
 * through it would silently swallow every map drag that starts under it. Each interactive
 * child opts back in.
 *
 * `overflow-y-auto` on the region with a `sticky` time section means one scroller, not two:
 * a short viewport scrolls the region's body under a pinned time bar instead of nesting a
 * scroller inside a scroller, which on a phone is how a control becomes unreachable.
 */
const PANEL_REGION_CLASSES =
  "pointer-events-none absolute right-16 top-4 z-10 flex max-h-[calc(100%-5rem)] w-[min(24rem,calc(100vw-5rem))] flex-col overflow-y-auto overscroll-contain";

/**
 * The collapsed pill and the bar's Today button share the toolbar chrome so the whole top row
 * reads as one system; tokens match FloatingToolbar's. `max-sm:min-h-11` is the same 44px
 * mobile tap-target rule every other control follows.
 */
const TOP_BAR_CONTROL_CLASSES =
  "pointer-events-auto flex items-center gap-1.5 rounded-xl border border-[var(--glass-border)] bg-[var(--glass-bg)] px-2.5 py-1.5 text-xs font-medium text-[hsl(var(--foreground))] shadow-[var(--shadow-lg)] [backdrop-filter:blur(var(--glass-blur))] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[hsl(var(--ring))] max-sm:min-h-11";

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
  const selectedDate = useTimeSliderStore((state) => state.selectedDate);
  const storeCapabilities = useTimeSliderStore((state) => state.capabilities);
  const storeCapabilitiesUnavailable = useTimeSliderStore(
    (state) => state.capabilitiesUnavailable
  );
  const resetToToday = useTimeSliderStore((state) => state.resetToToday);
  // Collapsed by default: the pill IS the marker, and the full scrubber is an opened detail.
  // Session-local on purpose -- reopening the app should always start at the subtle state.
  const [isExpanded, setIsExpanded] = useState(false);
  const timeControlsId = useId();
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

  // What the pill can honestly say. Before the payload lands there is no date and no "today",
  // so the bar renders nothing rather than a placeholder claiming one -- the same rule
  // TimeSlider itself follows. The one exception is an outright failed fetch, which must not
  // look like "no bar": the pill then names the failure and still opens the full notice.
  const hasDay = storeCapabilities !== null && isCalendarDate(selectedDate);
  const fetchFailed = storeCapabilities === null && storeCapabilitiesUnavailable;
  const serverToday = storeCapabilities?.serverCurrentDate ?? null;
  const isOffToday = hasDay && serverToday !== null && selectedDate !== serverToday;
  const isFuture = hasDay && isFutureDate(selectedDate, storeCapabilities);

  return (
    <section
      className={PANEL_REGION_CLASSES}
      aria-label="Map time and panel region"
      data-testid="map-panel-region"
    >
      {/* Pinned: the day is the one marker every layer draws as of, so the bar stays put
          while the body below scrolls. `sticky` rather than a fixed-height split so the
          section is exactly as tall as its content, which changes with the disclosure. */}
      <div
        className="sticky top-0 flex shrink-0 flex-col"
        data-testid="map-panel-region-time"
      >
        {(hasDay || fetchFailed) && (
          <div className="flex items-center justify-end gap-2">
            {/* One-tap return to the live day without opening the card: an off-today date
                silently filters EVERY layer, so the way back must cost no disclosure. Hidden
                while expanded -- the card has its own Today button right next to the field. */}
            {isOffToday && !isExpanded && (
              <button
                type="button"
                onClick={() => resetToToday()}
                title={serverToday === null ? undefined : `Jump to ${serverToday}`}
                className={cn(TOP_BAR_CONTROL_CLASSES, "text-[hsl(var(--muted-foreground))]")}
                data-testid="time-slider-bar-today"
              >
                Today
              </button>
            )}
            <button
              type="button"
              aria-expanded={isExpanded}
              aria-controls={timeControlsId}
              onClick={() => setIsExpanded((expanded) => !expanded)}
              title={isExpanded ? "Hide time controls" : "Change the map date"}
              className={TOP_BAR_CONTROL_CLASSES}
              data-testid="time-slider-toggle"
            >
              <CalendarDays
                aria-hidden="true"
                className="size-3.5 shrink-0 text-[hsl(var(--muted-foreground))]"
              />
              {fetchFailed ? (
                <span className="text-[hsl(var(--destructive))]">Time range unavailable</span>
              ) : (
                <>
                  <span data-testid="time-slider-toggle-date">{selectedDate}</span>
                  {/* Silent only at the live default. Any other day is a claim about the whole
                      map, so the pill says which kind: a real observed day in the past, or a
                      day past the end of the record. */}
                  {isFuture ? (
                    <span className="rounded-(--radius) bg-[hsl(var(--muted-foreground))]/20 px-1.5 py-0.5 text-[0.6875rem] font-semibold uppercase tracking-wide text-[hsl(var(--muted-foreground))]">
                      Beyond record
                    </span>
                  ) : (
                    isOffToday && (
                      <span className="rounded-(--radius) bg-[hsl(var(--primary))]/15 px-1.5 py-0.5 text-[0.6875rem] font-semibold uppercase tracking-wide text-[hsl(var(--primary))]">
                        Past day
                      </span>
                    )
                  )}
                </>
              )}
              <ChevronDown
                aria-hidden="true"
                className={cn(
                  "size-3.5 shrink-0 text-[hsl(var(--muted-foreground))] transition-transform",
                  isExpanded && "rotate-180"
                )}
              />
            </button>
          </div>
        )}
        {/* Same className-swap idiom as TimeSlider's own disclosures: the native `hidden`
            attribute would lose to an author-origin display utility at equal specificity,
            so only one display rule is ever present. The card stays mounted either way --
            its store subscriptions are cheap, and unmounting would drop the scroll and
            disclosure state a user set inside it. */}
        <div
          id={timeControlsId}
          className={isExpanded ? "pointer-events-auto mt-2" : "hidden"}
          data-testid="map-panel-region-time-controls"
        >
          <TimeSlider />
        </div>
      </div>
      {children !== undefined && (
        <div className="pointer-events-auto mt-2 shrink-0" data-testid="map-panel-region-body">
          {children}
        </div>
      )}
    </section>
  );
}
