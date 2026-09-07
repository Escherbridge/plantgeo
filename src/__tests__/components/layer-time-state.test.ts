import { describe, expect, it } from "vitest";
import {
  describeWithholdingReason,
  findWithheldCapability,
  isParquetCoverageUnavailable,
  LAYER_WITHHOLDING_REASONS,
  readWithheldCapabilities,
  resolveLayerTimeState,
  type LayerWithholdingReason,
} from "@/components/map/layer-panel/layer-time-state";
import type { SliderCapabilities, SliderLayerCapability } from "@/types/time-slider";
// TYPE-ONLY, and it must stay that way: this is a test for a client module, and a value import
// would pull `parquet-slider-capabilities.ts` -> `environmental-read-model.ts` -> the `db` handle
// into a suite that must never open a connection. Erased at compile time; see the drift case at
// the bottom of this file for what it buys.
import type {
  WithheldParquetCapability,
  WithheldParquetCapabilityReason,
} from "@/lib/server/services/parquet-slider-capabilities";

const SERVER_CURRENT_DATE = "2026-09-07";

function capability(overrides: Partial<SliderLayerCapability> = {}): SliderLayerCapability {
  return {
    layerName: "fire-detections",
    temporalKind: "daily_series",
    forecastHorizonDays: 0,
    forecastVariants: [],
    earliestObservedDate: "2026-08-01",
    latestObservedDate: "2026-09-06",
    coverageGaps: [],
    thinRanges: [],
    describedFromDay: null,
    ...overrides,
  };
}

/**
 * The payload shape production really answers with: `ParquetSliderCapabilities`, which is
 * `SliderCapabilities` plus the four Parquet fields. The client type does not declare them, which
 * is exactly the gap `readWithheldCapabilities` reads across -- so the fixture is built as the
 * WIDER object and then handed over as the narrower one, the same way `setCapabilities` narrows
 * it on the way into the store.
 */
function capabilities(options: {
  layers?: SliderLayerCapability[];
  withheld?: WithheldParquetCapability[];
  parquetCoverageUnavailable?: boolean;
  streamsUnavailable?: boolean;
}): SliderCapabilities {
  const payload = {
    serverCurrentDate: SERVER_CURRENT_DATE,
    futureAxisDays: 30,
    streamsUnavailable: options.streamsUnavailable ?? false,
    layers: options.layers ?? [],
    parquetCoverageGeneratedAt: "2026-09-07T04:00:00.000Z",
    parquetCoverageEvaluatedThroughDay: SERVER_CURRENT_DATE,
    parquetCoverageUnavailable: options.parquetCoverageUnavailable ?? false,
    withheldParquetCapabilities: options.withheld ?? [],
  };
  return payload as SliderCapabilities;
}

function withheld(
  layerName: string,
  reason: WithheldParquetCapabilityReason,
  parquetLanes: string[] = [layerName]
): WithheldParquetCapability {
  return { layerName, reason, parquetLanes, missingEvidence: [] };
}

describe("resolveLayerTimeState: the transport states", () => {
  /**
   * The state the owner's complaint is about. A cold `getSliderCapabilities` measured 7.6-8.5s
   * against production and used to render as a bare `return null` on every row -- byte-identical
   * to a layer that has no dates at all.
   */
  it("reads the null-payload-without-a-failure pair as loading, not as absence", () => {
    const state = resolveLayerTimeState({
      warehouseLayerName: "fire-detections",
      capabilities: null,
      capabilitiesUnavailable: false,
    });

    expect(state.kind).toBe("loading");
    // Something is on its way, so the row is allowed to move; see `isSettling`.
    expect(state.isSettling).toBe(true);
    // The wait is named, which is the whole point: a reader who is told the first read takes a
    // few seconds is not looking at a bug.
    expect(state.detail).toContain("few seconds");
    expect(state.reason).toBeNull();
  });

  it("reads the same null payload as an error once the fetch has never succeeded", () => {
    const state = resolveLayerTimeState({
      warehouseLayerName: "fire-detections",
      capabilities: null,
      capabilitiesUnavailable: true,
    });

    expect(state.kind).toBe("error");
    // OUR failure, never a claim about the warehouse -- the sentence the bigint 500 taught.
    expect(state.detail).toContain("not a gap in the record");
    // The two transport states must not share a word, or the row cannot be read at a glance.
    expect(state.badge).not.toBe(
      resolveLayerTimeState({
        warehouseLayerName: "fire-detections",
        capabilities: null,
        capabilitiesUnavailable: false,
      }).badge
    );
  });

  it("claims nothing at all about a layer no warehouse stream backs", () => {
    const state = resolveLayerTimeState({
      warehouseLayerName: null,
      capabilities: null,
      capabilitiesUnavailable: true,
    });

    // A failed fetch says nothing about a layer that was never in the census, so the state is
    // decided BEFORE transport is consulted.
    expect(state.kind).toBe("no_time_axis");
    expect(state.detail).toContain("No warehouse layer backs this one");
  });
});

describe("resolveLayerTimeState: the record's own states", () => {
  it("calls a layer with a real axis ready", () => {
    const state = resolveLayerTimeState({
      warehouseLayerName: "fire-detections",
      capabilities: capabilities({ layers: [capability()] }),
      capabilitiesUnavailable: false,
    });

    expect(state.kind).toBe("ready");
    expect(state.isSettling).toBe(false);
  });

  /**
   * The constraint stated in the brief: `sensors`, `watersheds` and `evacuation-zones` are
   * snapshots, `sliderDomain` returns null for a snapshot, and that is neither an error nor an
   * empty -- it is a complete record that does not vary by day.
   */
  it("gives a snapshot its own state rather than folding it into empty or error", () => {
    const state = resolveLayerTimeState({
      warehouseLayerName: "watersheds",
      capabilities: capabilities({
        layers: [
          capability({
            layerName: "watersheds",
            temporalKind: "snapshot",
            earliestObservedDate: "2013-01-18",
            latestObservedDate: "2013-01-18",
          }),
        ],
      }),
      capabilitiesUnavailable: false,
    });

    expect(state.kind).toBe("no_time_axis");
    expect(state.detail).toContain("draws the same on every date");
    // Nothing is going to change about it, so nothing on the row may suggest waiting.
    expect(state.isSettling).toBe(false);
    // And it is NOT the empty state: a snapshot's record is complete.
    expect(state.kind).not.toBe("empty");
  });

  it("calls a published layer that has observed nothing empty, and says which", () => {
    const state = resolveLayerTimeState({
      warehouseLayerName: "interventions",
      capabilities: capabilities({
        layers: [
          capability({
            layerName: "interventions",
            earliestObservedDate: null,
            latestObservedDate: null,
          }),
        ],
      }),
      capabilitiesUnavailable: false,
    });

    expect(state.kind).toBe("empty");
    expect(state.detail).toContain("Nothing observed yet");
  });

  it("names the day when a record starts after today rather than saying it is empty", () => {
    const state = resolveLayerTimeState({
      warehouseLayerName: "fire-detections",
      capabilities: capabilities({
        layers: [capability({ earliestObservedDate: "2026-12-01" })],
      }),
      capabilitiesUnavailable: false,
    });

    expect(state.kind).toBe("empty");
    // The day is in the sentence, because "no range" without it is unactionable.
    expect(state.detail).toContain("2026-12-01");
    expect(state.detail).toContain("after today");
  });

  it("says the payload is at fault, not the record, when its earliest day is not a date", () => {
    const state = resolveLayerTimeState({
      warehouseLayerName: "fire-detections",
      capabilities: capabilities({
        layers: [capability({ earliestObservedDate: "not-a-day" })],
      }),
      capabilitiesUnavailable: false,
    });

    expect(state.detail).toContain("not-a-day");
    expect(state.detail).toContain("is not a date");
    // Distinct from the two honest empties above, which is the claim: a malformed payload must
    // not be reported as an empty record.
    expect(state.badge).not.toBe(
      resolveLayerTimeState({
        warehouseLayerName: "fire-detections",
        capabilities: capabilities({
          layers: [capability({ earliestObservedDate: null, latestObservedDate: null })],
        }),
        capabilitiesUnavailable: false,
      }).badge
    );
  });

  it("falls back to not-published for a layer this payload simply omits", () => {
    const state = resolveLayerTimeState({
      warehouseLayerName: "fire-detections",
      capabilities: capabilities({ layers: [] }),
      capabilitiesUnavailable: false,
    });

    expect(state.kind).toBe("empty");
    expect(state.detail).toContain("Not published to the warehouse record yet");
  });

  it("blames the short scan, not the record, when the payload says its streams are unavailable", () => {
    const state = resolveLayerTimeState({
      warehouseLayerName: "fire-detections",
      capabilities: capabilities({ layers: [], streamsUnavailable: true }),
      capabilitiesUnavailable: false,
    });

    // The same absence, opposite attribution: a timed-out scan is ours, and the loader is already
    // retrying it on its 30s clock.
    expect(state.kind).toBe("error");
    expect(state.detail).toContain("could not be read");
    expect(state.isSettling).toBe(true);
  });
});

describe("resolveLayerTimeState: a withheld layer is not an error", () => {
  /**
   * The distinction the owner asked for by name. Both layers are absent from `layers` in exactly
   * the same way; only the withheld list tells them apart, and they must not be captioned alike:
   * one is waiting on a build that is running, the other will never arrive.
   */
  it("says something different about an unpublished index and a lane that never wrote", () => {
    const indexing = resolveLayerTimeState({
      warehouseLayerName: "fire-detections",
      capabilities: capabilities({
        withheld: [withheld("fire-detections", "availability_unpublished")],
      }),
      capabilitiesUnavailable: false,
    });
    const never = resolveLayerTimeState({
      warehouseLayerName: "soil-survey",
      capabilities: capabilities({
        withheld: [withheld("soil-survey", "lane_never_written")],
      }),
      capabilitiesUnavailable: false,
    });

    expect(indexing.kind).toBe("withheld");
    expect(never.kind).toBe("withheld");

    // Every visible token differs -- badge AND sentence. A shared badge with different tooltips
    // would still read as one state on a dock full of rows.
    expect(indexing.badge).not.toBe(never.badge);
    expect(indexing.detail).not.toBe(never.detail);

    // And the sentences say the two opposite things they mean.
    expect(indexing.detail).toContain("still being built");
    expect(indexing.isSettling).toBe(true);
    expect(never.detail).toContain("never published anything");
    // Nothing is coming. Pulsing this row would promise an arrival that is not on its way.
    expect(never.isSettling).toBe(false);

    // The machine reason survives for the operator hover, unmapped and unspun.
    expect(indexing.reason).toBe("availability_unpublished");
    expect(never.reason).toBe("lane_never_written");
    expect(indexing.evidenceLanes).toEqual(["fire-detections"]);
  });

  it("gives every reason the serving side can emit its own sentence", () => {
    const sentences = LAYER_WITHHOLDING_REASONS.map(
      (reason) => describeWithholdingReason(reason).detail
    );

    // Fifteen reasons, fifteen distinct sentences: a reason that shared wording with another
    // would be a reason the UI cannot actually report.
    expect(new Set(sentences).size).toBe(LAYER_WITHHOLDING_REASONS.length);
    for (const sentence of sentences) {
      // Each is a real sentence about the layer, not a status code echoed at the user.
      expect(sentence.length).toBeGreaterThan(30);
      expect(sentence).not.toMatch(/_/);
    }
  });

  it("prefers the named reason over the generic not-published sentence", () => {
    const named = resolveLayerTimeState({
      warehouseLayerName: "fire-detections",
      capabilities: capabilities({
        withheld: [withheld("fire-detections", "availability_unpublished")],
      }),
      capabilitiesUnavailable: false,
    });
    const unnamed = resolveLayerTimeState({
      warehouseLayerName: "fire-detections",
      capabilities: capabilities({}),
      capabilitiesUnavailable: false,
    });

    // Same absence from `layers`; the withheld list is the ONLY thing that separates them, so a
    // resolver that ignored it would report both as "not published yet".
    expect(named.detail).not.toBe(unnamed.detail);
  });

  it("keeps a withheld layer out of the error states entirely", () => {
    for (const reason of LAYER_WITHHOLDING_REASONS) {
      const state = resolveLayerTimeState({
        warehouseLayerName: "fire-detections",
        capabilities: capabilities({ withheld: [withheld("fire-detections", reason)] }),
        capabilitiesUnavailable: false,
      });
      expect(state.kind, reason).toBe("withheld");
      // Withheld data is a governed decision, not a fault, and must never read as one.
      expect(state.detail.toLowerCase(), reason).not.toContain("error");
      expect(state.detail.toLowerCase(), reason).not.toContain("failed to");
    }
  });

  it("only pulses the two withheld reasons something is actually retrying", () => {
    const settling = LAYER_WITHHOLDING_REASONS.filter(
      (reason) => describeWithholdingReason(reason).isSettling === true
    );

    // `coverage_unavailable` is the loader's own 30s retry (a cold census that exhausted the 8s
    // timeout), and `availability_unpublished` is an index build that is running. Everything else
    // is a settled decision that will not move while the reader watches.
    expect([...settling].sort()).toEqual(["availability_unpublished", "coverage_unavailable"]);
  });
});

describe("reading the withheld evidence the client type does not declare", () => {
  it("finds the entry for one layer and ignores every other layer's", () => {
    const payload = capabilities({
      withheld: [
        withheld("vegetation", "lane_never_written"),
        withheld("fire-detections", "availability_unpublished", ["fire-detections", "firms"]),
      ],
    });

    expect(findWithheldCapability(payload, "fire-detections")).toEqual({
      layerName: "fire-detections",
      reason: "availability_unpublished",
      parquetLanes: ["fire-detections", "firms"],
    });
    expect(findWithheldCapability(payload, "weather-observations")).toBeNull();
    expect(findWithheldCapability(payload, null)).toBeNull();
  });

  it("reads nothing at all from a payload published before the field existed", () => {
    // An older server sends no `withheldParquetCapabilities`. Absence must read as "no reason
    // stated", never as a reason.
    const legacy = {
      serverCurrentDate: SERVER_CURRENT_DATE,
      futureAxisDays: 0,
      streamsUnavailable: false,
      layers: [],
    } as SliderCapabilities;

    expect(readWithheldCapabilities(legacy)).toEqual([]);
    expect(readWithheldCapabilities(null)).toEqual([]);
    expect(isParquetCoverageUnavailable(legacy)).toBe(false);
  });

  it("drops an entry whose reason this build cannot word instead of showing the raw string", () => {
    const payload = {
      serverCurrentDate: SERVER_CURRENT_DATE,
      futureAxisDays: 0,
      streamsUnavailable: false,
      layers: [],
      withheldParquetCapabilities: [
        { layerName: "fire-detections", reason: "rung_bounds_v2", parquetLanes: ["x"] },
        { layerName: "vegetation", reason: "lane_never_written", parquetLanes: null },
        "not an object",
      ],
    } as unknown as SliderCapabilities;

    // A chip reading `rung_bounds_v2` teaches a reader nothing and looks like a crash; the layer
    // falls through to the weaker-but-never-wrong "not published yet" instead.
    expect(findWithheldCapability(payload, "fire-detections")).toBeNull();
    expect(
      resolveLayerTimeState({
        warehouseLayerName: "fire-detections",
        capabilities: payload,
        capabilitiesUnavailable: false,
      }).detail
    ).toContain("Not published to the warehouse record yet");

    // A malformed neighbour does not take the readable entries down with it.
    expect(findWithheldCapability(payload, "vegetation")).toEqual({
      layerName: "vegetation",
      reason: "lane_never_written",
      parquetLanes: [],
    });
  });

  it("reports the whole-census failure flag the loader already retries on", () => {
    expect(isParquetCoverageUnavailable(capabilities({ parquetCoverageUnavailable: true }))).toBe(
      true
    );
    expect(isParquetCoverageUnavailable(capabilities({}))).toBe(false);
  });

  /**
   * The drift alarm for the copied enum.
   *
   * `LAYER_WITHHOLDING_REASONS` is a hand-copy of `WithheldParquetCapabilityReason`, because no
   * file under `src/components/` may import from `@/lib/server/**`. These two assignments are
   * what keep the copy honest: they compile only while the two lists name the same members, in
   * BOTH directions -- add a reason on the serving side and the first fails, delete one here and
   * the second does. A new reason then arrives as a red type-check rather than as an unworded
   * layer in production.
   */
  it("names exactly the reasons the serving side can emit", () => {
    const clientReasonIsServed: WithheldParquetCapabilityReason = "availability_unpublished";
    const servedReasonIsWorded: LayerWithholdingReason = clientReasonIsServed;
    expect(describeWithholdingReason(servedReasonIsWorded).badge).toBe("Indexing");

    // Assignable both ways => the two unions are the same set.
    const everyClientReason: WithheldParquetCapabilityReason[] = [...LAYER_WITHHOLDING_REASONS];
    const everyServedReason: LayerWithholdingReason[] = everyClientReason;
    expect(everyServedReason).toHaveLength(LAYER_WITHHOLDING_REASONS.length);
  });
});
