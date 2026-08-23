import { describe, expect, it } from "vitest";

import {
  assertExhaustiveParquetPlaneState,
  PARQUET_PLANE_STATES,
  UnhandledParquetPlaneStateError,
  type ParquetPlaneEnvelope,
  type ParquetPlaneState,
} from "@/lib/server/services/parquet-envelope";

/**
 * The four states must stay four DISTINCT things all the way to a call site.
 *
 * These assertions are half the point; the other half is that this file compiles at all. The
 * exhaustive `switch` below declares a return type and ends in `assertExhaustiveParquetPlaneState`,
 * whose parameter is `never` -- so a fifth member added to `ParquetPlaneEnvelope` breaks `tsc` here
 * (and at every other call site written this way) instead of silently falling through to a caption
 * nobody chose. `STATE_CAPTIONS` is the second, weaker guard: a total `Record<ParquetPlaneState, T>`
 * cannot be written without an entry for the new member.
 */

const STATE_CAPTIONS: Record<ParquetPlaneState, string> = {
  published: "rows",
  governed_absence: "deliberately empty",
  day_not_written: "never written",
  lane_never_written: "lane never drained",
};

/** What a consumer switching on the union looks like, with each arm reading its OWN fields. */
function describeEnvelope(envelope: ParquetPlaneEnvelope): string {
  switch (envelope.state) {
    case "published":
      return `${envelope.rows.length} rows from ${envelope.servedDay}${
        envelope.truncated ? " (truncated)" : ""
      }`;
    case "governed_absence":
      return `nothing on ${envelope.servedDay}: ${envelope.evidence.reason}`;
    case "day_not_written":
      return `no record for ${envelope.requestedDay}`;
    case "lane_never_written":
      return "this lane has never been drained";
    default:
      return assertExhaustiveParquetPlaneState(envelope);
  }
}

describe("ParquetPlaneEnvelope narrowing", () => {
  it("enumerates exactly the four warehouse states", () => {
    expect(PARQUET_PLANE_STATES).toEqual([
      "published",
      "governed_absence",
      "day_not_written",
      "lane_never_written",
    ]);
    expect(Object.keys(STATE_CAPTIONS).sort()).toEqual([...PARQUET_PLANE_STATES].sort());
  });

  it("narrows a published day to its rows and the day they came from", () => {
    expect(
      describeEnvelope({
        state: "published",
        requestedDay: "2026-08-20",
        servedDay: "2026-08-20",
        rows: [{ cellId: "a" }, { cellId: "b" }],
        truncated: false,
      })
    ).toBe("2 rows from 2026-08-20");
  });

  it("reports a carried-forward release at its own day, not the day asked for", () => {
    // The release read's whole reason for existing: a weekly USDM release answering a Tuesday is
    // still Thursday's release, and `servedDay` is what keeps it from being captioned as fresher.
    expect(
      describeEnvelope({
        state: "published",
        requestedDay: "2026-08-20",
        servedDay: "2026-08-14",
        rows: [{ dmCategory: 2 }],
        truncated: true,
      })
    ).toBe("1 rows from 2026-08-14 (truncated)");
  });

  it("keeps a governed absence distinguishable from a gap, with its evidence intact", () => {
    const absence: ParquetPlaneEnvelope = {
      state: "governed_absence",
      requestedDay: "2026-08-20",
      servedDay: "2026-08-20",
      evidence: {
        reason: "no active perimeters in the ingest envelope",
        upstreamResponse: "200 {\"features\":[]}",
        recordedAt: "2026-08-20T06:14:02+00:00",
        runId: "run-4711",
      },
    };
    const gap: ParquetPlaneEnvelope = { state: "day_not_written", requestedDay: "2026-08-20" };

    expect(describeEnvelope(absence)).toBe(
      "nothing on 2026-08-20: no active perimeters in the ingest envelope"
    );
    expect(describeEnvelope(gap)).toBe("no record for 2026-08-20");
    // Both draw an empty map; only one of them licenses the sentence "there was none here".
    expect(describeEnvelope(absence)).not.toBe(describeEnvelope(gap));
  });

  it("separates a lane that has never written from a day that was not written", () => {
    expect(
      describeEnvelope({ state: "lane_never_written", requestedDay: "2026-08-20" })
    ).toBe("this lane has never been drained");
  });

  it("refuses a state no arm handles instead of falling through to a default rendering", () => {
    // Only reachable by lying to the type system, which is exactly what a drifted server would do.
    const drifted = { state: "conflict", requestedDay: "2026-08-20" } as unknown as never;
    expect(() => assertExhaustiveParquetPlaneState(drifted)).toThrow(
      UnhandledParquetPlaneStateError
    );
    expect(() => assertExhaustiveParquetPlaneState(drifted)).toThrow(/conflict/);
  });
});
