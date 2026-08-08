"use client";

import { CalendarDays, PanelLeftOpen } from "lucide-react";
import { cn } from "@/lib/utils";
import { usePanelStore } from "@/stores/panel-store";
import {
  isCalendarDate,
  isFutureDate,
  useTimeSliderStore,
} from "@/stores/time-slider-store";

/**
 * The pill row's anchor, forming the right end of the top bar: `SearchBar` holds `top-4 left-4`,
 * this holds `top-4` on the right, and MapLibre's own control stack keeps the far right edge.
 * `right-16` clears that stack (29px wide, 44px under the (max-width: 640px) rule in
 * globals.css). Width is shrink-to-fit growing leftwards, capped against the viewport so a long
 * date-plus-chip can never run off the screen or under the search field.
 *
 * `pointer-events-none` on the row with `pointer-events-auto` on its children (through
 * TOP_BAR_CONTROL_CLASSES): the row is a box floating over the canvas, and without the
 * pass-through its padding would swallow map drags that start under it. It carries no
 * `overflow-*` -- it is one row of controls, not the 24rem scrolling column it replaced.
 */
const PILL_ROW_CLASSES =
  "pointer-events-none absolute right-16 top-4 z-10 flex max-w-[calc(100vw-5rem)] items-center justify-end gap-2";

/**
 * The pill and the bar's Today button share the toolbar chrome so the whole top row reads as
 * one system; tokens match FloatingToolbar's. `max-sm:min-h-11` is the same 44px mobile
 * tap-target rule every other control follows.
 */
const TOP_BAR_CONTROL_CLASSES =
  "pointer-events-auto flex items-center gap-1.5 rounded-xl border border-(--glass-border) bg-(--glass-bg) px-2.5 py-1.5 text-xs font-medium text-[hsl(var(--foreground))] shadow-(--shadow-lg) [backdrop-filter:blur(var(--glass-blur))] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[hsl(var(--ring))] max-sm:min-h-11";

/**
 * The map's date, as the one thing about time that never leaves the screen.
 *
 * This is all that remains of `TimeSliderPanel`'s right-hand region. The scrubber moved into
 * the left dock's Time section on 2026-08-08; what could not move with it is the CLAIM -- the
 * map draws one day and every layer draws as of it, so an off-today date silently filters the
 * whole map. A marker for that cannot be something a reader opens a panel to find, which is
 * why this row is mounted unconditionally, over the canvas, whatever the dock is doing.
 *
 * It is no longer a disclosure. Clicking it calls `focusDockSection("time")`: the dock opens,
 * the Time section expands and scrolls itself into view -- the same three-fact handshake the
 * toolbar's alert bell uses. There is one scrubber now instead of a card that could be open
 * here and a section that could be open there, which is what the `aria-expanded` this button
 * used to carry would have had to lie about.
 *
 * The bar-level Today reset stays beside it for the same reason the pill does: getting back to
 * the live day must not cost a panel open.
 */
export default function TimeDatePill() {
  const selectedDate = useTimeSliderStore((state) => state.selectedDate);
  const capabilities = useTimeSliderStore((state) => state.capabilities);
  const capabilitiesUnavailable = useTimeSliderStore((state) => state.capabilitiesUnavailable);
  const resetToToday = useTimeSliderStore((state) => state.resetToToday);
  const focusDockSection = usePanelStore((state) => state.focusDockSection);

  // What the pill can honestly say. Before the payload lands there is no date and no "today",
  // so this renders nothing rather than a placeholder claiming one -- the same rule TimeSlider
  // itself follows. The one exception is an outright failed fetch, which must not look like
  // "no pill": it then names the failure and still opens the section that explains it.
  const hasDay = capabilities !== null && isCalendarDate(selectedDate);
  const fetchFailed = capabilities === null && capabilitiesUnavailable;
  const serverToday = capabilities?.serverCurrentDate ?? null;
  const isOffToday = hasDay && serverToday !== null && selectedDate !== serverToday;
  const isFuture =
    capabilities !== null &&
    isCalendarDate(selectedDate) &&
    isFutureDate(selectedDate, capabilities);

  if (!hasDay && !fetchFailed) return null;

  return (
    // No role and no aria-label on the row: it is a positioning box, and naming a generic
    // container only adds a landmark-shaped thing a screen reader has to step through. The
    // button inside carries the name.
    <div className={PILL_ROW_CLASSES} data-testid="map-date-pill">
      {/* One-tap return to the live day without opening the dock: an off-today date silently
          filters EVERY layer, so the way back must cost no panel. The dock's own card carries
          a second Today button next to its date field; they are the same act at two distances,
          not two controls over one value. */}
      {isOffToday && (
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
        aria-label="Open time controls"
        title="Open the map date controls"
        onClick={() => focusDockSection("time")}
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
            {/* Silent only at the live default. Any other day is a claim about the whole map,
                so the pill says which kind: a real observed day in the past, or a day past the
                end of the record. */}
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
        {/* A left-panel glyph rather than the chevron this button used to wear: the chevron
            promised the card would unfold underneath it, and the click now sends the reader to
            the dock on the opposite edge. It is the mirror of the `PanelLeftClose` the dock's
            own header carries, so the pair reads as one panel opening and shutting. */}
        <PanelLeftOpen
          aria-hidden="true"
          className="size-3.5 shrink-0 text-[hsl(var(--muted-foreground))]"
        />
      </button>
    </div>
  );
}
