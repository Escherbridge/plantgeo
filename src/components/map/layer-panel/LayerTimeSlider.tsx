"use client";

import { useEffect, useId, useMemo, useState, type KeyboardEvent } from "react";
import {
  describeCoverageBand,
  describeCoverageTopology,
  describeDayCoverage,
  dayCoverageState,
  drawCoverageBands,
  percentOfDayOffset,
  TRACK_REGION_APPEARANCE,
} from "@/components/map/layer-panel/layer-coverage-track";
import { layerLabel, type LayerToggleId } from "@/lib/map/layer-registry";
import { useLayerDay } from "@/lib/map/layer-toggle-context";
import { cn } from "@/lib/utils";
import {
  addDays,
  clampDateToDomain,
  dayOffset,
  findLayerCapability,
  isCalendarDate,
  sliderDomain,
  sliderMaxOffset,
  todayOffset,
  useTimeSliderStore,
  warehouseLayerNameFor,
} from "@/stores/time-slider-store";

/**
 * Injected once per document, not once per instance.
 *
 * The same guard `ui/slider.tsx` uses, and for a sharper reason: the layer tree mounts one of
 * these on every switched-on layer, so a `<style>` in the component body would put a dozen
 * identical blocks in the DOM. Keyed on the element id rather than a module flag so a second
 * bundle instance or a fast-refresh remount still resolves to one element, and never removed --
 * the rules are static, and tearing them down on the last unmount would only risk an unstyled
 * control when the next row switches on.
 *
 * A local range input rather than `@/components/ui/slider`: that component paints a two-stop
 * gradient over its own track, which would bury every coverage band underneath it, and it
 * forwards no `aria-valuetext`, so a screen reader would announce the day OFFSET instead of the
 * day.
 */
const LAYER_TIME_SLIDER_STYLE_ELEMENT_ID = "plantgeo-layer-time-slider-styles";

const layerTimeSliderStyles = `
  .layer-time-slider-range {
    -webkit-appearance: none;
    appearance: none;
    background: transparent;
    /* Taller than the 12px track and thumb on purpose: this is the drag control's hit area in a
       19rem column, so it grows without changing anything that is drawn. */
    height: 20px;
    width: 100%;
    outline: none;
    cursor: pointer;
    margin: 0;
  }
  .layer-time-slider-range::-webkit-slider-runnable-track {
    background: transparent;
    height: 12px;
  }
  .layer-time-slider-range::-moz-range-track {
    background: transparent;
    height: 12px;
  }
  .layer-time-slider-range::-webkit-slider-thumb {
    -webkit-appearance: none;
    appearance: none;
    width: 12px;
    height: 12px;
    border-radius: 50%;
    background: hsl(var(--primary));
    border: 2px solid hsl(var(--background));
    cursor: pointer;
    box-shadow: 0 0 0 1px hsl(var(--primary) / 0.35);
    transition: box-shadow 150ms;
  }
  .layer-time-slider-range:focus-visible::-webkit-slider-thumb {
    box-shadow: 0 0 0 3px hsl(var(--ring) / 0.6);
  }
  .layer-time-slider-range::-moz-range-thumb {
    width: 12px;
    height: 12px;
    border-radius: 50%;
    background: hsl(var(--primary));
    border: 2px solid hsl(var(--background));
    cursor: pointer;
    box-shadow: 0 0 0 1px hsl(var(--primary) / 0.35);
    transition: box-shadow 150ms;
  }
  .layer-time-slider-range:focus-visible::-moz-range-thumb {
    box-shadow: 0 0 0 3px hsl(var(--ring) / 0.6);
  }
  /* Without this the UA paints the calendar glyph for a light page, which on the default dark
     theme is a near-black icon on a near-black field: the picker is there but invisible, so the
     field reads as plain text and nobody clicks it. */
  .layer-time-slider-date-input {
    color-scheme: light dark;
  }
  /* 44px tap target on a phone, where the panel is a full-screen overlay and this is the one
     drag control on the row. Only the hit area grows; the thumb and the track keep their size,
     and the row around it carries max-sm:h-11 so it still contains the taller input. */
  @media (max-width: 640px) {
    .layer-time-slider-range {
      height: 2.75rem;
    }
  }
`;

function useLayerTimeSliderStyles(): void {
  useEffect(() => {
    if (typeof document === "undefined") return;
    if (document.getElementById(LAYER_TIME_SLIDER_STYLE_ELEMENT_ID) !== null) return;
    const style = document.createElement("style");
    style.id = LAYER_TIME_SLIDER_STYLE_ELEMENT_ID;
    style.textContent = layerTimeSliderStyles;
    document.head.appendChild(style);
  }, []);
}

/**
 * Days one Page Up or Page Down press moves the thumb, and one held with Shift.
 *
 * `step={1}` makes an arrow press one day, which is the right grain for reading a record and a
 * hopeless one for crossing it: vegetation's axis is roughly 1,460 days, so reaching last spring
 * from the live edge is some four hundred presses. A month and a year are the two spans a reader
 * of a date axis actually thinks in, so those are the two the keyboard offers.
 *
 * Applied by this component rather than left to the browser on purpose, even though every engine
 * already moves a range input on Page Up/Down. That native step is an undocumented fraction of
 * the RANGE -- so the same press covers a fortnight on one row and five months on the next, and
 * nothing on the page can state what it will do. A fixed span in days is the same promise on
 * every row, which is what makes it describable to the screen-reader user who needs it most.
 */
const COARSE_STEP_DAYS = 30;
const VERY_COARSE_STEP_DAYS = 365;

/**
 * Why this layer has no scrubbable axis, named rather than left blank.
 *
 * Five separate facts, and collapsing them would misinform in both directions: a snapshot has a
 * perfectly complete record that simply does not vary by day, while an unpublished layer has no
 * record at all, and a row that said the same thing about both would be wrong about one of them.
 * The row keeps showing its date either way -- the layer is still drawing as of some day, and
 * hiding that is what makes a mixed-time map unreadable.
 *
 * A PRECONDITION refusal, not an ordinary state: `LayerRow` mounts this control only for a layer
 * that has a selectable day or whose capabilities have not landed yet, so reaching this branch
 * means the row's gate and `sliderDomain` disagreed. It states which fact it was mounted
 * without rather than returning null, because a bare silent return is exactly the shape of the
 * defect this whole control exists to remove.
 */
function describeMissingAxis(
  warehouseLayerName: string | null,
  capability: ReturnType<typeof findLayerCapability>,
  serverCurrentDate: string | null,
  streamsUnavailable: boolean
): string {
  if (warehouseLayerName === null) {
    return "No warehouse layer backs this one, so it has no record of its own to scrub.";
  }
  // Checked BEFORE the null-capability sentence below, which would otherwise report a timed-out
  // scan as "not published yet" -- a claim about the warehouse built out of a failed query, and
  // the precise inversion this control exists to refuse. The absence is ours, not the record's.
  if (streamsUnavailable && capability === null) {
    return "Its history could not be read just now, so no range can be drawn yet. Retrying.";
  }
  if (capability === null) {
    return "Not published to the warehouse record yet, so it has no dates of its own.";
  }
  if (capability.temporalKind === "snapshot") {
    return "A snapshot, not a daily record: it draws the same on every date.";
  }
  if (capability.earliestObservedDate === null) {
    return "Nothing observed yet, so there is no range to scrub.";
  }
  if (serverCurrentDate !== null && capability.earliestObservedDate > serverCurrentDate) {
    return `Its record starts on ${capability.earliestObservedDate}, after today, so no range can be drawn.`;
  }
  return "No range can be drawn from this layer's record.";
}

export interface LayerTimeSliderProps {
  layerId: LayerToggleId;
  className?: string;
}

/**
 * One layer's own day, scrubbed on its own axis, drawn over its own coverage.
 *
 * The successor to the single global slider. Every row carries one, so the map is a mixed-time
 * composite: fire on 2026-08-07 beside vegetation on 2025-06-14 looks like one moment in a
 * screenshot unless each row states its own day and marks itself when it is not on its own
 * newest one. Both of those are non-negotiable here, which is why the date is always rendered --
 * even for a layer with no axis at all -- and why the behind-latest mark is a word and not a
 * colour.
 *
 * It also owns the account of why there is no axis to draw. `LayerRow` mounts this control for
 * any layer that has a selectable day OR whose capabilities have not landed yet, precisely so
 * that a failed `getSliderCapabilities` reaches a control that can SAY so: the row cannot know
 * whether a layer has dates while the payload is missing, and refusing the control on that
 * unknown is what silently stripped the time control from every row during the read model's
 * bigint 500 with nothing anywhere naming the cause.
 *
 * Presentational and store-driven: no tRPC of its own, so it renders against a fixture without
 * a provider. `TimeSliderCapabilitiesLoader` remains the one reader and the one writer of
 * capabilities.
 */
export function LayerTimeSlider({ layerId, className }: LayerTimeSliderProps) {
  useLayerTimeSliderStyles();

  const label = layerLabel(layerId);
  const { selectedDate, serverCurrentDate, latestObservedDate, isBehindLatestObservedDate } =
    useLayerDay(layerId);
  const capabilities = useTimeSliderStore((state) => state.capabilities);
  const capabilitiesUnavailable = useTimeSliderStore((state) => state.capabilitiesUnavailable);
  // Whether this layer is PINNED to a day of its own, which is a different question from
  // whether that day happens to equal its newest one -- see `canReturnToLatest`. Selected as a
  // boolean so a scrub on any other row cannot re-render this one.
  const hasOwnDateOverride = useTimeSliderStore(
    (state) => state.layerDates[layerId] !== undefined
  );
  const setLayerDate = useTimeSliderStore((state) => state.setLayerDate);
  const resetLayerDate = useTimeSliderStore((state) => state.resetLayerDate);
  const dateInputId = useId();
  const coverageSummaryId = useId();
  const noteId = useId();

  // The date field's own value while it is being edited. A `<input type="date">` reports "" for
  // every incomplete entry, so a directly-bound store value would push this layer to a clamped
  // day on the first keystroke of a typed date.
  const [draftDate, setDraftDate] = useState(selectedDate ?? "");
  // Adjusted during render rather than in an effect, which is React's own prescription for state
  // that has to follow an external value: an effect paints one stale frame first, and re-running
  // it on every pointer tick of a scrub is the cascade the lint rule warns about. Tracking the
  // last day seen is what makes this fire on a real change -- a scrub, the Latest button, the
  // clamp a late capabilities payload applies -- and never on editing the field itself.
  const [lastSeenSelectedDate, setLastSeenSelectedDate] = useState(selectedDate ?? "");
  if ((selectedDate ?? "") !== lastSeenSelectedDate) {
    setLastSeenSelectedDate(selectedDate ?? "");
    setDraftDate(selectedDate ?? "");
  }

  const warehouseLayerName = warehouseLayerNameFor(layerId);
  // Reference-stable while `capabilities` is: a `find` over the same array returns the same row.
  const capability =
    warehouseLayerName === null ? null : findLayerCapability(capabilities, warehouseLayerName);
  const domain = useMemo(
    () => (warehouseLayerName === null ? null : sliderDomain(capabilities, warehouseLayerName)),
    [capabilities, warehouseLayerName]
  );
  const bands = useMemo(
    () => (domain === null || capability === null ? [] : drawCoverageBands(domain, capability)),
    [domain, capability]
  );
  // The whole record's shape in one sentence, for the reader who cannot see the bands. Memoized
  // beside them because it is built from the same partition and changes on exactly the same
  // input.
  const coverageSummary = useMemo(
    () =>
      domain === null || capability === null
        ? null
        : describeCoverageTopology(domain, capability),
    [domain, capability]
  );

  // The fetch has never once succeeded and its last attempt failed. There is no honest axis to
  // draw and no honest "today" to anchor it to, so the row says why rather than going silent --
  // silence is what let a failing endpoint read as "this layer has no dates". Reachable only
  // because `LayerRow` mounts this control while the payload is unknown; gating that mount on an
  // axis instead made this branch unreachable and the outage invisible.
  if (capabilities === null && capabilitiesUnavailable) {
    return (
      <p
        className={cn("text-[10px] leading-relaxed text-[hsl(var(--muted-foreground))]", className)}
        data-testid={`layer-time-slider-unavailable-${layerId}`}
      >
        Dates could not be loaded. This is a loading failure, not a gap in the record.
      </p>
    );
  }

  // Capabilities still in flight. Rendering nothing rather than a placeholder is what keeps a
  // normal fast load from flashing a skeleton in and back out of every switched-on row.
  if (capabilities === null || selectedDate === null) return null;

  // One guard for every state that leaves this layer without a scrubbable axis. The three
  // conditions are not independent -- `sliderDomain` already returns null whenever the warehouse
  // name or the capability row is missing -- but naming all three is what lets the compiler treat
  // them as present below, rather than leaving a `?? ""` fallback that could quietly clamp one
  // layer's day against another layer's axis.
  //
  // With capabilities in hand this is `LayerRow`'s own gate restated, so reaching it means the
  // gate and `sliderDomain` disagreed -- see `describeMissingAxis`. It states the disagreement
  // instead of returning null, and keeps showing the day the layer is drawing as of either way.
  if (domain === null || warehouseLayerName === null || capability === null) {
    return (
      <div
        className={cn("flex flex-col gap-0.5", className)}
        data-testid={`layer-time-slider-no-axis-${layerId}`}
      >
        {/* Still says which day this layer is drawing. It has no axis, but it is on the map as
            of some date, and a mixed-time composite is only readable while every row admits its
            own. */}
        <p className="text-[10px] tabular-nums text-[hsl(var(--foreground))]">{selectedDate}</p>
        <p className="text-[10px] leading-relaxed text-[hsl(var(--muted-foreground))]">
          {describeMissingAxis(
            warehouseLayerName,
            capability,
            serverCurrentDate,
            capabilities?.streamsUnavailable ?? false
          )}
        </p>
      </div>
    );
  }

  const { firstDay, lastDay, today } = domain;
  const maxOffset = sliderMaxOffset(domain);
  const todayTickOffset = todayOffset(domain);
  // Held on the track even if a resolved day somehow sits outside it, because a range input
  // silently clamps its own value and the thumb would then disagree with the date beside it.
  const selectedOffset = Math.min(
    maxOffset,
    Math.max(0, dayOffset(firstDay, selectedDate))
  );
  const coverageNote = describeDayCoverage(dayCoverageState(domain, capability, selectedDate));
  const daysBehindLatest =
    isBehindLatestObservedDate && latestObservedDate !== null
      ? dayOffset(selectedDate, latestObservedDate)
      : 0;
  // Enabled exactly while this layer is PINNED, which is what this button undoes: it deletes the
  // override so the row follows its own live edge again.
  //
  // Deliberately not "the selected day differs from the latest published one". Scrub away and
  // back -- or type the latest day in -- and the store holds an explicit override that merely
  // EQUALS the latest day, so that test disabled the only control that deletes it and left the
  // layer silently pinned behind its own record from the next payload on. That is the precise
  // harm the sparse default exists to avoid; see `TimeSliderState.layerDates`.
  //
  // A layer with no override needs nothing undone, so it stays disabled -- including one whose
  // newest day nobody knows, which is also the layer for which no jump target could be named.
  const canReturnToLatest = hasOwnDateOverride;

  // Ordered day-first: the coverage note is about the day the thumb is on, which is the more
  // immediate fact; the staleness note is about the row's relationship to its own record.
  const notes = [
    coverageNote === null ? null : `${coverageNote}.`,
    isBehindLatestObservedDate && latestObservedDate !== null
      ? `${daysBehindLatest} ${daysBehindLatest === 1 ? "day" : "days"} behind its latest, ${latestObservedDate}.`
      : null,
  ].filter((note): note is string => note !== null);

  /** Writes through on every tick so the thumb tracks the pointer; the query debounces. */
  const handleOffsetChange = (nextOffset: number) => {
    // Whole days only. A fractional position would name a day nothing was observed on.
    const wholeDayOffset = Math.round(nextOffset);
    setLayerDate(
      layerId,
      clampDateToDomain(addDays(firstDay, wholeDayOffset), capabilities, warehouseLayerName)
    );
  };

  /**
   * Commits a typed day, and only a complete in-domain one.
   *
   * The draft always takes the keystroke so the field stays editable; the store only hears about
   * days that are well formed. `min`/`max` already refuse an out-of-range day in every browser
   * that implements them, but the clamp stays the authority -- the attributes are a hint the DOM
   * can be driven around, and the layer's axis is not.
   */
  const handleDateInputChange = (nextValue: string) => {
    setDraftDate(nextValue);
    if (!isCalendarDate(nextValue)) return;
    setLayerDate(layerId, clampDateToDomain(nextValue, capabilities, warehouseLayerName));
  };

  /**
   * Page Up / Page Down as a fixed span of days, Shift for a year of them.
   *
   * The ONLY keys this handler claims, and it claims them completely -- `preventDefault` before
   * moving the value, so the engine's own Page step cannot land on top of ours and double it.
   * Every other key, the arrows and Home/End above all, is left to the native range: an explicit
   * handler for those would either duplicate the platform or quietly diverge from it.
   */
  const handleCoarseStepKey = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key !== "PageUp" && event.key !== "PageDown") return;
    event.preventDefault();
    const stepDays = event.shiftKey ? VERY_COARSE_STEP_DAYS : COARSE_STEP_DAYS;
    const direction = event.key === "PageUp" ? 1 : -1;
    handleOffsetChange(
      Math.min(maxOffset, Math.max(0, selectedOffset + direction * stepDays))
    );
  };

  return (
    <div
      className={cn("flex flex-col gap-1", className)}
      data-testid={`layer-time-slider-${layerId}`}
    >
      {/* Wraps rather than squashes: at 19rem, minus the row's 3.5rem indent, three controls is
          already the most this line holds, and a squashed date field is unreadable. */}
      <div className="flex flex-wrap items-center gap-1">
        <label className="sr-only" htmlFor={dateInputId}>
          {label} date, YYYY-MM-DD
        </label>
        <input
          id={dateInputId}
          type="date"
          className="layer-time-slider-date-input min-w-0 flex-1 rounded-(--radius) border border-[hsl(var(--border))] bg-[hsl(var(--background))] px-1 py-0.5 text-[10px] tabular-nums text-[hsl(var(--foreground))] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[hsl(var(--ring))] max-sm:min-h-11"
          value={draftDate}
          min={firstDay}
          max={lastDay}
          data-testid={`layer-time-slider-date-input-${layerId}`}
          onChange={(event) => handleDateInputChange(event.target.value)}
          // A partly typed day leaves the input empty; restore the committed day on the way out
          // so the field can never sit blank beside a layer that is drawing some date.
          onBlur={() => setDraftDate(selectedDate)}
        />

        {/* A WORD, not a colour and not an icon: this is the single guard against a mixed-time
            map being read as one moment, so it has to survive a monochrome screenshot and a
            screen reader alike. Only rendered when the layer is PROVABLY behind -- a layer whose
            newest day nobody knows reads as on-latest, because staleness we cannot measure must
            not be asserted. */}
        {isBehindLatestObservedDate && (
          <span
            data-testid={`layer-time-slider-behind-mark-${layerId}`}
            title={`Showing ${selectedDate}; this layer's latest published day is ${latestObservedDate}`}
            className="shrink-0 rounded-(--radius) bg-[hsl(var(--muted-foreground))]/20 px-1 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-[hsl(var(--foreground))]"
          >
            Not latest
          </span>
        )}

        <button
          type="button"
          onClick={() => resetLayerDate(layerId)}
          disabled={!canReturnToLatest}
          // Named for its layer, because a dozen of these share one screen and "Latest" alone
          // would announce a dozen identical buttons.
          aria-label={`Return ${label} to its latest published day`}
          title={
            !canReturnToLatest
              ? "Already following its latest published day"
              : latestObservedDate === null
                ? "Release this layer's pinned day; its latest published day is not known"
                : `Jump to ${latestObservedDate}`
          }
          className="shrink-0 rounded-(--radius) border border-[hsl(var(--border))] px-1.5 py-0.5 text-[10px] text-[hsl(var(--muted-foreground))] disabled:cursor-not-allowed disabled:opacity-50 max-sm:min-h-11 max-sm:px-3"
          data-testid={`layer-time-slider-latest-button-${layerId}`}
        >
          Latest
        </button>
      </div>

      {/* h-5 matches the 20px range hit area; max-sm:h-11 matches the 44px it grows to under the
          media query above, so the row still contains its own input on a phone.

          No overflow and no max-height anywhere in here: nested in the manager either would be
          the second-scrollbar defect layer-panel/panel-scroll.ts rule 2 names, and this drag
          control is exactly what a nested scroller makes unreachable on a touch screen. */}
      <div
        className="relative flex h-5 items-center max-sm:h-11"
        title={`${firstDay} to ${lastDay}`}
        data-testid={`layer-time-slider-track-${layerId}`}
      >
        {/* The dense base. Every covered day is dense until a band overlays it, so a dense run
            costs no element at all -- which is what keeps a four-year axis to a handful of divs. */}
        <div
          aria-hidden="true"
          data-testid={`layer-time-slider-dense-base-${layerId}`}
          className="absolute left-0 h-1.5 rounded-full"
          style={{
            width: `${percentOfDayOffset(domain, todayTickOffset)}%`,
            backgroundColor: TRACK_REGION_APPEARANCE.dense.backgroundColor,
          }}
        />

        {/* Painted before the bands so a hole at the live edge still draws over it. */}
        {todayTickOffset < maxOffset && (
          <div
            aria-hidden="true"
            data-testid={`layer-time-slider-future-band-${layerId}`}
            className="absolute right-0 h-1.5 rounded-r-full"
            style={{
              left: `${percentOfDayOffset(domain, todayTickOffset)}%`,
              backgroundColor: TRACK_REGION_APPEARANCE.future.backgroundColor,
              backgroundImage: TRACK_REGION_APPEARANCE.future.backgroundImage,
            }}
          />
        )}

        {/* `aria-hidden`, all of them, and that is only defensible because the two things they
            show are stated in words elsewhere: WHERE the runs are, in the slider's
            `aria-describedby` summary below, and WHAT the day under the thumb is, in its
            `aria-valuetext`. Exposing the bands themselves would put a dozen decorative regions
            per row in the tab order and still leave the topology unreadable without walking all
            of them. */}
        {bands.map((band) => (
          <div
            key={`${band.kind}-${band.fromDate}`}
            aria-hidden="true"
            data-testid={`layer-time-slider-band-${layerId}-${band.kind}`}
            data-from={band.fromDate}
            data-to={band.toDate}
            data-range-count={band.rangeCount}
            title={describeCoverageBand(band)}
            className="absolute h-1.5"
            style={{
              left: `${band.leftPercent}%`,
              width: `${band.widthPercent}%`,
              backgroundColor: TRACK_REGION_APPEARANCE[band.kind].backgroundColor,
              backgroundImage: TRACK_REGION_APPEARANCE[band.kind].backgroundImage,
            }}
          />
        ))}

        {/* Drawn only when it does not coincide with the today tick, so the common case of a
            layer publishing right up to today does not stack two marks on one pixel. */}
        {latestObservedDate !== null && latestObservedDate !== today && (
          <div
            aria-hidden="true"
            data-testid={`layer-time-slider-latest-tick-${layerId}`}
            className="absolute h-2.5 w-0.5 -translate-x-1/2 rounded-full bg-[hsl(var(--primary))]"
            style={{
              left: `${percentOfDayOffset(domain, dayOffset(firstDay, latestObservedDate))}%`,
            }}
          />
        )}

        <div
          aria-hidden="true"
          data-testid={`layer-time-slider-today-tick-${layerId}`}
          className="absolute h-2.5 w-px -translate-x-1/2 bg-[hsl(var(--foreground))]"
          style={{ left: `${percentOfDayOffset(domain, todayTickOffset)}%` }}
        />

        {/* A native range, so `role="slider"`, Arrow and Home/End stepping and the drag are the
            platform's rather than ours -- an explicit role and a hand-written arrow handler would
            both be redundant, and the handler would double-step in a browser that already moved
            the value. `step={1}` is what makes one arrow press one day; Page Up/Down is the one
            pair `handleCoarseStepKey` takes over, and it preventDefaults for exactly that reason.
            `aria-valuetext` replaces the day OFFSET the value really holds, which is meaningless
            spoken, and carries the coverage at that day so a screen-reader user learns "No data
            on this date" exactly where a sighted user sees the hatch. `aria-describedby` adds the
            two things the value alone cannot carry: the shape of the whole record, and whatever
            the note below the track is saying about this day. */}
        <input
          type="range"
          className="layer-time-slider-range absolute inset-x-0"
          min={0}
          max={maxOffset}
          step={1}
          value={selectedOffset}
          aria-label={`${label} date`}
          aria-valuetext={coverageNote === null ? selectedDate : `${selectedDate}, ${coverageNote}`}
          aria-describedby={
            notes.length > 0 ? `${coverageSummaryId} ${noteId}` : coverageSummaryId
          }
          data-testid={`layer-time-slider-range-${layerId}`}
          onChange={(event) => handleOffsetChange(Number(event.target.value))}
          onKeyDown={handleCoarseStepKey}
        />
      </div>

      {/* Unpainted and unmissable: the coverage topology and the two coarse keys, for the reader
          who has neither the bands nor a pointer. Without it the only way to find a three-day
          hole in a four-year record is to land on each of its 1,460 days in turn. */}
      <p
        className="sr-only"
        id={coverageSummaryId}
        data-testid={`layer-time-slider-summary-${layerId}`}
      >
        {coverageSummary} Page Up and Page Down move {COARSE_STEP_DAYS} days; hold Shift to move{" "}
        {VERY_COARSE_STEP_DAYS}.
      </p>

      {/* Joined the way LayerRow joins its own captions: one paragraph, so a row that is both on
          a hole and behind its latest costs one line of a 19rem column rather than two. Absent
          entirely when there is nothing true to say -- an empty caption slot on every row would
          be the vertical cost of a dozen sliders for no information. */}
      {notes.length > 0 && (
        <p
          id={noteId}
          data-testid={`layer-time-slider-note-${layerId}`}
          className="text-[10px] leading-relaxed text-[hsl(var(--muted-foreground))]"
        >
          {notes.join(" ")}
        </p>
      )}
    </div>
  );
}
