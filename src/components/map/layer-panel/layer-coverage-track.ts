/**
 * Geometry for one layer row's coverage track: which days of that layer's own axis the
 * warehouse covered, how densely, and where each run lands as a percentage of the drawn track.
 *
 * Split out of LayerTimeSlider.tsx because none of it touches the DOM, and because the three
 * coverage states are the one thing on that control that can make a false claim about the
 * warehouse. Keeping them here means they are asserted against the capabilities payload
 * directly, without rendering anything, and the component is left with nothing but layout.
 */

import {
  addDays,
  dayOffset,
  isCalendarDate,
  isDayDescribed,
  isWithinCoverageGap,
  sliderMaxOffset,
  todayOffset,
  type SliderDomain,
} from "@/stores/time-slider-store";
import type { DayRange, SliderLayerCapability } from "@/types/time-slider";

/**
 * What the record says about a run of days on ONE layer's axis.
 *
 * Four states and not two, because each pair the slider might flatten is a pair of different
 * facts:
 *
 * - `thin` against `absent`: "published thinly" is an observation and "not published" is a hole,
 *   so a track that drew a thin day as a hole would assert the warehouse recorded nothing on a
 *   day it recorded something. See `SliderLayerCapability.thinRanges`.
 * - `undescribed` against `dense`: below `SliderLayerCapability.describedFromDay` the server's
 *   range lists are TRUNCATED, so their silence about a day is not evidence the day was
 *   published. Painting those days dense is the exact defect the boundary was added to close --
 *   a gap dropped by the cap drew as solid coverage, `dayCoverageState` answered "dense",
 *   `describeDayCoverage` therefore said nothing, and the day read as an ordinary published one
 *   to a sighted reader and to a screen reader alike.
 */
export type CoverageKind = "dense" | "thin" | "absent" | "undescribed";

/** The kinds a band is actually drawn for: everything except the dense base. */
export type DrawnCoverageKind = Exclude<CoverageKind, "dense">;

/** One run of consecutive days sharing a coverage state, in day offsets from the axis start. */
export interface CoverageSegment {
  kind: CoverageKind;
  /** Day offset from `domain.firstDay` where the run begins. */
  startOffset: number;
  /** One past the run's last offset, so `endOffsetExclusive - startOffset` is its length in days. */
  endOffsetExclusive: number;
}

/** A non-dense run as it is actually painted: percentage geometry plus the days it stands for. */
export interface DrawnCoverageBand {
  kind: DrawnCoverageKind;
  leftPercent: number;
  widthPercent: number;
  /** First day of the earliest range this band draws. */
  fromDate: string;
  /** Last day of the latest range this band draws. */
  toDate: string;
  /**
   * How many separate ranges this band draws; above one when they were too close together to
   * be drawn apart. Read by `describeCoverageBand`, which must not describe three holes as one.
   */
  rangeCount: number;
}

/**
 * The narrowest band that is still visible, as a percentage of the track.
 *
 * A day is not a drawable unit here. The manager column is 19rem, and after the panel's own
 * `px-2`, the scroller's `pr-1` and the row's `pl-[3.5rem]` indent the track measures roughly
 * 224px; vegetation's axis is four years, so one day is 0.07% -- about a sixth of a pixel, which
 * is nothing. Left alone, a one-day hole in a four-year record would draw as solid coverage,
 * and the track would state the exact falsehood it exists to prevent.
 *
 * 0.9% is 2px at that measured width: the narrowest mark that survives a non-integer device
 * pixel ratio. A floored band therefore overdraws its neighbours by a fraction of a day. That
 * trade is deliberate and one-directional -- overstating a hole by hours is recoverable by
 * scrubbing onto the day and reading what the control says about it, whereas hiding the hole
 * entirely leaves nothing to scrub towards.
 */
export const MINIMUM_DRAWN_BAND_PERCENT = 0.9;

/** Position of a day offset along the track, in percent. Guards the single-day axis. */
export function percentOfDayOffset(domain: SliderDomain, offset: number): number {
  const maxOffset = sliderMaxOffset(domain);
  if (maxOffset === 0) return 0;
  return (offset / maxOffset) * 100;
}

/**
 * How many days of the axis the coverage lists can speak for.
 *
 * The axis is drawn past today -- `sliderDomain` pads it by `futureAxisDays` so the end of the
 * record is visible rather than sitting invisibly at the right edge -- but `coverageGaps` runs
 * only to `serverCurrentDate`, so coverage stops there too. Painting the padding as "absent"
 * would legend days that have not happened yet as a hole in a record, which they are not.
 */
function coveredDayCount(domain: SliderDomain): number {
  return Math.max(0, todayOffset(domain) + 1);
}

/** True when the day falls inside any of the closed ranges. Both ends inclusive, as `DayRange` states. */
function fallsWithinAnyRange(ranges: DayRange[] | undefined, date: string): boolean {
  // Guarded rather than trusted: a payload from a server predating the field carries no array,
  // and a missing list must read as "nothing known", never as a crash.
  return (ranges ?? []).some((range) => date >= range.from && date <= range.to);
}

/**
 * Stamps every day of a range with one coverage kind.
 *
 * Written as a sweep over an offset array rather than as interval arithmetic so that a
 * malformed payload needs no case analysis: a range reaching before the axis, past today, or
 * backwards (`from` after `to`) simply paints nothing, and overlapping ranges resolve by paint
 * order instead of by a rule nobody can check.
 */
function paintRanges(
  claims: CoverageKind[],
  firstDay: string,
  ranges: DayRange[] | undefined,
  kind: CoverageKind
): void {
  for (const range of ranges ?? []) {
    if (!isCalendarDate(range.from) || !isCalendarDate(range.to)) continue;
    const startOffset = Math.max(0, dayOffset(firstDay, range.from));
    const endOffset = Math.min(claims.length - 1, dayOffset(firstDay, range.to));
    for (let offset = startOffset; offset <= endOffset; offset += 1) {
      claims[offset] = kind;
    }
  }
}

/**
 * The axis partitioned into runs of one coverage state each, in ascending day order.
 *
 * Complete by construction: every day from `domain.firstDay` to `domain.today` belongs to
 * exactly one segment. That completeness is what lets the component paint one solid base bar
 * and overlay only the exceptions.
 *
 * The default is dense only where the payload's range lists reach. Below
 * `describedFromDay` the seed is `undescribed`, because there the lists were truncated and
 * their silence is an absence of evidence rather than evidence of coverage. Seeded per day
 * through the store's own `isDayDescribed` rather than from an offset computed here, so this
 * sweep and `dayCoverageState` can never disagree about where the boundary falls -- the
 * per-day loop runs only for a payload that actually reports one.
 *
 * Absent and thin are painted OVER the seed, in that order. A range that the server did
 * report below the boundary is a positive claim and outranks "we were not told", and thin
 * outranks absent so a day both lists claim resolves to thin. The two are disjoint per the
 * server contract, so that last case only fires on a self-contradictory payload -- and on
 * one, thin is the weaker claim and the one that degrades honestly: the reader is told the
 * day is sparse, scrubs onto it, and the row then reports whatever the query actually finds.
 * Resolving to absent instead would print "no data" over a day the same payload says was
 * published, and the control would contradict itself the moment anyone checked.
 */
export function buildCoverageSegments(
  domain: SliderDomain,
  layer: SliderLayerCapability
): CoverageSegment[] {
  const dayCount = coveredDayCount(domain);
  if (dayCount === 0) return [];

  const claims: CoverageKind[] = new Array<CoverageKind>(dayCount).fill("dense");
  if ((layer.describedFromDay ?? null) !== null) {
    for (let offset = 0; offset < dayCount; offset += 1) {
      if (isDayDescribed(layer, addDays(domain.firstDay, offset))) continue;
      claims[offset] = "undescribed";
    }
  }
  paintRanges(claims, domain.firstDay, layer.coverageGaps, "absent");
  paintRanges(claims, domain.firstDay, layer.thinRanges, "thin");

  const segments: CoverageSegment[] = [];
  let runStart = 0;
  for (let offset = 1; offset <= dayCount; offset += 1) {
    if (offset < dayCount && claims[offset] === claims[runStart]) continue;
    segments.push({
      kind: claims[runStart],
      startOffset: runStart,
      endOffsetExclusive: offset,
    });
    runStart = offset;
  }
  return segments;
}

/**
 * The non-dense runs as percentage bands, floored to a visible width and coalesced when that
 * flooring makes two of them touch.
 *
 * Coalescing is what bounds the element count. Without it a lane with hundreds of one-day holes
 * would emit hundreds of sub-pixel divs per row, on a panel that renders one of these per
 * switched-on layer; with it there can never be more than `100 / MINIMUM_DRAWN_BAND_PERCENT`
 * bands of either kind. A coalesced band keeps `rangeCount` above one precisely so its own
 * description can say "three ranges between X and Y" rather than claiming one continuous hole
 * across days that were published.
 */
export function drawCoverageBands(
  domain: SliderDomain,
  layer: SliderLayerCapability
): DrawnCoverageBand[] {
  const bands: DrawnCoverageBand[] = [];

  for (const segment of buildCoverageSegments(domain, layer)) {
    if (segment.kind === "dense") continue;

    const leftEdge = Math.min(100, Math.max(0, percentOfDayOffset(domain, segment.startOffset)));
    const rightEdge = Math.min(
      100,
      Math.max(0, percentOfDayOffset(domain, segment.endOffsetExclusive))
    );
    const widthPercent = Math.min(
      100,
      Math.max(rightEdge - leftEdge, MINIMUM_DRAWN_BAND_PERCENT)
    );
    // Pulled back off the right edge rather than clipped, so a hole on the last covered day is
    // drawn at full width instead of shrinking to nothing exactly where the record ends.
    const leftPercent = Math.max(0, Math.min(leftEdge, 100 - widthPercent));
    const fromDate = addDays(domain.firstDay, segment.startOffset);
    const toDate = addDays(domain.firstDay, segment.endOffsetExclusive - 1);

    const previous = bands[bands.length - 1];
    if (
      previous !== undefined &&
      previous.kind === segment.kind &&
      leftPercent <= previous.leftPercent + previous.widthPercent
    ) {
      previous.widthPercent = Math.min(
        100 - previous.leftPercent,
        leftPercent + widthPercent - previous.leftPercent
      );
      previous.toDate = toDate;
      previous.rangeCount += 1;
      continue;
    }

    bands.push({ kind: segment.kind, leftPercent, widthPercent, fromDate, toDate, rangeCount: 1 });
  }

  return bands;
}

/**
 * What the record says about ONE day, including the two cases that are not coverage at all.
 *
 * `beyond_record` and `off_axis` exist so the control never has to answer a question it cannot:
 * the padding right of today is time that has not happened, and a day outside this layer's own
 * axis is a day its capability row says nothing about. Neither is a hole in a record.
 */
export type DayCoverageState = CoverageKind | "beyond_record" | "off_axis";

/**
 * The coverage state at one day, agreeing with `buildCoverageSegments` day for day.
 *
 * The test order mirrors that function's paint order exactly, and the two are one decision that
 * must move together: thin over absent, both over undescribed, undescribed over dense. Reaching
 * `dense` therefore means the lists describe this day AND neither claimed it, which is the only
 * footing on which "published on this date" may be implied by saying nothing.
 */
export function dayCoverageState(
  domain: SliderDomain,
  layer: SliderLayerCapability,
  date: string
): DayCoverageState {
  if (!isCalendarDate(date)) return "off_axis";
  if (date < domain.firstDay || date > domain.lastDay) return "off_axis";
  if (date > domain.today) return "beyond_record";
  if (fallsWithinAnyRange(layer.thinRanges, date)) return "thin";
  // The store's own answer to this question, reused rather than reimplemented: a change to what
  // counts as a gap has to reach this track, and a second copy here is how it would not.
  if (isWithinCoverageGap(layer, date)) return "absent";
  // The same rule, and the reason the two silences are not interchangeable: below the boundary a
  // gap the cap dropped is indistinguishable from continuous coverage, so falling through to
  // `dense` here would let the control state a day was published on the strength of a list that
  // never reached it.
  if (!isDayDescribed(layer, date)) return "undescribed";
  return "dense";
}

/**
 * The coverage state in words, or null when there is nothing to say.
 *
 * Null on `dense` is deliberate symmetry with the drawing: a dense day carries no hatch either,
 * so announcing one would give a screen-reader user a running commentary a sighted user never
 * gets. Every other state is marked on the track and therefore has to be spoken.
 *
 * `undescribed` is phrased as a limit of the REPORT, not as a claim about the warehouse. The
 * warehouse may well have published that day; what we know is that the list we were sent stops
 * short of it, and that is the whole of what a reader may be told.
 */
export function describeDayCoverage(state: DayCoverageState): string | null {
  switch (state) {
    case "dense":
      return null;
    case "thin":
      return "Fewer readings than usual on this date";
    case "absent":
      return "No data on this date";
    case "undescribed":
      return "Coverage on this date is unknown; the record's gap list does not reach this far back";
    case "beyond_record":
      return "Beyond the record; nothing is published after today";
    case "off_axis":
      return "Outside this layer's record";
  }
}

/** The four regions a coverage track paints. `future` is the padding right of today, not coverage. */
export type TrackRegion = CoverageKind | "future";

/** One region's fill. `backgroundImage` is undefined only where the absence of texture is the mark. */
export interface TrackRegionAppearance {
  backgroundColor: string;
  backgroundImage: string | undefined;
}

/**
 * How the five regions are told apart: by HUE, by ALPHA and by STRIPE ANGLE at once.
 *
 * Three channels, because each one fails somewhere alone. Hue fails for a red-green-deficient
 * reader and on a monochrome display. Alpha fails against an unknown backdrop -- these bands sit
 * on the dock's translucent glass over live map tiles. Angle fails at the width a one-day band
 * is floored to, where the texture is only a few pixels of it. Carry all three and no single
 * failure collapses two states into one.
 *
 * - `dense`  solid, untextured, the layer's own hue at full strength. The absence of texture IS
 *   the mark: it is the ordinary case and the only one a reader needs no key for.
 * - `thin`   the same hue at a third strength, combed with VERTICAL bars. Hue because a thin day
 *   is an observation and must never read as a hole; strength because there is little of it;
 *   vertical because it is the one angle no other region uses.
 * - `absent` grey, hatched at 135 degrees.
 * - `future` grey, hatched at 45 degrees -- the mirror of absent, and the treatment the retired
 *   global slider drew right of today.
 * - `undescribed` the faintest region of all, hatched HORIZONTALLY at 0 degrees. Faintest
 *   because it is the only region that asserts nothing whatever -- neither a reading nor its
 *   absence -- and a region that states less must not shout louder than the ones that state
 *   something. Horizontal because 0 is the fourth of the four right-angle-separated angles, so
 *   the texture alone still separates it from `thin` (90), `future` (45) and `absent` (135).
 *
 * Absent and future share a colour deliberately: both say "nothing published", which is one
 * fact, and a second colour for it would imply a distinction the record does not make. What
 * separates them is the mirrored hatch and the today tick drawn between them -- a hole in a
 * record we collected, against time that has not happened yet. `undescribed` takes the same
 * neutral hue at half their alpha rather than a hue of its own, because inventing a colour for
 * "we were not told" would put a missing REPORT on the same footing as a measured record.
 *
 * Grey rather than a warning colour for all three: none is a fault. A gap is a statement about
 * coverage, and `--destructive` would make every ingest lane's ordinary history look like an
 * error the reader is expected to fix. `--accent` is not an option for any of them either --
 * it resolves to exactly `--primary` in both themes, so an accent region would be invisible as
 * a distinction from the dense fill.
 */
export const TRACK_REGION_APPEARANCE: Record<TrackRegion, TrackRegionAppearance> = {
  dense: {
    backgroundColor: "hsl(var(--primary))",
    backgroundImage: undefined,
  },
  thin: {
    backgroundColor: "hsl(var(--primary) / 0.3)",
    backgroundImage:
      "repeating-linear-gradient(90deg, hsl(var(--primary) / 0.75) 0 1px, transparent 1px 4px)",
  },
  absent: {
    backgroundColor: "hsl(var(--muted-foreground) / 0.35)",
    backgroundImage:
      "repeating-linear-gradient(135deg, hsl(var(--muted-foreground) / 0.6) 0 3px, transparent 3px 6px)",
  },
  undescribed: {
    backgroundColor: "hsl(var(--muted-foreground) / 0.15)",
    backgroundImage:
      "repeating-linear-gradient(0deg, hsl(var(--muted-foreground) / 0.3) 0 1px, transparent 1px 5px)",
  },
  future: {
    backgroundColor: "hsl(var(--muted-foreground) / 0.35)",
    backgroundImage:
      "repeating-linear-gradient(45deg, hsl(var(--muted-foreground) / 0.55) 0 3px, transparent 3px 6px)",
  },
};

/** What a band of each drawn kind is called, in the tooltip and in the spoken summary. */
const DRAWN_COVERAGE_SUBJECT: Record<DrawnCoverageKind, string> = {
  absent: "No data",
  thin: "Fewer readings than usual",
  undescribed: "Coverage unknown",
};

/** One band in words, for its tooltip. Never describes several ranges as one continuous run. */
export function describeCoverageBand(band: DrawnCoverageBand): string {
  const subject = DRAWN_COVERAGE_SUBJECT[band.kind];
  if (band.rangeCount > 1) {
    return `${subject}: ${band.rangeCount} ranges between ${band.fromDate} and ${band.toDate}`;
  }
  const extent =
    band.fromDate === band.toDate ? band.fromDate : `${band.fromDate} to ${band.toDate}`;
  return `${subject}: ${extent}`;
}

/**
 * How many runs of one kind the spoken summary names before it starts counting them instead.
 *
 * Three, because the summary is read out on every focus of the slider and its job is to tell a
 * reader WHERE to scrub, not to recite the record. A lane with forty one-day holes would take
 * minutes to speak in full and leave nobody able to act on it; three landmarks plus a count is
 * enough to aim at, and the day the thumb lands on then reports itself exactly through
 * `aria-valuetext`.
 */
export const MAX_SPOKEN_COVERAGE_RUNS = 3;

/** One run of days in the words the summary uses. A one-day run is named once, not twice. */
function describeRun(run: { from: string; to: string }): string {
  return run.from === run.to ? run.from : `${run.from} to ${run.to}`;
}

/** Up to `MAX_SPOKEN_COVERAGE_RUNS` runs named, then the rest counted. */
function listRuns(runs: { from: string; to: string }[]): string {
  const named = runs.slice(0, MAX_SPOKEN_COVERAGE_RUNS).map(describeRun);
  const remaining = runs.length - named.length;
  return remaining > 0 ? `${named.join("; ")}, and ${remaining} more` : named.join("; ");
}

/**
 * The whole axis's coverage topology in one spoken paragraph.
 *
 * A screen-reader user could otherwise learn WHERE the holes are only by landing on each one:
 * the bands are decorative, and the day under the thumb is all `aria-valuetext` can report. On
 * vegetation's four-year axis that is up to 1,460 arrow presses to discover a three-day gap.
 * This is what the slider's `aria-describedby` points at, so the shape of the record is
 * available before any scrubbing at all.
 *
 * Built from `buildCoverageSegments` rather than from `coverageGaps`/`thinRanges` directly, so
 * the spoken account names exactly the runs the track paints -- clipped to the axis, merged
 * where the payload overlapped, and carrying the undescribed span the raw lists cannot express.
 */
export function describeCoverageTopology(
  domain: SliderDomain,
  layer: SliderLayerCapability
): string {
  const segments = buildCoverageSegments(domain, layer);
  if (segments.length === 0) return "This layer's axis covers no days yet.";

  const runsOfKind = (kind: CoverageKind) =>
    segments
      .filter((segment) => segment.kind === kind)
      .map((segment) => ({
        from: addDays(domain.firstDay, segment.startOffset),
        to: addDays(domain.firstDay, segment.endOffsetExclusive - 1),
      }));

  const undescribed = runsOfKind("undescribed");
  const absent = runsOfKind("absent");
  const thin = runsOfKind("thin");
  const lastCoveredDay = addDays(
    domain.firstDay,
    segments[segments.length - 1].endOffsetExclusive - 1
  );

  const spoken = [`Coverage from ${domain.firstDay} to ${lastCoveredDay}.`];
  if (undescribed.length > 0) {
    // Named as the boundary rather than as a run, because that is the fact a reader can use:
    // everything below it is a region the report simply does not reach.
    spoken.push(`Not described before ${addDays(undescribed[undescribed.length - 1].to, 1)}.`);
  }
  if (absent.length === 0 && thin.length === 0) {
    spoken.push(
      undescribed.length > 0
        ? "No gaps and no sparse days after that."
        : "No gaps and no sparse days."
    );
    return spoken.join(" ");
  }
  if (absent.length > 0) {
    const noun = absent.length === 1 ? "gap" : "gaps";
    spoken.push(`${absent.length} ${noun} with no data: ${listRuns(absent)}.`);
  }
  if (thin.length > 0) {
    const noun = thin.length === 1 ? "range" : "ranges";
    spoken.push(`${thin.length} sparse ${noun}: ${listRuns(thin)}.`);
  }
  return spoken.join(" ");
}
