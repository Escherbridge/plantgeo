import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  getPublishedDroughtClassification: vi.fn(),
  getPublishedStreamflowGauges: vi.fn(),
  getPublishedWeatherForPoint: vi.fn(),
  getPublishedWeatherForBbox: vi.fn(),
  getPublishedFireDetections: vi.fn(),
  getSliderCapabilities: vi.fn(),
  getStrategyRecommendations: vi.fn(),
  getInterventionSuitability: vi.fn(),
  getServerSession: vi.fn(),
}));

// readPublishedFirePerimeters is the one read this module issues itself; it is not the subject
// of these tests, so the chain terminates in an empty result rather than a fixture.
vi.mock("@/lib/server/db", () => ({
  db: {
    select: () => ({
      from: () => ({
        innerJoin: () => ({
          where: () => ({
            orderBy: () => ({ limit: async () => [] }),
          }),
        }),
      }),
    }),
  },
}));

vi.mock("@/lib/server/auth", () => ({ getServerSession: mocks.getServerSession }));

vi.mock("@/lib/server/services/environmental-read-model", async () => {
  const actual = await vi.importActual<
    typeof import("@/lib/server/services/environmental-read-model")
  >("@/lib/server/services/environmental-read-model");
  return {
    ...actual,
    // Pinned so the suite does not change meaning at UTC midnight. resolveRequestedObservationDay
    // stays REAL: every call site in regional-context passes this day explicitly, so the live /
    // historical / unobserved split under test is the production one.
    serverCurrentDate: () => "2026-08-09",
    getPublishedDroughtClassification: mocks.getPublishedDroughtClassification,
    getPublishedStreamflowGauges: mocks.getPublishedStreamflowGauges,
    getPublishedWeatherForPoint: mocks.getPublishedWeatherForPoint,
    getPublishedWeatherForBbox: mocks.getPublishedWeatherForBbox,
    getPublishedFireDetections: mocks.getPublishedFireDetections,
    getSliderCapabilities: mocks.getSliderCapabilities,
  };
});

vi.mock("@/lib/server/services/strategy-scoring", () => ({
  getStrategyRecommendations: mocks.getStrategyRecommendations,
}));

vi.mock("@/lib/server/services/carbon-potential", () => ({
  getInterventionSuitability: mocks.getInterventionSuitability,
}));

import {
  assembleRegionalContext,
  type TemporalContext,
} from "@/lib/server/services/regional-context";
import { buildSystemPrompt, buildTemporalSection } from "@/lib/server/services/ai-prompt";
import { MAX_VIEWED_LAYERS, requestSchema } from "@/app/api/ai/regional-intelligence/route";

const SERVER_TODAY = "2026-08-09";

/**
 * The real fire-detections shape as of 2026-08: a multi-year ingestion hole between an early
 * run and the current lane. The hole is the whole reason this feature needs a vocabulary --
 * a day inside it is not a day with zero fires.
 */
const FIRE_DETECTION_COVERAGE_GAP = { from: "2023-03-01", to: "2025-11-05" };
const DAY_INSIDE_THE_FIRE_HOLE = "2024-06-01";
const DAY_THE_FIRE_LANE_PUBLISHED = "2026-02-14";

/**
 * The day fire-detections' coverage lists stop describing, and a published-looking day below it.
 *
 * The read model caps both range lists at their newest entries and reports where the surviving
 * ones start. Below that boundary a dropped gap is indistinguishable from continuous coverage,
 * which is exactly the state in which "not in coverageGaps" must stop meaning "published".
 */
const FIRE_DESCRIBED_FROM_DAY = "2023-02-01";
const DAY_BELOW_THE_DESCRIBED_BOUNDARY = "2023-01-15";

function sliderCapabilities() {
  return {
    serverCurrentDate: SERVER_TODAY,
    futureAxisDays: 0,
    layers: [
      {
        layerName: "fire-detections",
        temporalKind: "event",
        forecastHorizonDays: 0,
        forecastVariants: [],
        earliestObservedDate: "2023-01-01",
        latestObservedDate: "2026-08-08",
        coverageGaps: [FIRE_DETECTION_COVERAGE_GAP],
        thinRanges: [],
        describedFromDay: FIRE_DESCRIBED_FROM_DAY,
      },
      {
        layerName: "water-gauges",
        temporalKind: "daily_series",
        forecastHorizonDays: 0,
        forecastVariants: [],
        earliestObservedDate: "2024-01-01",
        latestObservedDate: "2026-08-08",
        coverageGaps: [],
        thinRanges: [],
        describedFromDay: null,
      },
      // `fire-perimeters` is here for one reason: it decides whether the map bounds that row by
      // its viewed day, which is what makes the set-mismatch statement below true.
      {
        layerName: "fire-perimeters",
        temporalKind: "event",
        forecastHorizonDays: 0,
        forecastVariants: [],
        earliestObservedDate: "2023-01-01",
        latestObservedDate: "2026-08-08",
        coverageGaps: [],
        thinRanges: [],
        describedFromDay: null,
      },
    ],
  };
}

function emptyDrought() {
  return { type: "FeatureCollection", features: [], availability: "unavailable", observedAt: null };
}

beforeEach(() => {
  vi.clearAllMocks();
  mocks.getStrategyRecommendations.mockResolvedValue([]);
  mocks.getInterventionSuitability.mockResolvedValue({
    availability: "unavailable",
    reason: "No carbon model is published.",
  });
  mocks.getPublishedDroughtClassification.mockResolvedValue(emptyDrought());
  mocks.getPublishedStreamflowGauges.mockResolvedValue([]);
  mocks.getPublishedWeatherForPoint.mockResolvedValue(null);
  mocks.getPublishedWeatherForBbox.mockResolvedValue([]);
  mocks.getPublishedFireDetections.mockResolvedValue({
    type: "FeatureCollection",
    features: [],
  });
  mocks.getSliderCapabilities.mockResolvedValue(sliderCapabilities());
});

describe("assembling regional context at the days the user is viewing", () => {
  it("reads each warehouse source at the day its own layer row is scrubbed to", async () => {
    await assembleRegionalContext(43.6, -116.2, [
      { layer: "fire", date: "2025-08-14", hasDataOnDate: true },
      { layer: "water", date: "2026-03-02", hasDataOnDate: true },
      { layer: "drought", date: "2026-01-07", hasDataOnDate: false },
    ]);

    expect(mocks.getPublishedFireDetections).toHaveBeenCalledWith(
      expect.any(String),
      undefined,
      "2025-08-14"
    );
    expect(mocks.getPublishedStreamflowGauges).toHaveBeenCalledWith(
      expect.any(String),
      "2026-03-02"
    );
    expect(mocks.getPublishedDroughtClassification).toHaveBeenCalledWith(
      undefined,
      "2026-01-07"
    );
  });

  it("accepts a layer row named by its geo.layers name as well as by its toggle id", async () => {
    await assembleRegionalContext(43.6, -116.2, [
      { layer: "fire-detections", date: "2025-08-14", hasDataOnDate: true },
      { layer: "water-gauges", date: "2026-03-02", hasDataOnDate: true },
    ]);

    expect(mocks.getPublishedFireDetections).toHaveBeenCalledWith(
      expect.any(String),
      undefined,
      "2025-08-14"
    );
    expect(mocks.getPublishedStreamflowGauges).toHaveBeenCalledWith(
      expect.any(String),
      "2026-03-02"
    );
  });

  it("still assembles a context when the client reports no viewed layers at all", async () => {
    const result = await assembleRegionalContext(43.6, -116.2);

    expect(mocks.getPublishedFireDetections).toHaveBeenCalledWith(
      expect.any(String),
      undefined,
      undefined
    );
    expect(mocks.getPublishedStreamflowGauges).toHaveBeenCalledWith(
      expect.any(String),
      undefined
    );
    expect(result.temporalContext.viewedLayersUnreported).toBe(true);
    expect(result.temporalContext.readings).toEqual([]);
    expect(result.temporalContext.serverCurrentDate).toBe(SERVER_TODAY);
    expect(result.payload.location.lat).toBe(43.6);
  });

  it("reads weather through the point reader at the live edge and the bbox reader on a past day", async () => {
    await assembleRegionalContext(43.6, -116.2, [
      { layer: "weather", date: SERVER_TODAY, hasDataOnDate: true },
    ]);
    expect(mocks.getPublishedWeatherForPoint).toHaveBeenCalledTimes(1);
    expect(mocks.getPublishedWeatherForBbox).not.toHaveBeenCalled();

    vi.clearAllMocks();
    mocks.getSliderCapabilities.mockResolvedValue(sliderCapabilities());
    mocks.getPublishedDroughtClassification.mockResolvedValue(emptyDrought());
    mocks.getPublishedStreamflowGauges.mockResolvedValue([]);
    mocks.getPublishedFireDetections.mockResolvedValue({
      type: "FeatureCollection",
      features: [],
    });
    mocks.getStrategyRecommendations.mockResolvedValue([]);
    mocks.getInterventionSuitability.mockResolvedValue({ availability: "unavailable" });
    mocks.getPublishedWeatherForBbox.mockResolvedValue([
      { lat: 43.7, lon: -116.3, observedAt: "2026-03-02T12:00:00Z" },
      { lat: 43.61, lon: -116.21, observedAt: "2026-03-02T12:00:00Z" },
    ]);

    const result = await assembleRegionalContext(43.6, -116.2, [
      { layer: "weather", date: "2026-03-02", hasDataOnDate: true },
    ]);
    expect(mocks.getPublishedWeatherForPoint).not.toHaveBeenCalled();
    expect(mocks.getPublishedWeatherForBbox).toHaveBeenCalledWith(
      expect.any(String),
      "2026-03-02"
    );
    // The nearest of the two bbox samples, not the first one returned.
    expect(result.payload.weather?.lat).toBe(43.61);
  });

  it("separates a day the warehouse never ingested from a day it published with nothing here", async () => {
    const hole = await assembleRegionalContext(43.6, -116.2, [
      { layer: "fire", date: DAY_INSIDE_THE_FIRE_HOLE, hasDataOnDate: false },
    ]);
    expect(hole.temporalContext.readings[0].outcome).toBe("not_published_on_viewed_date");
    expect(hole.temporalContext.readings[0].reason).toContain(
      FIRE_DETECTION_COVERAGE_GAP.from
    );

    const publishedButEmpty = await assembleRegionalContext(43.6, -116.2, [
      { layer: "fire", date: DAY_THE_FIRE_LANE_PUBLISHED, hasDataOnDate: true },
    ]);
    expect(publishedButEmpty.temporalContext.readings[0].outcome).toBe(
      "published_with_nothing_at_this_location"
    );
  });

  /**
   * F5. The day is inside the layer's axis and outside every REPORTED gap, which is precisely
   * the shape of a published day -- and the coverage lists stop describing it, so that shape is
   * an artefact of the cap rather than evidence. Reading it as published is what let the prompt
   * license "you may say there was none here" for a day that was never ingested.
   */
  it("refuses to call a day published when the coverage lists no longer describe it", async () => {
    const result = await assembleRegionalContext(43.6, -116.2, [
      { layer: "fire", date: DAY_BELOW_THE_DESCRIBED_BOUNDARY, hasDataOnDate: false },
    ]);

    expect(result.temporalContext.readings[0].outcome).toBe(
      "coverage_unknown_on_viewed_date"
    );
    expect(result.temporalContext.readings[0].outcome).not.toBe(
      "published_with_nothing_at_this_location"
    );
    expect(result.temporalContext.readings[0].reason).toContain(FIRE_DESCRIBED_FROM_DAY);
  });

  it("still calls a day published when the lists describe it and report no gap there", async () => {
    // The same layer, one day ABOVE the boundary and outside the gap: the lists speak here, so
    // their silence is evidence and the observed absence stands. Understating what is known is
    // the safe direction, but only where the record actually stops.
    const result = await assembleRegionalContext(43.6, -116.2, [
      { layer: "fire", date: "2023-02-10", hasDataOnDate: true },
    ]);

    expect(result.temporalContext.readings[0].outcome).toBe(
      "published_with_nothing_at_this_location"
    );
  });

  it("does not flag the client's claim as contradicted on a day neither side can speak for", async () => {
    // The client's mirror of this rule refuses the same day, so both halves say "cannot
    // affirm". A contradiction here would be the two halves disagreeing about a day about which
    // there is nothing to disagree.
    const result = await assembleRegionalContext(43.6, -116.2, [
      { layer: "fire", date: DAY_BELOW_THE_DESCRIBED_BOUNDARY, hasDataOnDate: false },
    ]);

    expect(result.temporalContext.readings[0].clientClaimContradicted).toBe(false);
  });

  /**
   * F6. `firePerimeters` is read at the live edge because WFIGS publishes no per-feature
   * observation time, while the `fire-perimeters` row IS date-filtered on the map. The agent is
   * therefore holding a set the user cannot see, and nothing in the date vocabulary says so.
   */
  it("says the map's set is not the payload's for a row the map filters but the reader cannot", async () => {
    const result = await assembleRegionalContext(43.6, -116.2, [
      { layer: "fire-perimeters", date: "2024-06-01", hasDataOnDate: true },
    ]);

    expect(result.temporalContext.readings[0].outcome).toBe("served_as_of_latest");
    expect(result.temporalContext.readings[0].setCorrespondence).toBe(
      "map_bounded_by_viewed_day_payload_is_latest"
    );
  });

  it("says the payload IS the drawn set for a source read at the row's own day", async () => {
    const result = await assembleRegionalContext(43.6, -116.2, [
      { layer: "fire", date: DAY_THE_FIRE_LANE_PUBLISHED, hasDataOnDate: true },
      { layer: "water", date: "2026-03-02", hasDataOnDate: true },
    ]);

    expect(result.temporalContext.readings[0].setCorrespondence).toBe(
      "payload_is_the_viewed_day"
    );
    expect(result.temporalContext.readings[1].setCorrespondence).toBe(
      "payload_is_the_viewed_day"
    );
  });

  it("claims no correspondence at all for a row that feeds no observation block", async () => {
    const result = await assembleRegionalContext(43.6, -116.2, [
      { layer: "vegetation", date: "2025-06-14", hasDataOnDate: true },
    ]);

    expect(result.temporalContext.readings[0].setCorrespondence).toBe(
      "no_payload_block_for_this_row"
    );
  });

  /**
   * The map's filter is installed only where the row also has a selectable day, so the server's
   * claim about it has to be conditioned the same way -- it asks `hasSelectableDay` against the
   * very payload the browser was served. With no capability for the layer there is no filter,
   * and the sets differ in the other direction: the map draws the whole record.
   */
  it("does not claim the map filtered a row whose axis the payload never described", async () => {
    mocks.getSliderCapabilities.mockResolvedValue({
      serverCurrentDate: SERVER_TODAY,
      futureAxisDays: 0,
      layers: [],
    });

    const result = await assembleRegionalContext(43.6, -116.2, [
      { layer: "fire-perimeters", date: "2024-06-01", hasDataOnDate: true },
    ]);

    expect(result.temporalContext.readings[0].setCorrespondence).toBe(
      "map_unbounded_payload_is_latest"
    );
  });

  it("reports a day before the layer's first observation as unpublished rather than as empty", async () => {
    const result = await assembleRegionalContext(43.6, -116.2, [
      { layer: "water", date: "2020-05-05", hasDataOnDate: false },
    ]);
    expect(result.temporalContext.readings[0].outcome).toBe(
      "not_published_on_viewed_date"
    );
    expect(result.temporalContext.readings[0].reason).toContain("2024-01-01");
  });

  it("never reports a failed read as the warehouse having published nothing", async () => {
    mocks.getPublishedFireDetections.mockRejectedValue(new Error("connection reset"));

    const result = await assembleRegionalContext(43.6, -116.2, [
      { layer: "fire", date: DAY_INSIDE_THE_FIRE_HOLE, hasDataOnDate: false },
    ]);
    expect(result.temporalContext.readings[0].outcome).toBe("read_failed");
  });

  it("reports coverage as unknown when the coverage record itself could not be read", async () => {
    mocks.getSliderCapabilities.mockRejectedValue(new Error("capabilities unavailable"));

    const result = await assembleRegionalContext(43.6, -116.2, [
      { layer: "fire", date: DAY_INSIDE_THE_FIRE_HOLE, hasDataOnDate: false },
    ]);
    expect(result.temporalContext.readings[0].outcome).toBe(
      "coverage_unknown_on_viewed_date"
    );
  });

  it("reports a layer the capability payload does not describe as unknown coverage rather than as a hole", async () => {
    const result = await assembleRegionalContext(43.6, -116.2, [
      { layer: "drought", date: "2026-01-07", hasDataOnDate: false },
    ]);
    expect(result.temporalContext.readings[0].outcome).toBe(
      "coverage_unknown_on_viewed_date"
    );
  });

  it("marks a source whose reader accepts no day as served at the live edge", async () => {
    const result = await assembleRegionalContext(43.6, -116.2, [
      { layer: "fire-perimeters", date: "2025-08-14", hasDataOnDate: true },
    ]);
    expect(result.temporalContext.readings[0].outcome).toBe("served_as_of_latest");
  });

  it("refuses a viewed day in the future instead of answering it with the newest observation", async () => {
    const result = await assembleRegionalContext(43.6, -116.2, [
      { layer: "fire", date: "2027-01-01", hasDataOnDate: false },
    ]);
    expect(result.temporalContext.readings[0].outcome).toBe(
      "viewed_date_not_observable"
    );
  });

  it("reports a viewed row that feeds no observation block instead of dropping it", async () => {
    const result = await assembleRegionalContext(43.6, -116.2, [
      { layer: "vegetation", date: "2025-06-14", hasDataOnDate: true },
    ]);
    expect(result.temporalContext.readings[0].outcome).toBe(
      "not_represented_in_payload"
    );
    expect(result.temporalContext.readings[0].evidenceSource).toBeNull();
    expect(result.temporalContext.viewedDates).toEqual(["2025-06-14"]);
  });

  it("flags the client's own coverage claim when the layer's record contradicts it", async () => {
    const result = await assembleRegionalContext(43.6, -116.2, [
      { layer: "fire", date: DAY_INSIDE_THE_FIRE_HOLE, hasDataOnDate: true },
    ]);
    expect(result.temporalContext.readings[0].clientReportsDataOnDate).toBe(true);
    expect(result.temporalContext.readings[0].clientClaimContradicted).toBe(true);
  });

  it("lists every block no viewed row named as served at the live edge", async () => {
    const result = await assembleRegionalContext(43.6, -116.2, [
      { layer: "fire", date: DAY_THE_FIRE_LANE_PUBLISHED, hasDataOnDate: true },
    ]);
    expect(result.temporalContext.sourcesServedAsOfLatest).toContain("streamflow");
    expect(result.temporalContext.sourcesServedAsOfLatest).not.toContain(
      "fireDetections"
    );
  });

  it("collects the distinct viewed days in ascending order", async () => {
    const result = await assembleRegionalContext(43.6, -116.2, [
      { layer: "water", date: "2026-03-02", hasDataOnDate: true },
      { layer: "fire", date: "2025-08-14", hasDataOnDate: true },
      { layer: "weather", date: "2026-03-02", hasDataOnDate: true },
    ]);
    expect(result.temporalContext.viewedDates).toEqual(["2025-08-14", "2026-03-02"]);
  });
});

function temporalContext(overrides: Partial<TemporalContext> = {}): TemporalContext {
  return {
    serverCurrentDate: SERVER_TODAY,
    viewedLayersUnreported: false,
    readings: [],
    viewedDates: [],
    sourcesServedAsOfLatest: [],
    ...overrides,
  };
}

describe("the prompt section describing what each layer is showing", () => {
  it("tells the agent a never-ingested day is a coverage hole and forbids reporting it as zero", () => {
    const text = buildTemporalSection(
      temporalContext({
        readings: [
          {
            layer: "fire",
            viewedDate: DAY_INSIDE_THE_FIRE_HOLE,
            clientReportsDataOnDate: false,
            evidenceSource: "fireDetections",
            outcome: "not_published_on_viewed_date",
            reason: "fire-detections published nothing from 2023-03-01 through 2025-11-05.",
            clientClaimContradicted: false,
            setCorrespondence: "payload_is_the_viewed_day",
          },
        ],
        viewedDates: [DAY_INSIDE_THE_FIRE_HOLE],
      })
    );

    expect(text).toContain("PUBLISHED NOTHING");
    expect(text).toContain("coverage hole, not a measurement");
    expect(text).toContain(
      `Do not write "0", "none", "no activity", or any other absence for ${DAY_INSIDE_THE_FIRE_HOLE}`
    );
    expect(text).toContain("say the day was never ingested");
    expect(text).toContain(
      "fire-detections published nothing from 2023-03-01 through 2025-11-05."
    );
  });

  it("licenses an absence only on a day the layer actually published", () => {
    const observedAbsence = buildTemporalSection(
      temporalContext({
        readings: [
          {
            layer: "fire",
            viewedDate: DAY_THE_FIRE_LANE_PUBLISHED,
            clientReportsDataOnDate: true,
            evidenceSource: "fireDetections",
            outcome: "published_with_nothing_at_this_location",
            reason: null,
            clientClaimContradicted: false,
            setCorrespondence: "payload_is_the_viewed_day",
          },
        ],
        viewedDates: [DAY_THE_FIRE_LANE_PUBLISHED],
      })
    );
    const coverageHole = buildTemporalSection(
      temporalContext({
        readings: [
          {
            layer: "fire",
            viewedDate: DAY_INSIDE_THE_FIRE_HOLE,
            clientReportsDataOnDate: false,
            evidenceSource: "fireDetections",
            outcome: "not_published_on_viewed_date",
            reason: "fire-detections published nothing from 2023-03-01 through 2025-11-05.",
            clientClaimContradicted: false,
            setCorrespondence: "payload_is_the_viewed_day",
          },
        ],
        viewedDates: [DAY_INSIDE_THE_FIRE_HOLE],
      })
    );

    expect(observedAbsence).toContain("the layer PUBLISHED on this day");
    expect(observedAbsence).toContain(
      `you may say there was none here on ${DAY_THE_FIRE_LANE_PUBLISHED}`
    );
    expect(coverageHole).not.toContain("you may say there was none here");
    expect(observedAbsence).not.toBe(coverageHole);
  });

  it("states plainly that the layers are not on the same day when they are not", () => {
    const text = buildTemporalSection(
      temporalContext({
        readings: [],
        viewedDates: ["2025-06-14", "2026-03-02", "2026-08-07"],
      })
    );

    expect(text).toContain("## These layers are NOT on the same day");
    expect(text).toContain(
      "The rows above span 3 different days: 2025-06-14, 2026-03-02, 2026-08-07."
    );
    expect(text).toContain("comparison ACROSS TIME");
    expect(text).toContain(
      "never present two layers on different days as a single moment"
    );
  });

  it("says the layers share a day when they do, rather than staying silent about it", () => {
    const text = buildTemporalSection(
      temporalContext({ viewedDates: ["2026-08-07"] })
    );
    expect(text).toContain("## Every viewed layer is on the same day");
    expect(text).toContain("All rows above are on 2026-08-07.");
    expect(text).not.toContain("NOT on the same day");
  });

  it("says the client named no days rather than implying every layer is on today", () => {
    const text = buildTemporalSection(
      temporalContext({ viewedLayersUnreported: true })
    );
    expect(text).toContain(
      "The client did not report which day each map layer is showing"
    );
    expect(text).toContain("every observation above is as-of-latest");
    expect(text).toContain(`The server's today is ${SERVER_TODAY}.`);
  });

  it("forbids attributing an as-of-latest value to the day the row is scrubbed to", () => {
    const text = buildTemporalSection(
      temporalContext({
        readings: [
          {
            layer: "fire-perimeters",
            viewedDate: "2025-08-14",
            clientReportsDataOnDate: true,
            evidenceSource: "firePerimeters",
            outcome: "served_as_of_latest",
            reason: "firePerimeters has no per-day reader.",
            clientClaimContradicted: false,
            setCorrespondence: "map_bounded_by_viewed_day_payload_is_latest",
          },
        ],
        viewedDates: ["2025-08-14"],
      })
    );
    expect(text).toContain("the values above are the LATEST published ones");
    expect(text).toContain("never to 2025-08-14");
  });

  /**
   * F6, at the seam where it reaches the model. The date vocabulary alone says nothing false
   * here -- the values ARE the latest ones and the line already forbids dating them to the
   * viewed day -- and that is exactly the problem: nothing tells the model the map is drawing a
   * different set, so "what fires are here" gets answered about perimeters the user cannot see
   * while the three on screen go unmentioned.
   */
  it("tells the agent the map is drawing a different set when the payload is not what is on screen", () => {
    const text = buildTemporalSection(
      temporalContext({
        readings: [
          {
            layer: "fire-perimeters",
            viewedDate: "2024-06-01",
            clientReportsDataOnDate: true,
            evidenceSource: "firePerimeters",
            outcome: "served_as_of_latest",
            reason: "firePerimeters has no per-day reader.",
            clientClaimContradicted: false,
            setCorrespondence: "map_bounded_by_viewed_day_payload_is_latest",
          },
        ],
        viewedDates: ["2024-06-01"],
      })
    );

    expect(text).toContain("NOT the set on the user's screen");
    expect(text).toContain(
      "the map draws this layer filtered to features observed on or before 2024-06-01"
    );
    expect(text).toContain("Do not describe what this layer looks like on the map");
    expect(text).toContain("do not count its features as what the user can see");
  });

  it("stays silent about the drawn set for a source read at the row's own day", () => {
    const text = buildTemporalSection(
      temporalContext({
        readings: [
          {
            layer: "fire",
            viewedDate: DAY_THE_FIRE_LANE_PUBLISHED,
            clientReportsDataOnDate: true,
            evidenceSource: "fireDetections",
            outcome: "observed_on_viewed_date",
            reason: null,
            clientClaimContradicted: false,
            setCorrespondence: "payload_is_the_viewed_day",
          },
        ],
        viewedDates: [DAY_THE_FIRE_LANE_PUBLISHED],
      })
    );

    // A warning on every row is a warning on none: it must appear only where the sets really
    // differ, or the model learns to read past it.
    expect(text).not.toContain("NOT the set on the user's screen");
  });

  /**
   * F5, at the same seam. An undescribed day and an observed absence are the two states this
   * whole section exists to keep apart, and the strongest sentence the prompt can emit --
   * "you may say there was none here" -- must never reach the first of them.
   */
  it("says an undescribed day's coverage is unknown rather than licensing an absence", () => {
    const undescribed = buildTemporalSection(
      temporalContext({
        readings: [
          {
            layer: "fire",
            viewedDate: DAY_BELOW_THE_DESCRIBED_BOUNDARY,
            clientReportsDataOnDate: false,
            evidenceSource: "fireDetections",
            outcome: "coverage_unknown_on_viewed_date",
            reason: `fire-detections reports its coverage only from ${FIRE_DESCRIBED_FROM_DAY} onward, so nothing on record says whether ${DAY_BELOW_THE_DESCRIBED_BOUNDARY} was ingested.`,
            clientClaimContradicted: false,
            setCorrespondence: "payload_is_the_viewed_day",
          },
        ],
        viewedDates: [DAY_BELOW_THE_DESCRIBED_BOUNDARY],
      })
    );
    const observedAbsence = buildTemporalSection(
      temporalContext({
        readings: [
          {
            layer: "fire",
            viewedDate: DAY_THE_FIRE_LANE_PUBLISHED,
            clientReportsDataOnDate: true,
            evidenceSource: "fireDetections",
            outcome: "published_with_nothing_at_this_location",
            reason: null,
            clientClaimContradicted: false,
            setCorrespondence: "payload_is_the_viewed_day",
          },
        ],
        viewedDates: [DAY_THE_FIRE_LANE_PUBLISHED],
      })
    );

    expect(undescribed).toContain(
      `Report this as unknown for ${DAY_BELOW_THE_DESCRIBED_BOUNDARY}`
    );
    expect(undescribed).toContain("Do not state an absence and do not state a presence");
    expect(undescribed).toContain(`only from ${FIRE_DESCRIBED_FROM_DAY} onward`);
    // The two must not be confusable, in either direction.
    expect(undescribed).not.toContain("you may say there was none here");
    expect(undescribed).not.toContain("This is an observed absence");
    expect(observedAbsence).toContain("This is an observed absence");
    expect(undescribed).not.toBe(observedAbsence);
  });

  it("teaches the model both new rules in the system prompt, not only in the per-row lines", () => {
    const system = buildSystemPrompt(false);

    expect(system).toContain(
      "Coverage records are reported only from a stated day onward"
    );
    expect(system).toContain("its silence is not evidence");
    expect(system).toContain(
      "The data you were handed is not automatically the data on the user's screen"
    );
  });

  it("refuses to turn a failed read into either a presence or an absence", () => {
    const text = buildTemporalSection(
      temporalContext({
        readings: [
          {
            layer: "water",
            viewedDate: "2026-03-02",
            clientReportsDataOnDate: true,
            evidenceSource: "streamflow",
            outcome: "read_failed",
            reason: "The streamflow read did not complete for this request.",
            clientClaimContradicted: false,
            setCorrespondence: "payload_is_the_viewed_day",
          },
        ],
        viewedDates: ["2026-03-02"],
      })
    );
    expect(text).toContain("the read of this source FAILED");
    expect(text).toContain(
      "do not report it as published and do not report it as absent"
    );
  });

  it("names the client's contradicted coverage claim instead of silently overriding it", () => {
    const text = buildTemporalSection(
      temporalContext({
        readings: [
          {
            layer: "fire",
            viewedDate: DAY_INSIDE_THE_FIRE_HOLE,
            clientReportsDataOnDate: true,
            evidenceSource: "fireDetections",
            outcome: "not_published_on_viewed_date",
            reason: "fire-detections published nothing from 2023-03-01 through 2025-11-05.",
            clientClaimContradicted: true,
            setCorrespondence: "payload_is_the_viewed_day",
          },
        ],
        viewedDates: [DAY_INSIDE_THE_FIRE_HOLE],
      })
    );
    expect(text).toContain("The client reported this day's coverage differently");
    expect(text).toContain("coverage record is authoritative here");
  });
});

describe("the viewed-layers request contract", () => {
  const validBody = {
    lat: 43.6,
    lon: -116.2,
    locationConsent: { precision: "approximate" as const, confirmed: true as const },
  };

  it("accepts a request that names no viewed layers at all", () => {
    expect(requestSchema.safeParse(validBody).success).toBe(true);
  });

  it("accepts a viewed-layers array at the bound", () => {
    const viewedLayers = Array.from({ length: MAX_VIEWED_LAYERS }, (_unused, index) => ({
      layer: `layer-${index}`,
      date: "2026-08-07",
      hasDataOnDate: true,
    }));
    expect(requestSchema.safeParse({ ...validBody, viewedLayers }).success).toBe(true);
  });

  it("rejects a viewed-layers array one entry past the bound", () => {
    const viewedLayers = Array.from(
      { length: MAX_VIEWED_LAYERS + 1 },
      (_unused, index) => ({
        layer: `layer-${index}`,
        date: "2026-08-07",
        hasDataOnDate: true,
      })
    );
    expect(requestSchema.safeParse({ ...validBody, viewedLayers }).success).toBe(false);
  });

  it("rejects a viewed layer whose date is not a calendar day", () => {
    const parsed = requestSchema.safeParse({
      ...validBody,
      viewedLayers: [
        { layer: "fire", date: "2026-08-07T00:00:00Z", hasDataOnDate: true },
      ],
    });
    expect(parsed.success).toBe(false);
  });

  it("rejects a viewed layer that omits its coverage claim", () => {
    const parsed = requestSchema.safeParse({
      ...validBody,
      viewedLayers: [{ layer: "fire", date: "2026-08-07" }],
    });
    expect(parsed.success).toBe(false);
  });

  it("keeps the viewed-layers bound small enough that a full array fits the body cap", () => {
    const viewedLayers = Array.from({ length: MAX_VIEWED_LAYERS }, () => ({
      layer: "building-footprints",
      date: "2026-08-07",
      hasDataOnDate: true,
    }));
    const bytes = new TextEncoder().encode(
      JSON.stringify({ ...validBody, question: "x".repeat(1000), viewedLayers })
    ).length;
    expect(bytes).toBeLessThan(16 * 1024);
  });
});
