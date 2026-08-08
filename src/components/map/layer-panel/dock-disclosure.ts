"use client";

/**
 * The chrome every dock disclosure shares: one row style, one scroll-on-request behaviour.
 *
 * Extracted on 2026-08-08, when the time scrubber became a section of this dock. Two sections
 * now open the same way while differing in what they hold -- `DetailsSection` mounts a
 * dynamically-imported report, `TimeDockSection` holds the always-cheap scrubber card -- and
 * the dock's stated rule is that a caret and a name are ONE vocabulary here, not a style each
 * surface re-types. Keeping the row's class list and the pending-scroll handshake in one module
 * is what makes that enforceable rather than aspirational.
 *
 * Free of JSX on purpose so it stays a `.ts` file: nothing here renders, it only describes and
 * coordinates.
 */

import { useEffect, type RefObject } from "react";
import { usePanelStore, type DockSectionId } from "@/stores/panel-store";

/** True when the reader has asked the platform to stop animating things at them. */
function prefersReducedMotion(): boolean {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") return false;
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

/**
 * The disclosure row's class list, expanded and collapsed.
 *
 * `max-sm:min-h-11` is the 44px mobile tap target every control on this map holds to; the
 * expanded state gets a filled background so a long dock reads as a stack of open and shut
 * drawers rather than a list of identical rows.
 */
export function dockDisclosureRowClasses(isExpanded: boolean): string {
  return [
    "flex min-h-8 w-full items-center gap-1.5 rounded-md px-1 py-1 text-left text-[11px]",
    "font-medium transition-colors focus-visible:outline-none focus-visible:ring-2",
    "focus-visible:ring-[hsl(var(--ring))] max-sm:min-h-11",
    isExpanded
      ? "bg-[hsl(var(--muted)/0.5)] text-[hsl(var(--foreground))]"
      : "text-[hsl(var(--muted-foreground))] hover:bg-[hsl(var(--muted)/0.4)] hover:text-[hsl(var(--foreground))]",
  ].join(" ");
}

/**
 * Consumes `pendingScrollSection` on behalf of one section: scrolls itself into the dock's one
 * scroller, then clears the request.
 *
 * The clearing is the load-bearing half. "Open the dock at Alerts" is two facts -- expand it,
 * and put it on screen -- and the second is an event, not state a section renders from; leaving
 * it set would re-scroll the dock out from under a reader who later expanded something by hand.
 */
export function useDockSectionScroll(
  id: DockSectionId,
  containerRef: RefObject<HTMLElement | null>
): void {
  const pendingScrollSection = usePanelStore((state) => state.pendingScrollSection);
  const clearPendingScroll = usePanelStore((state) => state.clearPendingScroll);

  useEffect(() => {
    if (pendingScrollSection !== id) return;
    const container = containerRef.current;
    // jsdom implements no scrollIntoView, and neither does an unmounted node; the request is
    // consumed either way so a stale target cannot pin a later expansion in place.
    if (container && typeof container.scrollIntoView === "function") {
      container.scrollIntoView({
        block: "start",
        behavior: prefersReducedMotion() ? "auto" : "smooth",
      });
    }
    clearPendingScroll();
  }, [pendingScrollSection, id, containerRef, clearPendingScroll]);
}
