"use client";

import { useMemo } from "react";
import { CalendarDays } from "lucide-react";
import { layerLabel } from "@/lib/map/layer-registry";
import { useViewedLayerDays, type ViewedLayerDay } from "@/lib/map/layer-toggle-context";
import { dayOffset, useTimeSliderStore } from "@/stores/time-slider-store";

/** Where a drawn day sits against the server's today. */
export type ViewedDayStanding = "live" | "past" | "beyond_record" | "unknown";

/** What the visible layers, taken together, are showing and as of when. */
export interface ViewedDateSummary {
  /** How many visible layers name a day. */
  layerCount: number;
  /** Every distinct day drawn, oldest first. */
  distinctDays: string[];
  /** The one day every visible layer is on, or null when they differ. */
  sharedDay: string | null;
  /** Oldest day any visible layer is on. */
  earliestDay: string;
  /** Newest day any visible layer is on. */
  latestDay: string;
  /** Where `latestDay` stands against the server's today; also `sharedDay`'s standing. */
  latestDayStanding: ViewedDayStanding;
  /** Whole days from `earliestDay` to `latestDay`; 0 when one day is shared. */
  spanDays: number;
  /** Visible layers provably behind their own newest published day. */
  behindLatestCount: number;
}

/**
 * Where one day stands against the server's today.
 *
 * "unknown" without a server today rather than a guess from the browser clock: across New Year
 * the two disagree by a whole year, which is the trap `serverCurrentDate` exists to close. The
 * `<` comparison is a string compare and is exact -- YYYY-MM-DD sorts lexicographically.
 */
function dayStanding(day: string, serverCurrentDate: string | null): ViewedDayStanding {
  if (serverCurrentDate === null) return "unknown";
  if (day === serverCurrentDate) return "live";
  return day < serverCurrentDate ? "past" : "beyond_record";
}

/**
 * What the visible layers are showing, or null when none of them names a day.
 *
 * Null rather than an empty summary, and the component then renders NOTHING. A surface that
 * said "no dates" while the canvas draws a basemap and nothing else would be a claim about a
 * composite that does not exist; silence over an empty map asserts nothing, which is the only
 * honest answer. `useViewedLayerDays` has already dropped every layer whose day cannot be
 * named, so an empty list here means exactly "nothing dated is drawn".
 */
export function summariseViewedDays(
  viewed: ViewedLayerDay[],
  serverCurrentDate: string | null
): ViewedDateSummary | null {
  if (viewed.length === 0) return null;
  const distinctDays = Array.from(new Set(viewed.map((layer) => layer.date))).sort();
  const earliestDay = distinctDays[0];
  const latestDay = distinctDays[distinctDays.length - 1];
  return {
    layerCount: viewed.length,
    distinctDays,
    sharedDay: distinctDays.length === 1 ? earliestDay : null,
    earliestDay,
    latestDay,
    latestDayStanding: dayStanding(latestDay, serverCurrentDate),
    spanDays: dayOffset(earliestDay, latestDay),
    // `isOnLatest` is true whenever a layer is not PROVABLY behind, including when its newest
    // published day is unknown -- see MapDay.isBehindLatestObservedDate. So this counts layers
    // measured to be behind, never layers we merely cannot vouch for.
    behindLatestCount: viewed.filter((layer) => !layer.isOnLatest).length,
  };
}

/** "1 layer is behind its latest" / "3 layers are behind their latest". */
function describeBehindLatest(behindLatestCount: number): string {
  return behindLatestCount === 1
    ? "1 layer behind its latest"
    : `${behindLatestCount} layers behind their latest`;
}

/**
 * The whole composite in one sentence plus a line per layer, for a tooltip and for a screen
 * reader. This is the escape hatch that keeps the visible surface small: the span says THAT the
 * days differ, this says exactly which layer is on which day, without a dozen rows on canvas.
 */
export function describeViewedDays(
  viewed: ViewedLayerDay[],
  summary: ViewedDateSummary
): string {
  const headline =
    summary.sharedDay !== null
      ? `${
          summary.layerCount === 1
            ? "The one visible layer is"
            : `All ${summary.layerCount} visible layers are`
        } showing ${summary.sharedDay}.`
      : `The ${summary.layerCount} visible layers are on ${summary.distinctDays.length} ` +
        `different days spanning ${summary.spanDays} days, from ${summary.earliestDay} to ` +
        `${summary.latestDay}. This is a mixed-time composite, not one moment.`;
  const rows = [...viewed]
    .sort((left, right) => left.date.localeCompare(right.date))
    .map(
      (layer) =>
        `${layerLabel(layer.layerId)}: ${layer.date}` +
        (layer.isOnLatest ? "" : " (behind its latest)")
    );
  return [headline, ...rows].join("\n");
}

/**
 * The anchor box, holding the top-right corner the deleted `TimeDatePill` used to hold.
 *
 * `right-16` clears MapLibre's own top-right control stack (29px wide, 44px under the
 * (max-width: 640px) rule in globals.css); `top-4` is the top row, which the manager's left-edge
 * column and the bottom-left rail both leave free. Width is shrink-to-fit growing leftwards and
 * capped against the viewport, so a long mixed-date line can never run off screen.
 *
 * `pointer-events-none` here with `pointer-events-auto` on the surface inside: without the
 * pass-through the box's padding would swallow map drags that start under it.
 */
const SUMMARY_ANCHOR_CLASSES =
  "pointer-events-none absolute right-16 top-4 z-10 flex max-w-[calc(100vw-5rem)] justify-end";

/** The glass chrome the rest of the map's floating chrome wears; tokens match `ManagerRail`. */
const SUMMARY_SURFACE_CLASSES =
  "pointer-events-auto flex flex-col gap-0.5 rounded-xl border border-(--glass-border) " +
  "bg-(--glass-bg) px-2.5 py-1.5 text-xs font-medium text-[hsl(var(--foreground))] " +
  "shadow-(--shadow-lg) [backdrop-filter:blur(var(--glass-blur))]";

const CHIP_CLASSES =
  "shrink-0 rounded-(--radius) px-1.5 py-0.5 text-[0.6875rem] font-semibold uppercase tracking-wide";
const MUTED_CHIP_CLASSES = `${CHIP_CLASSES} bg-[hsl(var(--muted-foreground))]/20 text-[hsl(var(--muted-foreground))]`;
const PRIMARY_CHIP_CLASSES = `${CHIP_CLASSES} bg-[hsl(var(--primary))]/15 text-[hsl(var(--primary))]`;

/**
 * What the map is drawn as of, stated on the canvas and never inside anything closable.
 *
 * The per-layer sliders made this map a mixed-time composite: fire on today beside vegetation on
 * 2025-06-14 renders as ONE image, and a screenshot of it carries no date at all. Every date in
 * the app was, briefly, inside the manager -- reachable only with the dock docked, the row's
 * group expanded, the layer switched on and an axis published for it -- so closing the dock left
 * three different months looking like one moment. That is the failure this surface exists to
 * make impossible, which is why it is mounted beside the canvas in `MapView` rather than in the
 * dock, and why it renders whatever the dock is doing.
 *
 * It STATES and never sets. The controls are the per-row sliders in the manager; a second date
 * control here would be the map-wide day this feature deleted, wearing a smaller hat. Nothing in
 * it is clickable, and its only interaction is a `title` on hover.
 *
 * No `role="status"` and no `aria-live`: it re-renders on every layer toggle and on every settle
 * of every row's scrub, so announcing it would talk over the control the reader is dragging. The
 * same full text a sighted reader gets on hover is in the DOM as `sr-only` text instead.
 */
export function MapDateSummary() {
  const viewedLayerDays = useViewedLayerDays();
  const serverCurrentDate = useTimeSliderStore(
    (state) => state.capabilities?.serverCurrentDate ?? null
  );

  const summary = useMemo(
    () => summariseViewedDays(viewedLayerDays, serverCurrentDate),
    [viewedLayerDays, serverCurrentDate]
  );
  const detail = useMemo(
    () => (summary === null ? "" : describeViewedDays(viewedLayerDays, summary)),
    [viewedLayerDays, summary]
  );

  if (summary === null) return null;

  const isMixed = summary.sharedDay === null;
  // The second line only exists when there is a second thing to say. A map whose layers share
  // one current day is one row of chrome, which is the common case and must stay unobtrusive.
  const secondLineParts: string[] = [];
  if (isMixed) {
    secondLineParts.push(
      `${summary.earliestDay} – ${summary.latestDay}`,
      `${summary.layerCount} layers`,
      `${summary.spanDays} days apart`
    );
  }
  if (summary.behindLatestCount > 0) {
    secondLineParts.push(describeBehindLatest(summary.behindLatestCount));
  }

  return (
    // No role and no aria-label on either box: they are positioning and chrome, and naming a
    // generic container only adds a landmark a screen reader has to step through. The text
    // inside is the statement.
    <div className={SUMMARY_ANCHOR_CLASSES} data-testid="map-date-summary">
      <div className={SUMMARY_SURFACE_CLASSES} title={detail}>
        <div className="flex items-center gap-1.5">
          <CalendarDays
            aria-hidden="true"
            className="size-3.5 shrink-0 text-[hsl(var(--muted-foreground))]"
          />
          <span data-testid="map-date-summary-headline">
            {isMixed ? "Mixed dates" : summary.sharedDay}
          </span>
          {/* Silent at the live edge. Any other standing is a claim about what is drawn, so it
              is named: a real observed day behind today, or a day past the end of the record.
              In the mixed case the chip describes the NEWEST day drawn -- the second line
              carries the rest of the span. */}
          {summary.latestDayStanding === "beyond_record" && (
            <span className={MUTED_CHIP_CLASSES}>Beyond record</span>
          )}
          {!isMixed && summary.latestDayStanding === "past" && (
            <span className={PRIMARY_CHIP_CLASSES}>Past day</span>
          )}
        </div>
        {secondLineParts.length > 0 && (
          <span
            // `pl-5` hangs the line under the headline text rather than under the glyph.
            className="pl-5 text-[0.6875rem] font-normal text-[hsl(var(--muted-foreground))]"
            data-testid="map-date-summary-detail"
          >
            {secondLineParts.join(" · ")}
          </span>
        )}
        <span className="sr-only" data-testid="map-date-summary-full">
          {detail}
        </span>
      </div>
    </div>
  );
}
