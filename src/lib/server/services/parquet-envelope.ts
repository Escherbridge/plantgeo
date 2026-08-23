/**
 * The four states one stream-day of the Parquet warehouse can be in, as one discriminated union.
 *
 * The warehouse already distinguishes these four; the UI cannot express them yet, and this module
 * is where the distinction ENTERS the TypeScript side. `partition_day_statuses`
 * (services/agri-data-service/.../foundation/parquet/paths.py) classifies every day of a stream at
 * one tier from the object listing alone as `data` | `absent` | `conflict` | `missing`, and
 * `GovernedAbsence` (foundation/parquet/absence.py) is the evidence an `absent` day must carry:
 * reason, the upstream response that refused it, when it was recorded, and the run that recorded
 * it. 358 such markers exist today.
 *
 * COLLAPSING ANY TWO OF THESE IS THE BUG THIS TYPE EXISTS TO PREVENT. "The lane looked and the
 * source deliberately had nothing" and "nobody ever wrote this day" render identically -- an empty
 * map -- and mean opposite things: the first licenses the sentence "there was none here on that
 * day", the second forbids it. `MetricAtDateAvailability` (src/types/time-slider.ts) carries the
 * same discipline for the Postgres read model and has no `governed_absence` member, because
 * `geo.features` has no way to record one; the warehouse does, so this union adds it. Nothing here
 * is a transport state: a request that did not complete is a THROWN fault (see
 * `parquet-plane-client.ts`), never an envelope, for the reason `request_failed`'s comment in
 * time-slider.ts gives -- a blip must never become a positive claim that the warehouse published
 * nothing.
 *
 * NOT A MEMBER, deliberately: `conflict` -- a day holding both a part file and an absence marker,
 * which paths.py says only a manual admin action should ever produce. The server resolves it before
 * answering rather than handing the contradiction to a map that has no way to draw it. When that
 * resolution is decided it becomes a fifth member here, and `assertExhaustiveParquetPlaneState`
 * below is what makes that a compile error at every call site rather than a silent fallthrough.
 *
 * Every day on every member is an opaque `YYYY-MM-DD` string that is never parsed, formatted or
 * converted here. See `PUBLISHER_NAMED_DAY_RULE` in `environmental-read-model.ts`: 37.5% of the
 * stored water-gauge rows carry a `-07:00` offset, and one instant-based conversion moves 6,279 of
 * 16,743 of them onto the following day. This module owns no day semantics whatsoever.
 */

/**
 * The four states, in the order a reader should think about them: answered, deliberately empty,
 * never written for this day, never written at all.
 *
 * A tuple rather than a bare union so a consumer that must enumerate the states (a caption table,
 * a `Record<ParquetPlaneState, T>`) reads them from one place instead of hand-typing four literals.
 */
export const PARQUET_PLANE_STATES = [
  "published",
  "governed_absence",
  "day_not_written",
  "lane_never_written",
] as const;

/** One of the four states above. */
export type ParquetPlaneState = (typeof PARQUET_PLANE_STATES)[number];

/**
 * One warehouse row, as it arrives before a caller narrows it.
 *
 * Untyped by design at this layer: the warehouse has a per-kind schema per layer
 * (`warehouse/parquet/schema.py`), so one concrete row interface here would be a lie about eleven
 * of the twelve streams. A caller that knows its layer parameterizes `ParquetPlaneEnvelope<TRow>`.
 */
export type ParquetPlaneRow = Readonly<Record<string, unknown>>;

/**
 * Why a day is deliberately empty, as `GovernedAbsence` recorded it (absence.py).
 *
 * All four fields are mandatory upstream and non-blank: an absence without evidence is
 * indistinguishable from a silent ingest failure, which is the exact confusion the marker exists to
 * end. `recordedAt` is an opaque ISO-8601 instant -- it is provenance for a human reader, never an
 * input to a day calculation.
 */
export interface GovernedAbsenceEvidence {
  /** The lane's own words for why the day is empty. */
  reason: string;
  /** What the upstream actually answered, quoted rather than summarised. */
  upstreamResponse: string;
  /** ISO-8601 instant the marker was written. Opaque; never converted to a day. */
  recordedAt: string;
  /** The ingest run that recorded the absence, so the decision can be traced back. */
  runId: string;
}

/** The warehouse answered with rows for the day it served. */
export interface PublishedParquetPlane<TRow = ParquetPlaneRow> {
  state: "published";
  /** The `YYYY-MM-DD` the caller asked for, echoed back verbatim. */
  requestedDay: string;
  /**
   * The partition day the rows actually came from, `YYYY-MM-DD`.
   *
   * Equal to `requestedDay` for a day read. For the newest-release-at-or-before read it is the
   * release's OWN day, which is the whole point of that endpoint: a weekly release carried forward
   * must be reported at its own date, never dressed up as fresher than it is (the same rule
   * `PublishedDroughtCollection.carryForwardDays` enforces for USDM).
   */
  servedDay: string;
  rows: readonly TRow[];
  /** The serving row budget bit, so these rows are a subset the caller must not read as the whole day. */
  truncated: boolean;
}

/** The lane looked at this day and the source deliberately had nothing to give. */
export interface GovernedAbsenceParquetPlane {
  state: "governed_absence";
  /** The `YYYY-MM-DD` the caller asked for, echoed back verbatim. */
  requestedDay: string;
  /**
   * The partition day the marker itself sits on, `YYYY-MM-DD`. Distinct from `requestedDay` for a
   * release read, where captioning a marker from an earlier release as "nothing on the day you
   * asked for" would state an absence for a day nothing was ever claimed about.
   */
  servedDay: string;
  evidence: GovernedAbsenceEvidence;
}

/**
 * This day holds neither a part file nor a marker: a real gap, and nothing may be drawn or said
 * about what was there. `missing` in `partition_day_statuses`.
 */
export interface DayNotWrittenParquetPlane {
  state: "day_not_written";
  /** The `YYYY-MM-DD` the caller asked for, echoed back verbatim. */
  requestedDay: string;
}

/**
 * The lane has never written anything at all, on any day.
 *
 * Separate from `day_not_written` because the two license different sentences: one day being absent
 * from a live stream is a gap in a record that exists, while a lane with no objects at all has no
 * record to have a gap in -- the honest caption is "this layer has not been drained yet", and a
 * slider must not mount an axis over it.
 */
export interface LaneNeverWrittenParquetPlane {
  state: "lane_never_written";
  /** The `YYYY-MM-DD` the caller asked for, echoed back verbatim. */
  requestedDay: string;
}

/**
 * One stream-day, in exactly one of the four states.
 *
 * Discriminated on `state`, so a `switch` narrows each arm to its own fields and a caller cannot
 * reach `rows` without having proved the day was published.
 */
export type ParquetPlaneEnvelope<TRow = ParquetPlaneRow> =
  | PublishedParquetPlane<TRow>
  | GovernedAbsenceParquetPlane
  | DayNotWrittenParquetPlane
  | LaneNeverWrittenParquetPlane;

/** A state reached a `switch` that has no arm for it -- a five-member union read by four-member code. */
export class UnhandledParquetPlaneStateError extends Error {}

/**
 * The `default:` arm every `switch` over `ParquetPlaneEnvelope` must end with.
 *
 * Its parameter is `never`, so it compiles only while the four arms above it are exhaustive: adding
 * a fifth member to the union turns every call site that omits its arm into a compile error, which
 * is the whole reason the four states are modelled as a union rather than as an optional field. It
 * throws rather than returning a fallback, because a fallback would be a fifth rendering nobody
 * chose.
 */
export function assertExhaustiveParquetPlaneState(unhandled: never): never {
  const state = (unhandled as { state?: unknown }).state;
  throw new UnhandledParquetPlaneStateError(
    `Unhandled Parquet plane state: ${typeof state === "string" ? state : String(unhandled)}`
  );
}
