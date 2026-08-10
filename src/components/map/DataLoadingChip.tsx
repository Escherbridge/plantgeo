"use client";

import { useIsFetching } from "@tanstack/react-query";

/**
 * That the map is waiting on data, said out loud.
 *
 * Every viewport-scoped feed arrives after the basemap does, and until it lands the ground is
 * simply empty. Empty is also what a layer with no coverage looks like, what a layer that is
 * switched off looks like, and what an outage looks like -- four different situations sharing
 * one appearance, with first paint being the one that happens to everybody. This distinguishes
 * the transient case from the three that are not, and disappears the moment it stops being
 * true.
 *
 * Counts every in-flight query rather than a named few: the chip's claim is "the map is still
 * filling in", which is exactly what a non-zero fetch count means, and a hand-kept list of
 * feeds would go stale the first time one was added.
 */
export function DataLoadingChip() {
  const fetchingCount = useIsFetching();
  if (fetchingCount === 0) return null;

  return (
    <div
      role="status"
      aria-live="polite"
      className="pointer-events-none absolute left-1/2 top-3 z-20 -translate-x-1/2 rounded-full border border-[hsl(var(--border))] bg-[hsl(var(--card))]/95 px-3 py-1 text-[11px] font-medium text-[hsl(var(--muted-foreground))] shadow-sm backdrop-blur"
    >
      <span
        aria-hidden="true"
        className="mr-1.5 inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-[hsl(var(--primary))] align-middle"
      />
      Loading map data…
    </div>
  );
}
