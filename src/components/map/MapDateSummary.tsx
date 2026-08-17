"use client";

import { useMemo } from "react";
import { CalendarDays } from "lucide-react";
import { layerLabel, type LayerToggleId } from "@/lib/map/layer-registry";
import { useViewedLayerDays, type ViewedLayerDay } from "@/lib/map/layer-toggle-context";
import {
  dayOffset,
  latestObservedDateFor,
  useTimeSliderStore,
} from "@/stores/time-slider-store";
import { useDrawnLayerDayStore, type DrawnLayerDay } from "@/stores/useMetricAtDate";
import type { SliderCapabilities } from "@/types/time-slider";

/** Where a drawn day sits against the server's today. */
export type ViewedDayStanding = "live" | "past" | "beyond_record" | "unknown";

/**
 * One visible layer as this surface may state it: the day actually PAINTED, plus what is still
 * on its way.
 *
 * `ViewedLayerDay.date` is the row's settled slider position, which is the day being asked for
 * and not necessarily the day in hand. Since `LayerManager` retains the previous collection
 * while the next loads (`keepPreviousData`), those two differ for a whole warehouse round trip
 * after every scrub -- and captioning the request would state a day the canvas is not showing.
 * The rule is `useMetricAtDate.resolvedDate`'s: the surface may lag, it may not misstate.
 */
export interface DrawnViewedLayerDay extends ViewedLayerDay {
  /** The day being waited on, or null when the painted day IS the day the row asks for. */
  pendingDate: string | null;
  /** A request whose answer is not yet on the canvas is open. False while offline pauses it. */
  isLoading: boolean;
}

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
  /** Visible layers provably behind their own newest published day, judged on the DRAWN day. */
  behindLatestCount: number;
  /** Visible layers with an open request whose answer is not yet painted. */
  loadingCount: number;
  /** Visible layers painting a day OLDER than the one their row is asking for. */
  pendingCount: number;
}

/**
 * Each visible layer restated on the day it is actually drawing.
 *
 * `isOnLatest` is RE-ANSWERED against that day rather than carried over. `useViewedLayerDays`
 * answers it for the day the row is asking for, which is right for the agent payload and wrong
 * here: every qualifier in a sentence must be about the day that sentence names. Carrying it
 * over put two different days in one line -- click "Latest" on a scrubbed layer and the row read
 * an old date with no "behind its latest" mark, while the reverse case marked a day that WAS the
 * latest as behind it. The rule itself is `useViewedLayerDays`'s, unchanged; only its subject is.
 *
 * A layer nothing has published for keeps its row's day and reports no loading state. That is
 * honest only while unpublished layers BLANK during a load rather than retaining — a layer given
 * `keepPreviousData` without a report here would be captioned with a day it is not painting, so
 * the two must be added together.
 */
export function resolveDrawnViewedDays(
  viewed: ViewedLayerDay[],
  drawnDays: Partial<Record<LayerToggleId, DrawnLayerDay>>,
  capabilities: SliderCapabilities | null
): DrawnViewedLayerDay[] {
  return viewed.map((layer) => {
    const drawn = drawnDays[layer.layerId];
    if (drawn === undefined) return { ...layer, pendingDate: null, isLoading: false };
    // A feed whose read carries no day (SSURGO, proxied per viewport) publishes a null drawn
    // date and contributes only its loading state; the row's own day still names it.
    const drawnDate = drawn.drawnDate ?? layer.date;
    const latestObservedDate = latestObservedDateFor(capabilities, layer.layerId);
    return {
      ...layer,
      date: drawnDate,
      pendingDate:
        drawn.requestedDate !== null && drawn.requestedDate !== drawnDate
          ? drawn.requestedDate
          : null,
      isLoading: drawn.isLoading,
      isOnLatest: latestObservedDate === null || drawnDate >= latestObservedDate,
    };
  });
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
  viewed: DrawnViewedLayerDay[],
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
    loadingCount: viewed.filter((layer) => layer.isLoading).length,
    pendingCount: viewed.filter((layer) => layer.pendingDate !== null).length,
  };
}

/** "1 layer is behind its latest" / "3 layers are behind their latest". */
function describeBehindLatest(behindLatestCount: number): string {
  return behindLatestCount === 1
    ? "1 layer behind its latest"
    : `${behindLatestCount} layers behind their latest`;
}

/**
 * "1 layer on an earlier day" / "3 layers on earlier days".
 *
 * Counted over layers whose PAINTED day is older than the day their row asks for, which is not
 * the same population as "a request is open": a pan re-reads the same day over new ground, and
 * offline pauses a fetch without cancelling it (`fetchStatus: "paused"`, so a retained frame
 * stands with nothing in flight). Saying "still loading" was therefore false for as long as
 * connectivity was out, and contradicted the absent Updating chip beside it. This wording is
 * true in all three states, and it is the one worth a line of chrome: the control has moved
 * ahead of the canvas.
 */
function describeShowingEarlierDay(pendingCount: number): string {
  return pendingCount === 1
    ? "1 layer on an earlier day"
    : `${pendingCount} layers on earlier days`;
}

/**
 * The whole composite in one sentence plus a line per layer, for a tooltip and for a screen
 * reader. This is the escape hatch that keeps the visible surface small: the span says THAT the
 * days differ, this says exactly which layer is on which day, without a dozen rows on canvas.
 */
export function describeViewedDays(
  viewed: DrawnViewedLayerDay[],
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
  // Said in full, because the one-line surface can only carry a count.
  const loading =
    summary.pendingCount === 0
      ? []
      : [
          `${describeShowingEarlierDay(summary.pendingCount)}: each is painting the day it last ` +
            `loaded until its own request lands.`,
        ];
  const rows = [...viewed]
    .sort((left, right) => left.date.localeCompare(right.date))
    .map(
      (layer) =>
        `${layerLabel(layer.layerId)}: ${layer.date}` +
        (layer.pendingDate === null ? "" : ` (loading ${layer.pendingDate})`) +
        (layer.isOnLatest ? "" : " (behind its latest)")
    );
  return [headline, ...loading, ...rows].join("\n");
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
 * And what it states is the day DRAWN, never the day requested. It read the sliders' positions
 * until 2026-08-16, which was correct only while a layer blanked during a load; now that the
 * previous collection is retained (`keepPreviousData`, see `LayerManager`) the two differ for a
 * whole warehouse round trip after every scrub, and asserting the request would put the new day
 * over the old day's features. Ordinary latency then reads as a data bug -- which is exactly the
 * failure this surface exists to prevent, one layer down. So the headline names what is painted,
 * an "Updating" chip names the fetch, and the second line counts the layers whose control has
 * moved ahead of their canvas.
 *
 * No `role="status"` and no `aria-live`: it re-renders on every layer toggle and on every settle
 * of every row's scrub, so announcing it would talk over the control the reader is dragging. The
 * same full text a sighted reader gets on hover is in the DOM as `sr-only` text instead.
 */
export function MapDateSummary() {
  const viewedLayerDays = useViewedLayerDays();
  // What the layers say they are PAINTING, which is what this surface states. The rows above
  // carry the slider's position; captioning that would name a day the canvas is not showing for
  // the length of every fetch. See `src/stores/useMetricAtDate.ts` §DrawnLayerDay.
  const drawnDays = useDrawnLayerDayStore((state) => state.drawnDays);
  // The whole payload, because "behind its latest" is re-answered per layer against the day
  // actually drawn -- see `resolveDrawnViewedDays`.
  const capabilities = useTimeSliderStore((state) => state.capabilities);
  const serverCurrentDate = capabilities?.serverCurrentDate ?? null;

  const drawnLayerDays = useMemo(
    () => resolveDrawnViewedDays(viewedLayerDays, drawnDays, capabilities),
    [viewedLayerDays, drawnDays, capabilities]
  );
  const summary = useMemo(
    () => summariseViewedDays(drawnLayerDays, serverCurrentDate),
    [drawnLayerDays, serverCurrentDate]
  );
  const detail = useMemo(
    () => (summary === null ? "" : describeViewedDays(drawnLayerDays, summary)),
    [drawnLayerDays, summary]
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
  if (summary.pendingCount > 0) {
    secondLineParts.push(describeShowingEarlierDay(summary.pendingCount));
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
          {/* The latency signal, and a wider population than `pendingCount`: a pan re-reads the
              same day over new ground, which a reader must be able to tell from a day that is
              simply empty. Silent while offline pauses a fetch -- nothing is in flight then, and
              the second line carries that case instead. */}
          {summary.loadingCount > 0 && (
            <span className={MUTED_CHIP_CLASSES} data-testid="map-date-summary-loading">
              Updating
            </span>
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
