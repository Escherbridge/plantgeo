/**
 * The one vocabulary every layer row states its time control in. Rationale and the full
 * decision record: src/components/map/AGENTS.md §layer-time-state.
 */

import {
  findLayerCapability,
  isCalendarDate,
  sliderDomain,
  type SliderDomain,
} from "@/stores/time-slider-store";
import type { SliderCapabilities, SliderLayerCapability } from "@/types/time-slider";

/**
 * Every `withheld_reason` the capability serving side can emit, spelled exactly as it rides on
 * the wire.
 *
 * A COPY of `WithheldParquetCapabilityReason` in
 * `src/lib/server/services/parquet-slider-capabilities.ts`, and deliberately not an import:
 * `scripts/check-client-server-imports.mjs` refuses every `@/lib/server/**` edge from
 * `src/components/`, type-only ones included, outside a one-entry allowlist -- and that module
 * reaches `environmental-read-model.ts` and its `db` handle. The copy is held honest by
 * `src/__tests__/components/layer-time-state.test.ts`, which the same checker exempts and which
 * imports the server enum type-only (erased at runtime, so nothing server-side is pulled into a
 * browser bundle) to fail compilation the moment the two lists diverge.
 */
export const LAYER_WITHHOLDING_REASONS = [
  "coverage_unavailable",
  "coverage_not_current",
  "reader_not_parquet",
  "lane_not_registered",
  "lane_never_written",
  "rung_not_reported",
  "rung_never_written",
  "lane_nature_mismatch",
  "invalid_rung_bounds",
  "no_common_readable_history",
  "availability_unpublished",
  "availability_stale",
  "availability_malformed",
  "availability_checksum_invalid",
  "ceiling_violation",
] as const;

/** One reason the server named for holding a layer's axis back. */
export type LayerWithholdingReason = (typeof LAYER_WITHHOLDING_REASONS)[number];

/**
 * One withheld row of the capability payload, as this directory reads it.
 *
 * Structural rather than imported, and READ DEFENSIVELY, because `SliderCapabilities` in
 * `src/types/time-slider.ts` does not declare `withheldParquetCapabilities` even though
 * `getSliderCapabilities` has sent it since the Parquet cutover: the tRPC procedure returns the
 * wider `ParquetSliderCapabilities`, and `setCapabilities` narrows it to `SliderCapabilities` on
 * the way into the store, which erases the field from the type while leaving every byte of it on
 * the object. Reading it here is reading data the server really sends -- not a client-side guess
 * -- but the compiler cannot vouch for it, so `readWithheldCapabilities` validates every entry
 * and an unrecognised shape becomes "no reason stated" rather than a fabricated one.
 */
export interface WithheldLayerCapability {
  /** The `geo.layers.name` / stream name this withholding is about. */
  layerName: string;
  reason: LayerWithholdingReason;
  /** The physical Parquet lanes behind it, for the operator hover. */
  parquetLanes: string[];
}

const WITHHOLDING_REASON_SET: ReadonlySet<string> = new Set(LAYER_WITHHOLDING_REASONS);

/** True for a wire string this client knows how to word. */
export function isLayerWithholdingReason(value: unknown): value is LayerWithholdingReason {
  return typeof value === "string" && WITHHOLDING_REASON_SET.has(value);
}

/**
 * The withheld rows the payload carries, or an empty list when it carries none this client can
 * read.
 *
 * A reason spelled in a way this build does not know is DROPPED rather than surfaced raw: a chip
 * reading `rung_bounds_v2` teaches a user nothing and looks like a crash. The layer then falls
 * through to the generic "not published to the record yet" sentence, which is weaker but never
 * wrong -- the axis really is absent -- and the raw string is still in the payload for anyone
 * reading the network tab.
 */
export function readWithheldCapabilities(
  capabilities: SliderCapabilities | null
): WithheldLayerCapability[] {
  if (capabilities === null) return [];
  const raw = (capabilities as { withheldParquetCapabilities?: unknown })
    .withheldParquetCapabilities;
  if (!Array.isArray(raw)) return [];
  const withheld: WithheldLayerCapability[] = [];
  for (const entry of raw) {
    if (typeof entry !== "object" || entry === null) continue;
    const { layerName, reason, parquetLanes } = entry as Record<string, unknown>;
    if (typeof layerName !== "string" || !isLayerWithholdingReason(reason)) continue;
    withheld.push({
      layerName,
      reason,
      parquetLanes: Array.isArray(parquetLanes)
        ? parquetLanes.filter((lane): lane is string => typeof lane === "string")
        : [],
    });
  }
  return withheld;
}

/** The withheld row for one warehouse stream, or null when the server withheld nothing for it. */
export function findWithheldCapability(
  capabilities: SliderCapabilities | null,
  warehouseLayerName: string | null
): WithheldLayerCapability | null {
  if (warehouseLayerName === null) return null;
  return (
    readWithheldCapabilities(capabilities).find(
      (entry) => entry.layerName === warehouseLayerName
    ) ?? null
  );
}

/**
 * True when the whole Parquet coverage read failed, so EVERY Parquet-owned row in this payload is
 * withheld for one shared reason rather than for anything about itself.
 *
 * Read structurally for the same reason `withheldParquetCapabilities` is; the loader already
 * reads it off the query result to pick its retry clock
 * (`TimeSliderCapabilitiesLoader.tsx`), where the tRPC output type still has it.
 */
export function isParquetCoverageUnavailable(capabilities: SliderCapabilities | null): boolean {
  if (capabilities === null) return false;
  return (capabilities as { parquetCoverageUnavailable?: unknown }).parquetCoverageUnavailable
    === true;
}

/**
 * The six states a layer's time control can be in, and the complete set of them.
 *
 * The owner's ask was that every layer present identically. That is a statement about SHAPE, not
 * about words: one component, one chip, one sentence, one optional date, in that order, on every
 * row -- while the words differ per cause, because a snapshot and a lane that has never written a
 * byte are not the same fact and a UI that said one thing about both would be wrong about one of
 * them.
 *
 * - `loading`  -- nothing has arrived yet and something is on its way. A cold
 *   `getSliderCapabilities` measured 7.6-8.5s against production; a warm one is 0.28s.
 * - `ready`    -- there is a scrubbable axis. The only state that draws a track.
 * - `empty`    -- the layer IS described and still offers no selectable day.
 * - `withheld` -- the server named a specific reason it is not offering this axis.
 * - `no_time_axis` -- a snapshot. It has no time axis and never will; not an error, not an empty.
 * - `error`    -- the read failed. OUR failure, never a claim about the record.
 */
export type LayerTimeStateKind =
  | "loading"
  | "ready"
  | "empty"
  | "withheld"
  | "no_time_axis"
  | "error";

/** One layer's time control, as every row states it. */
export interface LayerTimeState {
  kind: LayerTimeStateKind;
  /**
   * The chip. Two words at most so it occupies the same slot on every row, and a NOUN PHRASE
   * about the layer rather than a verb about us -- "Indexing", not "Please wait".
   */
  badge: string;
  /** One sentence naming the cause, in the terms a reader of the map has. */
  detail: string;
  /**
   * True while this state is expected to resolve on its own, which is exactly the set of states
   * that earn a pulsing chip: a first load in flight, and a read the loader is already retrying
   * on its 30s clock. A withheld index nobody is rebuilding does not pulse, because nothing about
   * it is about to change.
   */
  isSettling: boolean;
  /** The server's own machine reason, for the operator hover; null when it named none. */
  reason: LayerWithholdingReason | null;
  /** The physical lanes the server named alongside that reason; empty when it named none. */
  evidenceLanes: string[];
}

/** The two visible tokens one withheld reason gets, plus whether anything is retrying it. */
export interface ReasonWording {
  badge: string;
  detail: string;
  /** Something is already working on this one; see `LayerTimeState.isSettling`. */
  isSettling?: true;
}

/**
 * One sentence per withheld reason, and every one of them different.
 *
 * The distinction the owner asked for explicitly is `availability_unpublished` versus
 * `lane_never_written`, and it is the widest gap in the list: the first layer HAS its data and is
 * waiting on an index build that is running, the second has never had a single byte written for
 * it. Captioning both "unavailable" tells a user to come back tomorrow for one thing that will
 * arrive and one that never will.
 *
 * Wording rules these all follow: name the thing that is missing, say whether waiting helps, and
 * never imply the map is broken. A withheld layer is not an error and must not read like one.
 */
const WITHHOLDING_WORDING: Record<LayerWithholdingReason, ReasonWording> = {
  availability_unpublished: {
    badge: "Indexing",
    detail:
      "The data is written; its day index is still being built, so no dates can be offered yet.",
    isSettling: true,
  },
  availability_stale: {
    badge: "Index behind",
    detail:
      "Its day index is older than the data it describes, so its dates are not offered until it is rebuilt.",
  },
  availability_malformed: {
    badge: "Index unreadable",
    detail: "Its day index could not be read, so no dates are taken from it.",
  },
  availability_checksum_invalid: {
    badge: "Index unverified",
    detail: "Its day index failed its own checksum, so no dates are taken from it.",
  },
  lane_never_written: {
    badge: "Never published",
    detail: "This source has never published anything, so there is no record to scrub.",
  },
  lane_not_registered: {
    badge: "No source",
    detail: "No warehouse lane is registered for this layer, so nothing describes its days.",
  },
  coverage_unavailable: {
    badge: "Retrying",
    detail: "The day census could not be read just now, so no dates are offered yet. Retrying.",
    isSettling: true,
  },
  coverage_not_current: {
    badge: "Census behind",
    detail:
      "The day census has not caught up to today, so its dates are held back rather than offered short.",
  },
  reader_not_parquet: {
    badge: "Other source",
    detail: "This layer is drawn from a store the day census does not describe, so it has no axis here.",
  },
  rung_not_reported: {
    badge: "Zooms missing",
    detail:
      "Not every zoom level this layer publishes has reported its days, so an axis would be wrong at some scales.",
  },
  rung_never_written: {
    badge: "Zooms missing",
    detail: "A zoom level this layer publishes has never been written, so its dates are held back.",
  },
  lane_nature_mismatch: {
    badge: "Kind mismatch",
    detail:
      "The source behind this layer is not the kind of series its axis was declared as, so no axis is offered.",
  },
  invalid_rung_bounds: {
    badge: "Bounds invalid",
    detail: "The day bounds its zoom levels report do not agree, so none of them is offered.",
  },
  no_common_readable_history: {
    badge: "No shared days",
    detail:
      "Its zoom levels share no day all of them can read, so no single axis covers the whole layer.",
  },
  ceiling_violation: {
    badge: "Past its source",
    detail:
      "It reports days newer than its own source can offer, so the axis is refused rather than quietly trimmed.",
  },
};

/** The sentence one withheld reason gets, for callers that already hold the reason. */
export function describeWithholdingReason(reason: LayerWithholdingReason): ReasonWording {
  return WITHHOLDING_WORDING[reason];
}

const LOADING_STATE: LayerTimeState = {
  kind: "loading",
  badge: "Loading",
  // Says the WAIT is expected, which is the whole complaint: a cold read of the day census takes
  // several seconds and used to render as an empty row indistinguishable from a layer that has
  // no dates at all.
  detail: "Reading which days this layer has published. The first read can take a few seconds.",
  isSettling: true,
  reason: null,
  evidenceLanes: [],
};

const READY_STATE: LayerTimeState = {
  kind: "ready",
  badge: "Ready",
  detail: "This layer has a scrubbable range of days.",
  isSettling: false,
  reason: null,
  evidenceLanes: [],
};

/**
 * The wording kept verbatim from the branch this replaces, because
 * `LayerRow.test.tsx` pins the phrase "not a gap in the record" and the phrase is the point: a
 * failed fetch stated as an absence of data is the inversion this whole control exists to refuse.
 */
const LOAD_FAILED_STATE: LayerTimeState = {
  kind: "error",
  badge: "Unavailable",
  detail: "Dates could not be loaded. This is a loading failure, not a gap in the record.",
  isSettling: true,
  reason: null,
  evidenceLanes: [],
};

/**
 * The short-payload state: the server answered without its stream scan, so this layer's absence
 * from the list is OUR failure and not the record's. Wording kept from the branch it replaces --
 * `LayerRow.test.tsx` pins "could not be read".
 */
const STREAMS_UNAVAILABLE_STATE: LayerTimeState = {
  kind: "error",
  badge: "Retrying",
  detail: "Its history could not be read just now, so no range can be drawn yet. Retrying.",
  isSettling: true,
  reason: null,
  evidenceLanes: [],
};

const UNBACKED_STATE: LayerTimeState = {
  kind: "no_time_axis",
  badge: "No time axis",
  detail: "No warehouse layer backs this one, so it has no record of its own to scrub.",
  isSettling: false,
  reason: null,
  evidenceLanes: [],
};

const SNAPSHOT_STATE: LayerTimeState = {
  kind: "no_time_axis",
  badge: "No time axis",
  // A COMPLETE record that does not vary by day, which is why this is neither an empty nor an
  // error: watersheds' 9,396 basins share one 2013 WBD loaddate, and a track over them would
  // advertise years of scrubbing across a boundary set that draws identically on every day of it.
  detail: "A snapshot, not a daily record: it draws the same on every date.",
  isSettling: false,
  reason: null,
  evidenceLanes: [],
};

const NOT_PUBLISHED_STATE: LayerTimeState = {
  kind: "empty",
  badge: "No dates",
  detail: "Not published to the warehouse record yet, so it has no dates of its own.",
  isSettling: false,
  reason: null,
  evidenceLanes: [],
};

const NOTHING_OBSERVED_STATE: LayerTimeState = {
  kind: "empty",
  badge: "No dates",
  detail: "Nothing observed yet, so there is no range to scrub.",
  isSettling: false,
  reason: null,
  evidenceLanes: [],
};

/** Everything one row needs to answer "what is this layer's time control doing?". */
export interface LayerTimeStateInput {
  /** The `geo.layers.name` / stream name behind the toggle; null when nothing backs it. */
  warehouseLayerName: string | null;
  capabilities: SliderCapabilities | null;
  /** `TimeSliderState.capabilitiesUnavailable`: the fetch has NEVER once succeeded. */
  capabilitiesUnavailable: boolean;
}

/**
 * Which of the six states one layer is in, decided in one place for every row.
 *
 * Reads pending straight off the store pair, which is the only signal there is and is exactly
 * sound: `TimeSliderCapabilitiesLoader` is mounted in `MapView` and never unmounts, its query is
 * never disabled, and it is the sole writer of both fields -- so `capabilities === null` with
 * `capabilitiesUnavailable === false` means one thing only, "the first fetch is still in flight".
 * That pair is the contract; see the loader's own header before changing either field's writer.
 *
 * Order matters and is not arbitrary. Transport is asked FIRST, because a failed or in-flight
 * read tells us nothing about the warehouse and every claim below it would be a fact invented out
 * of a missing payload. A named withholding is asked before the generic "not published", because
 * absence from `layers` is the same absence in both cases and only the withheld list can tell
 * them apart.
 */
export function resolveLayerTimeState({
  warehouseLayerName,
  capabilities,
  capabilitiesUnavailable,
}: LayerTimeStateInput): LayerTimeState {
  if (warehouseLayerName === null) return UNBACKED_STATE;
  if (capabilities === null) {
    return capabilitiesUnavailable ? LOAD_FAILED_STATE : LOADING_STATE;
  }

  const capability = findLayerCapability(capabilities, warehouseLayerName);
  if (capability === null) {
    const withheld = findWithheldCapability(capabilities, warehouseLayerName);
    if (withheld !== null) {
      const wording = WITHHOLDING_WORDING[withheld.reason];
      return {
        kind: "withheld",
        badge: wording.badge,
        detail: wording.detail,
        isSettling: wording.isSettling === true,
        reason: withheld.reason,
        evidenceLanes: withheld.parquetLanes,
      };
    }
    // No named reason. A short payload is our failure; anything else is an honest absence.
    return capabilities.streamsUnavailable ? STREAMS_UNAVAILABLE_STATE : NOT_PUBLISHED_STATE;
  }

  if (capability.temporalKind === "snapshot") return SNAPSHOT_STATE;
  if (sliderDomain(capabilities, warehouseLayerName) !== null) return READY_STATE;
  return describeUnusableAxis(capability, capabilities.serverCurrentDate);
}

/**
 * Why a described layer still has no axis. Reached only once `sliderDomain` has refused a
 * capability that is neither missing nor a snapshot, which leaves exactly two causes -- and both
 * are `empty`, because in both the record itself is what has nothing to offer.
 */
function describeUnusableAxis(
  capability: SliderLayerCapability,
  serverCurrentDate: string
): LayerTimeState {
  const { earliestObservedDate } = capability;
  if (earliestObservedDate === null) return NOTHING_OBSERVED_STATE;
  // Asked BEFORE the after-today comparison, which is a string compare and would answer this case
  // wrongly rather than not at all: "not-a-day" > "2026-09-07" is true, so a malformed value
  // would be reported as a record that starts in the future. Stating the malformed value beats a
  // sentence that implies the record is empty when it is the payload that is wrong.
  if (!isCalendarDate(earliestObservedDate)) {
    return {
      ...NOTHING_OBSERVED_STATE,
      badge: "Dates unreadable",
      detail: `Its record starts on "${earliestObservedDate}", which is not a date, so no range can be drawn.`,
    };
  }
  if (earliestObservedDate > serverCurrentDate) {
    return {
      ...NOTHING_OBSERVED_STATE,
      detail: `Its record starts on ${earliestObservedDate}, after today, so no range can be drawn.`,
    };
  }
  // Neither malformed, nor in the future, nor absent, and `sliderDomain` still refused: nothing
  // in the payload explains it, so the sentence claims nothing about the record beyond the fact.
  return {
    ...NOTHING_OBSERVED_STATE,
    detail: "No range can be drawn from this layer's record.",
  };
}

/**
 * The axis for a layer in the `ready` state, or null for every other state.
 *
 * A convenience for the one caller that needs both the state and the domain and must not let them
 * disagree: asking `resolveLayerTimeState` and `sliderDomain` separately is two chances to get a
 * different answer from one payload.
 */
export function readyLayerDomain(
  state: LayerTimeState,
  capabilities: SliderCapabilities | null,
  warehouseLayerName: string | null
): SliderDomain | null {
  if (state.kind !== "ready" || warehouseLayerName === null) return null;
  return sliderDomain(capabilities, warehouseLayerName);
}
