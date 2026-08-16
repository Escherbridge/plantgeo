import { describe, it, expect, beforeEach } from "vitest";
import { act, renderHook } from "@testing-library/react";
import {
  UNINITIALIZED_DATE,
  addDays,
  clampDateToDomain,
  dayOffset,
  findLayerCapability,
  hasSelectableDay,
  isDayDescribed,
  isFutureDate,
  isWithinCoverageGap,
  latestObservedDateFor,
  layerAvailabilityAt,
  readPersistedLayerDates,
  resolveLayerDate,
  resolveVariant,
  sliderDomain,
  sliderMaxOffset,
  todayOffset,
  useTimeSliderStore,
  warehouseLayerNameFor,
} from "@/stores/time-slider-store";
import { LAYER_REGISTRY, type LayerToggleId } from "@/lib/map/layer-registry";
import {
  climateFieldStreamName,
  climateFieldToggleId,
} from "@/lib/environmental/climate-field";
import { SLIDER_STREAM_LAYER_NAMES } from "@/types/time-slider";
import type { SliderCapabilities, SliderLayerCapability } from "@/types/time-slider";

// Deliberately nowhere near the machine's own date: any stray `new Date()` in the
// logic under test flips these expectations and fails the suite.
const SERVER_CURRENT_DATE = "2019-03-07";

/**
 * geo.layers names are real, and each one is reachable from a real toggle id -- `vegetation`
 * from `vegetation`, `weather-observations` from `weather`, `fire-perimeters` from
 * `fire-perimeters`, `sensors` from `sensors`. Per-layer dates are keyed by TOGGLE id and
 * resolved through `LAYER_REGISTRY[…].warehouseLayerName`, so a made-up name here would make
 * every default silently fall through to the server's today.
 */
const vegetationLayer: SliderLayerCapability = {
  layerName: "vegetation",
  temporalKind: "daily_series",
  forecastHorizonDays: 10,
  forecastVariants: ["monte_carlo"],
  earliestObservedDate: "2015-06-01",
  latestObservedDate: "2019-02-20",
  coverageGaps: [],
  thinRanges: [],
  describedFromDay: null,
};

const weatherLayer: SliderLayerCapability = {
  layerName: "weather-observations",
  temporalKind: "daily_series",
  forecastHorizonDays: 30,
  forecastVariants: ["monte_carlo", "ml"],
  earliestObservedDate: "2017-01-15",
  latestObservedDate: "2019-03-06",
  coverageGaps: [],
  thinRanges: [],
  describedFromDay: null,
};

const firePerimeterLayer: SliderLayerCapability = {
  layerName: "fire-perimeters",
  temporalKind: "event",
  forecastHorizonDays: 0,
  forecastVariants: [],
  earliestObservedDate: "2018-07-04",
  latestObservedDate: "2019-03-01",
  coverageGaps: [],
  thinRanges: [],
  describedFromDay: null,
};

const sensorLayer: SliderLayerCapability = {
  layerName: "sensors",
  temporalKind: "snapshot",
  forecastHorizonDays: 0,
  forecastVariants: [],
  earliestObservedDate: null,
  latestObservedDate: null,
  coverageGaps: [],
  thinRanges: [],
  describedFromDay: null,
};

const capabilities: SliderCapabilities = {
  serverCurrentDate: SERVER_CURRENT_DATE,
  // 0 so every domain assertion below still measures the FORECAST horizon alone; the
  // future-axis span has its own tests rather than shifting these.
  futureAxisDays: 0,
  streamsUnavailable: false,
  layers: [vegetationLayer, weatherLayer, firePerimeterLayer, sensorLayer],
};

/** Runs `body` under a fixed IANA zone so local-midnight bugs surface as diffs. */
function withTimeZone<T>(timeZone: string, body: () => T): T {
  const previous = process.env.TZ;
  process.env.TZ = timeZone;
  try {
    return body();
  } finally {
    if (previous === undefined) delete process.env.TZ;
    else process.env.TZ = previous;
  }
}

/**
 * One axis per layer. There is no whole-warehouse axis any more: it took its left end from the
 * earliest observation anywhere and its right end from the longest horizon anywhere, so it could
 * never answer "how far back does THIS resource go?" -- which is the only question a row's own
 * slider asks.
 */
describe("sliderDomain", () => {
  it("draws the layer's own record, so two layers get two different axes", () => {
    expect(sliderDomain(capabilities, "vegetation")).toEqual({
      firstDay: "2015-06-01",
      today: SERVER_CURRENT_DATE,
      lastDay: addDays(SERVER_CURRENT_DATE, 10),
    });
    // Both ends move with the layer: fire-perimeters starts three years later and ends today.
    expect(sliderDomain(capabilities, "fire-perimeters")).toEqual({
      firstDay: "2018-07-04",
      today: SERVER_CURRENT_DATE,
      lastDay: SERVER_CURRENT_DATE,
    });
  });

  it("derives both ends from the payload, so a different payload moves both", () => {
    const shallowHistory: SliderCapabilities = {
      serverCurrentDate: SERVER_CURRENT_DATE,
      futureAxisDays: 0,
      streamsUnavailable: false,
      layers: [
        { ...vegetationLayer, earliestObservedDate: "2018-02-20", forecastHorizonDays: 3 },
      ],
    };

    const deep = sliderDomain(capabilities, "vegetation");
    const shallow = sliderDomain(shallowHistory, "vegetation");

    expect(deep).toEqual({
      firstDay: "2015-06-01",
      today: "2019-03-07",
      lastDay: "2019-03-17",
    });
    expect(shallow).toEqual({
      firstDay: "2018-02-20",
      today: "2019-03-07",
      lastDay: "2019-03-10",
    });
    // Both ends must differ between payloads; a hardcoded domain cannot satisfy this.
    expect(shallow?.firstDay).not.toBe(deep?.firstDay);
    expect(shallow?.lastDay).not.toBe(deep?.lastDay);
  });

  it("takes lastDay from this layer's horizon, never from the longest one on the map", () => {
    // weather-observations forecasts 30 days and vegetation 10. Under the old whole-warehouse
    // axis vegetation's track ended 30 days out, on a horizon belonging to another layer.
    expect(sliderDomain(capabilities, "vegetation")?.lastDay).toBe(
      addDays(SERVER_CURRENT_DATE, 10)
    );
    expect(sliderDomain(capabilities, "weather-observations")?.lastDay).toBe(
      addDays(SERVER_CURRENT_DATE, 30)
    );
  });

  it("extends every row past today by futureAxisDays when the layer forecasts nothing", () => {
    // Production's exact shape: every horizon 0. Without the padding a row would end exactly at
    // today and draw no observed/future boundary at all, so "the record stops here" would again
    // be a fact stated only in words.
    const nothingForecasts: SliderCapabilities = {
      serverCurrentDate: SERVER_CURRENT_DATE,
      futureAxisDays: 30,
      streamsUnavailable: false,
      layers: [{ ...vegetationLayer, forecastHorizonDays: 0, forecastVariants: [] }],
    };

    expect(sliderDomain(nothingForecasts, "vegetation")?.lastDay).toBe(
      addDays(SERVER_CURRENT_DATE, 30)
    );
    expect(sliderDomain({ ...nothingForecasts, futureAxisDays: 0 }, "vegetation")?.lastDay).toBe(
      SERVER_CURRENT_DATE
    );
  });

  it("keeps a forecast horizon that reaches further than the drawn axis span", () => {
    // The two spans are different claims -- how far a layer can be ANSWERED for, and how far
    // its track is DRAWN -- so the axis must contain the horizon rather than truncate it.
    const longHorizon: SliderCapabilities = { ...capabilities, futureAxisDays: 5 };
    expect(sliderDomain(longHorizon, "weather-observations")?.lastDay).toBe(
      addDays(SERVER_CURRENT_DATE, 30)
    );
  });

  it("refuses a snapshot's publication date as an axis, however real that date is", () => {
    // `watersheds` persisted 9,396 HUC12 basins whose WBD loaddate is 2013-01-18 for 96% of
    // them. Ranging its row over those years would advertise six years of scrubbing across a
    // boundary set that draws identically on every one of them.
    const withWatersheds: SliderCapabilities = {
      ...capabilities,
      layers: [
        ...capabilities.layers,
        { ...sensorLayer, layerName: "watersheds", earliestObservedDate: "2013-01-18" },
      ],
    };
    expect(sliderDomain(withWatersheds, "watersheds")).toBeNull();

    // The same date on an EVENT layer is a different claim -- things really did happen on those
    // days -- and does range the axis, which makes this a snapshot rule and not a rule about
    // old dates.
    const withOldEvents: SliderCapabilities = {
      ...capabilities,
      layers: [{ ...firePerimeterLayer, earliestObservedDate: "2013-01-18" }],
    };
    expect(sliderDomain(withOldEvents, "fire-perimeters")?.firstDay).toBe("2013-01-18");
  });

  it("returns null rather than borrowing another layer's ends", () => {
    // Not in this payload at all: a stale toggle must draw no track, never someone else's.
    expect(sliderDomain(capabilities, "evacuation-zones")).toBeNull();
    // Published, but has observed nothing -- there is no record to range over.
    expect(sliderDomain(capabilities, "sensors")).toBeNull();
    expect(sliderDomain(null, "vegetation")).toBeNull();
    // An absurd payload whose first observed day is after the server's today would put firstDay
    // past lastDay and invert every offset on the track.
    const observedInTheFuture: SliderCapabilities = {
      ...capabilities,
      layers: [{ ...vegetationLayer, earliestObservedDate: "2020-01-01" }],
    };
    expect(sliderDomain(observedInTheFuture, "vegetation")).toBeNull();
  });

  it("refuses a negative or absent futureAxisDays rather than inverting the axis", () => {
    // A payload from a server that predates the field, and a malformed one. Either would put
    // lastDay before today and make every offset on the track negative.
    const absent = { ...capabilities, futureAxisDays: undefined } as unknown as SliderCapabilities;
    expect(sliderDomain(absent, "weather-observations")?.lastDay).toBe(
      addDays(SERVER_CURRENT_DATE, 30)
    );
    expect(
      sliderDomain({ ...capabilities, futureAxisDays: -10 }, "weather-observations")?.lastDay
    ).toBe(addDays(SERVER_CURRENT_DATE, 30));
  });

  it("exposes integer offsets for the thumb and the hatched region", () => {
    const domain = sliderDomain(capabilities, "vegetation");
    if (domain === null) throw new Error("expected a domain");
    expect(todayOffset(domain)).toBe(dayOffset("2015-06-01", SERVER_CURRENT_DATE));
    expect(sliderMaxOffset(domain)).toBe(todayOffset(domain) + 10);
    expect(Number.isInteger(sliderMaxOffset(domain))).toBe(true);
  });

  it("ends a zero-horizon layer's axis at today, drawing no future band", () => {
    const domain = sliderDomain(capabilities, "fire-perimeters");
    if (domain === null) throw new Error("expected a domain");
    // todayOffset === maxOffset is what the render reads as "no future band at all".
    expect(todayOffset(domain)).toBe(sliderMaxOffset(domain));
  });
});

describe("isFutureDate", () => {
  it("compares against the server's date, never the machine's", () => {
    // Every date here is years in the past for the test runner's own clock.
    expect(isFutureDate("2019-03-08", capabilities)).toBe(true);
    expect(isFutureDate("2019-04-01", capabilities)).toBe(true);
    expect(isFutureDate(SERVER_CURRENT_DATE, capabilities)).toBe(false);
    expect(isFutureDate("2019-03-06", capabilities)).toBe(false);
  });

  it("selects the observed series up to today and the forecast series after", () => {
    expect(resolveVariant(SERVER_CURRENT_DATE, capabilities, "ml")).toBe("observed");
    expect(resolveVariant("2019-03-06", capabilities, "ml")).toBe("observed");
    expect(resolveVariant("2019-03-08", capabilities, "ml")).toBe("ml");
    expect(resolveVariant("2019-03-08", capabilities, "monte_carlo")).toBe("monte_carlo");
  });
});

describe("layerAvailabilityAt", () => {
  it("returns not_forecastable for an event layer one day after the server's today", () => {
    expect(
      layerAvailabilityAt(firePerimeterLayer, "2019-03-08", "monte_carlo", capabilities)
    ).toBe("not_forecastable");
    // The same layer at the server's today is perfectly publishable.
    expect(
      layerAvailabilityAt(firePerimeterLayer, SERVER_CURRENT_DATE, "monte_carlo", capabilities)
    ).toBe("published");
  });

  it("returns not_forecastable for a non-event layer with a zero-day horizon", () => {
    const snapshotOnly: SliderLayerCapability = { ...vegetationLayer, forecastHorizonDays: 0 };
    expect(layerAvailabilityAt(snapshotOnly, "2019-03-08", "monte_carlo", capabilities)).toBe(
      "not_forecastable"
    );
  });

  it("returns not_yet_observed before the layer's earliest version", () => {
    expect(
      layerAvailabilityAt(firePerimeterLayer, "2018-07-03", "monte_carlo", capabilities)
    ).toBe("not_yet_observed");
    expect(
      layerAvailabilityAt(firePerimeterLayer, "2018-07-04", "monte_carlo", capabilities)
    ).toBe("published");
    // A layer with no versions at all has observed nothing on any date.
    expect(layerAvailabilityAt(sensorLayer, "2019-03-06", "monte_carlo", capabilities)).toBe(
      "not_yet_observed"
    );
  });

  it("returns beyond_horizon only past the layer's own horizon", () => {
    expect(layerAvailabilityAt(weatherLayer, "2019-04-06", "ml", capabilities)).toBe("published");
    expect(layerAvailabilityAt(weatherLayer, "2019-04-07", "ml", capabilities)).toBe(
      "beyond_horizon"
    );
    expect(layerAvailabilityAt(vegetationLayer, "2019-03-18", "monte_carlo", capabilities)).toBe(
      "beyond_horizon"
    );
  });

  it("returns variant_unavailable when the layer does not publish that variant", () => {
    expect(layerAvailabilityAt(vegetationLayer, "2019-03-09", "ml", capabilities)).toBe(
      "variant_unavailable"
    );
    expect(layerAvailabilityAt(vegetationLayer, "2019-03-09", "monte_carlo", capabilities)).toBe(
      "published"
    );
    // A missing variant in the past is irrelevant; observations have no variant.
    expect(layerAvailabilityAt(vegetationLayer, "2019-03-06", "ml", capabilities)).toBe(
      "published"
    );
  });

  it("prefers the most specific reason when several apply", () => {
    // Before the earliest version AND outside every horizon: history wins.
    expect(layerAvailabilityAt(weatherLayer, "2016-01-01", "ml", capabilities)).toBe(
      "not_yet_observed"
    );
    // Event layer far past its horizon still reads as not forecastable, not beyond horizon.
    expect(layerAvailabilityAt(firePerimeterLayer, "2020-01-01", "ml", capabilities)).toBe(
      "not_forecastable"
    );
  });
});

describe("isWithinCoverageGap", () => {
  it("closes a gap at both ends, so a one-day hole is a hole", () => {
    const stalled: SliderLayerCapability = {
      ...vegetationLayer,
      coverageGaps: [
        { from: "2018-01-10", to: "2018-01-10" },
        { from: "2018-06-01", to: "2018-06-30" },
      ],
    };
    expect(isWithinCoverageGap(stalled, "2018-01-10")).toBe(true);
    expect(isWithinCoverageGap(stalled, "2018-01-09")).toBe(false);
    expect(isWithinCoverageGap(stalled, "2018-06-01")).toBe(true);
    expect(isWithinCoverageGap(stalled, "2018-06-30")).toBe(true);
    expect(isWithinCoverageGap(stalled, "2018-07-01")).toBe(false);
  });

  it("reads a payload with no gap list as no gap known, not as a crash", () => {
    // A server that predates the field sends no array at all.
    const olderPayload = { ...vegetationLayer, coverageGaps: undefined } as unknown as
      SliderLayerCapability;
    expect(isWithinCoverageGap(olderPayload, "2018-01-10")).toBe(false);
  });
});

describe("isDayDescribed -- a capped range list stops being evidence below its boundary", () => {
  it("reports every day undescribed below describedFromDay, so silence is not a claim", () => {
    // The server dropped the older gaps to stay inside MAX_REPORTED_DAY_RANGES and reported
    // the oldest day the surviving ranges still cover. A day below it may sit inside a gap
    // that was dropped, so its absence from coverageGaps says nothing at all.
    const truncated: SliderLayerCapability = {
      ...vegetationLayer,
      coverageGaps: [{ from: "2018-06-01", to: "2018-06-30" }],
      describedFromDay: "2018-06-01",
    };

    expect(isWithinCoverageGap(truncated, "2016-04-09")).toBe(false);
    // ...and that `false` is exactly what must never be read as "published on 2016-04-09".
    expect(isDayDescribed(truncated, "2016-04-09")).toBe(false);
    expect(isDayDescribed(truncated, "2018-05-31")).toBe(false);
    expect(isDayDescribed(truncated, "2018-06-01")).toBe(true);
    expect(isDayDescribed(truncated, "2019-01-01")).toBe(true);
  });

  it("describes the whole axis when nothing was dropped", () => {
    expect(vegetationLayer.describedFromDay).toBeNull();
    expect(isDayDescribed(vegetationLayer, "2015-06-01")).toBe(true);
    expect(isDayDescribed(vegetationLayer, "2019-02-20")).toBe(true);
  });

  it("reads a payload with no boundary as fully described, not as a crash", () => {
    // A server that predates the field sends no boundary at all, and meant the whole axis.
    const olderPayload = { ...vegetationLayer, describedFromDay: undefined } as unknown as
      SliderLayerCapability;
    expect(isDayDescribed(olderPayload, "2016-04-09")).toBe(true);
  });
});

describe("hasSelectableDay -- one rule decides both the control and the filter", () => {
  it("is true exactly for a toggle whose stream defines an axis", () => {
    expect(hasSelectableDay(capabilities, "vegetation")).toBe(true);
    expect(hasSelectableDay(capabilities, "weather")).toBe(true);
    expect(hasSelectableDay(capabilities, "fire-perimeters")).toBe(true);
  });

  it("is false for a snapshot, which carries a publication date rather than an axis", () => {
    expect(findLayerCapability(capabilities, "sensors")?.temporalKind).toBe("snapshot");
    expect(hasSelectableDay(capabilities, "sensors")).toBe(false);
  });

  it("is false for a toggle that names no warehouse stream at all", () => {
    // The SoilGrids raster and the SSURGO viewport proxy are drawn from upstream, not from a
    // stream the warehouse dates.
    expect(hasSelectableDay(capabilities, "soil")).toBe(false);
    expect(hasSelectableDay(capabilities, "soil-survey")).toBe(false);
  });

  it("is false for a stream this payload does not carry, and while nothing has loaded", () => {
    // `fire` maps to fire-detections, which is absent from these capabilities.
    expect(hasSelectableDay(capabilities, "fire")).toBe(false);
    expect(hasSelectableDay(null, "vegetation")).toBe(false);
  });

  it("is false for a stream that has published nothing, or whose record starts after today", () => {
    const unpublished: SliderLayerCapability = { ...vegetationLayer, earliestObservedDate: null };
    const startsInTheFuture: SliderLayerCapability = {
      ...vegetationLayer,
      earliestObservedDate: "2019-03-08",
    };

    expect(
      hasSelectableDay({ ...capabilities, layers: [unpublished] }, "vegetation")
    ).toBe(false);
    expect(
      hasSelectableDay({ ...capabilities, layers: [startsInTheFuture] }, "vegetation")
    ).toBe(false);
  });

  it("agrees with sliderDomain for EVERY toggle, so a control and a filter cannot drift", () => {
    // The finding this predicate closes: whether a layer got a control was snapshot-aware
    // while whether its read was date-filtered was snapshot-blind. Both sides ask this
    // function now, so the two answers are the same answer by construction.
    for (const layerId of Object.keys(LAYER_REGISTRY) as LayerToggleId[]) {
      const layerName = warehouseLayerNameFor(layerId);
      const hasAxis = layerName !== null && sliderDomain(capabilities, layerName) !== null;
      expect({ layerId, selectable: hasSelectableDay(capabilities, layerId) }).toEqual({
        layerId,
        selectable: hasAxis,
      });
    }
  });

  it("gives the five model-plane and drought toggles a day once their streams are published", () => {
    // Before these streams were published they carried no capability, so they had no axis, no
    // control and no filter -- and resolveLayerDate pinned every one of them to the server's
    // today even though all five readers accept a historical day.
    const streamLayers: SliderLayerCapability[] = [
      SLIDER_STREAM_LAYER_NAMES.drought,
      SLIDER_STREAM_LAYER_NAMES.soilMoisture,
      SLIDER_STREAM_LAYER_NAMES.soilTemperature,
      SLIDER_STREAM_LAYER_NAMES.soilVapourPressureDeficit,
      // One climate signal stands for the nine: they are formed by `climateFieldStreamName`
      // rather than listed, so a stream that resolves for one resolves for all.
      climateFieldStreamName("air-temperature"),
    ].map((layerName) => ({ ...vegetationLayer, layerName }));
    const withStreams: SliderCapabilities = {
      ...capabilities,
      layers: [...capabilities.layers, ...streamLayers],
    };

    // Asserted on the stream names directly, so this test states what THIS module owns
    // whatever the registry currently points at: a stream capability defines an axis exactly
    // as a geo.layers one does.
    for (const layerName of streamLayers.map((layer) => layer.layerName)) {
      expect({ layerName, axis: sliderDomain(withStreams, layerName) !== null }).toEqual({
        layerName,
        axis: true,
      });
    }

    // And once the registry names them, the toggles get a day. Skipped rather than failed
    // while `warehouseLayerName` is still null: that half of the fix lives in layer-registry.ts.
    const wiredToggles = (
      [
        "drought",
        "soil-moisture",
        "soil-temperature",
        "soil-vpd",
        climateFieldToggleId("air-temperature"),
      ] as LayerToggleId[]
    ).filter((layerId) => warehouseLayerNameFor(layerId) !== null);
    expect(
      wiredToggles.filter((layerId) => !hasSelectableDay(withStreams, layerId))
    ).toEqual([]);
  });
});

describe("day arithmetic", () => {
  it("crosses month and year boundaries in UTC", () => {
    expect(addDays("2019-02-28", 1)).toBe("2019-03-01");
    expect(addDays("2020-02-28", 1)).toBe("2020-02-29");
    expect(addDays("2019-12-31", 1)).toBe("2020-01-01");
    expect(addDays("2019-03-01", -1)).toBe("2019-02-28");
    expect(dayOffset("2019-02-28", "2019-03-01")).toBe(1);
    expect(dayOffset("2019-03-07", "2019-04-06")).toBe(30);
    expect(dayOffset("2019-04-06", "2019-03-07")).toBe(-30);
  });

  it("gives the same answer across DST boundaries in any local zone", () => {
    const zones = ["UTC", "America/Los_Angeles", "Pacific/Kiritimati", "Pacific/Niue"];
    for (const zone of zones) {
      // 2019-03-10 is the US spring-forward; 2019-11-03 is the fall-back.
      expect(withTimeZone(zone, () => addDays("2019-03-09", 2))).toBe("2019-03-11");
      expect(withTimeZone(zone, () => addDays("2019-11-02", 2))).toBe("2019-11-04");
      expect(withTimeZone(zone, () => addDays("2019-10-26", 2))).toBe("2019-10-28");
      expect(withTimeZone(zone, () => dayOffset("2019-03-09", "2019-03-11"))).toBe(2);
      expect(withTimeZone(zone, () => dayOffset("2019-11-02", "2019-11-04"))).toBe(2);
    }
  });

  it("round-trips offsets so slider positions stay integers", () => {
    for (let offset = -400; offset <= 30; offset += 37) {
      expect(dayOffset("2019-03-07", addDays("2019-03-07", offset))).toBe(offset);
    }
  });

  it("leaves non-calendar strings alone instead of inventing a date", () => {
    expect(addDays(UNINITIALIZED_DATE, 1)).toBe(UNINITIALIZED_DATE);
    expect(dayOffset(UNINITIALIZED_DATE, "2019-03-07")).toBe(0);
    expect(clampDateToDomain(UNINITIALIZED_DATE, capabilities, "vegetation")).toBe(
      UNINITIALIZED_DATE
    );
  });
});

describe("clampDateToDomain", () => {
  it("holds a date inside that layer's own axis", () => {
    expect(clampDateToDomain("2010-01-01", capabilities, "vegetation")).toBe("2015-06-01");
    expect(clampDateToDomain("2019-12-31", capabilities, "vegetation")).toBe("2019-03-17");
    expect(clampDateToDomain("2018-08-08", capabilities, "vegetation")).toBe("2018-08-08");
  });

  it("clamps each layer to its own ends, not to a shared pair", () => {
    // 2016-04-09 is inside vegetation's axis and years before fire-perimeters observed
    // anything. One shared clamp would leave one of the two thumbs off its own track.
    expect(clampDateToDomain("2016-04-09", capabilities, "vegetation")).toBe("2016-04-09");
    expect(clampDateToDomain("2016-04-09", capabilities, "fire-perimeters")).toBe("2018-07-04");
    // Past a zero-horizon layer's right end, which is the server's today.
    expect(clampDateToDomain("2019-04-01", capabilities, "fire-perimeters")).toBe(
      SERVER_CURRENT_DATE
    );
  });

  it("returns the date untouched when the layer has no axis at all", () => {
    // A snapshot, an unknown layer and a missing payload are all "nothing to clamp to", and
    // inventing ends for them would move a day nobody could see the reason for.
    expect(clampDateToDomain("2016-04-09", capabilities, "sensors")).toBe("2016-04-09");
    expect(clampDateToDomain("2016-04-09", capabilities, "evacuation-zones")).toBe("2016-04-09");
    expect(clampDateToDomain("2016-04-09", null, "vegetation")).toBe("2016-04-09");
  });
});

describe("findLayerCapability", () => {
  it("looks layers up by their geo.layers name", () => {
    expect(findLayerCapability(capabilities, "fire-perimeters")).toBe(firePerimeterLayer);
    expect(findLayerCapability(capabilities, "evacuation-zones")).toBeNull();
    expect(findLayerCapability(null, "vegetation")).toBeNull();
  });
});

/**
 * The default day. This is the whole reason per-layer dates are worth having: every layer used
 * to share "today", and since only vegetation has years of depth most layers rendered
 * correctly-but-confusingly empty. Following each layer's OWN newest day opens every layer on
 * data instead of on a hole.
 */
describe("resolveLayerDate", () => {
  it("resolves an absent entry to that layer's own latest observed day", () => {
    expect(resolveLayerDate({}, capabilities, "vegetation")).toBe("2019-02-20");
    expect(resolveLayerDate({}, capabilities, "weather")).toBe("2019-03-06");
    expect(resolveLayerDate({}, capabilities, "fire-perimeters")).toBe("2019-03-01");
    // Three layers, three different days, with nothing stored for any of them.
    expect(latestObservedDateFor(capabilities, "vegetation")).toBe("2019-02-20");
  });

  it("prefers a layer's own stored day over its latest", () => {
    expect(resolveLayerDate({ vegetation: "2016-04-09" }, capabilities, "vegetation")).toBe(
      "2016-04-09"
    );
    // ...and leaves every other layer on its own default.
    expect(resolveLayerDate({ vegetation: "2016-04-09" }, capabilities, "weather")).toBe(
      "2019-03-06"
    );
  });

  it("falls back to the server's today for a layer whose latest nobody knows", () => {
    // `drought` has no geo.layers row at all -- it lives in geo.drought_areas -- and `sensors`
    // has published nothing. Today is the only day nameable without reading the browser clock,
    // and it is the day both layers already drew before per-layer dates existed.
    expect(latestObservedDateFor(capabilities, "drought")).toBeNull();
    expect(resolveLayerDate({}, capabilities, "drought")).toBe(SERVER_CURRENT_DATE);
    expect(resolveLayerDate({}, capabilities, "sensors")).toBe(SERVER_CURRENT_DATE);
  });

  it("names no day at all before capabilities arrive", () => {
    // Not a browser-clock guess: UNINITIALIZED_DATE is not a calendar date, so every consumer
    // reports "no day" rather than a day it invented.
    expect(resolveLayerDate({}, null, "vegetation")).toBe(UNINITIALIZED_DATE);
  });
});

describe("readPersistedLayerDates", () => {
  it("drops a legacy global selectedDate instead of pinning every layer to it", () => {
    // The old shape carried ONE day for the whole map. Fanning it out would pin every layer to
    // a stale day -- the exact state this feature removes -- and it would arrive as an override
    // nobody set. Dropping it returns every layer to following its own newest day.
    expect(readPersistedLayerDates({ selectedDate: "2016-04-09" })).toEqual({});
    expect(
      readPersistedLayerDates({ selectedDate: "2016-04-09", focusedLayerName: "vegetation" })
    ).toEqual({});
  });

  it("keeps only well-formed days for layers that still exist", () => {
    expect(
      readPersistedLayerDates({
        selectedDate: "2016-04-09",
        layerDates: {
          vegetation: "2018-08-08",
          weather: "not-a-date",
          "a-layer-that-was-removed": "2018-08-08",
          drought: 20180808,
        },
      })
    ).toEqual({ vegetation: "2018-08-08" });
  });

  it("treats anything that is not a blob of per-layer days as nothing stored", () => {
    expect(readPersistedLayerDates(undefined)).toEqual({});
    expect(readPersistedLayerDates(null)).toEqual({});
    expect(readPersistedLayerDates("2016-04-09")).toEqual({});
    expect(readPersistedLayerDates({ layerDates: "2016-04-09" })).toEqual({});
  });
});

describe("useTimeSliderStore", () => {
  beforeEach(() => {
    useTimeSliderStore.setState({
      layerDates: {},
      forecastVariant: "monte_carlo",
      capabilities: null,
      capabilitiesUnavailable: false,
    });
  });

  it("starts with no layer pinned to any day", () => {
    const { result } = renderHook(() => useTimeSliderStore());
    expect(result.current.layerDates).toEqual({});
    expect(result.current.capabilities).toBeNull();
    expect(result.current.forecastVariant).toBe("monte_carlo");
  });

  it("does not populate a single layer date when capabilities arrive", () => {
    // An eager copy is correct for exactly one payload: the next refresh moves a layer's newest
    // day and the copied value has silently become an override, pinning the layer behind the
    // live edge with nobody having touched its slider.
    const { result } = renderHook(() => useTimeSliderStore());

    act(() => {
      result.current.setCapabilities(capabilities);
    });

    expect(result.current.layerDates).toEqual({});
    expect(resolveLayerDate(result.current.layerDates, capabilities, "vegetation")).toBe(
      "2019-02-20"
    );
  });

  it("keeps the sparse default following each layer's newest day across a refresh", () => {
    const { result } = renderHook(() => useTimeSliderStore());

    act(() => {
      result.current.setCapabilities(capabilities);
    });
    expect(resolveLayerDate(result.current.layerDates, result.current.capabilities, "vegetation"))
      .toBe("2019-02-20");

    // An ingest run lands three more days of vegetation. Nothing was stored for it, so it must
    // move with the record rather than staying on the day it happened to open on.
    const refreshed: SliderCapabilities = {
      ...capabilities,
      layers: [
        { ...vegetationLayer, latestObservedDate: "2019-02-23" },
        weatherLayer,
        firePerimeterLayer,
        sensorLayer,
      ],
    };
    act(() => {
      result.current.setCapabilities(refreshed);
    });

    expect(result.current.layerDates).toEqual({});
    expect(resolveLayerDate(result.current.layerDates, result.current.capabilities, "vegetation"))
      .toBe("2019-02-23");
  });

  it("setLayerDate moves one layer and leaves every other layer alone", () => {
    const { result } = renderHook(() => useTimeSliderStore());

    act(() => {
      result.current.setCapabilities(capabilities);
      result.current.setLayerDate("vegetation", "2018-08-08");
    });

    expect(result.current.layerDates).toEqual({ vegetation: "2018-08-08" });
    const readDay = (layerId: LayerToggleId) =>
      resolveLayerDate(result.current.layerDates, result.current.capabilities, layerId);
    expect(readDay("vegetation")).toBe("2018-08-08");
    // The mixed-time composite the owner asked for: fire and weather did not move.
    expect(readDay("weather")).toBe("2019-03-06");
    expect(readDay("fire-perimeters")).toBe("2019-03-01");

    act(() => {
      result.current.setLayerDate("fire-perimeters", "2018-09-09");
    });
    expect(result.current.layerDates).toEqual({
      vegetation: "2018-08-08",
      "fire-perimeters": "2018-09-09",
    });
    expect(readDay("vegetation")).toBe("2018-08-08");
  });

  it("setLayerDate clamps to that layer's own axis, not to any other layer's", () => {
    const { result } = renderHook(() => useTimeSliderStore());

    act(() => {
      result.current.setCapabilities(capabilities);
      // Years before fire-perimeters observed anything, but well inside vegetation's record.
      result.current.setLayerDate("fire-perimeters", "2016-04-09");
      result.current.setLayerDate("vegetation", "2016-04-09");
    });

    expect(result.current.layerDates["fire-perimeters"]).toBe("2018-07-04");
    expect(result.current.layerDates.vegetation).toBe("2016-04-09");
  });

  it("setLayerDate on a layer with no axis stores the day rather than inventing ends", () => {
    const { result } = renderHook(() => useTimeSliderStore());

    act(() => {
      result.current.setCapabilities(capabilities);
      result.current.setLayerDate("drought", "2016-04-09");
    });

    // `drought` has no geo.layers row, so there is no record to clamp against; making one up
    // would move a day for a reason the row could not show.
    expect(result.current.layerDates.drought).toBe("2016-04-09");
  });

  it("setLayerDate refuses a day that is not a day by returning the layer to its latest", () => {
    const { result } = renderHook(() => useTimeSliderStore());

    act(() => {
      result.current.setCapabilities(capabilities);
      result.current.setLayerDate("vegetation", "2018-08-08");
    });
    expect(result.current.layerDates.vegetation).toBe("2018-08-08");

    act(() => {
      result.current.setLayerDate("vegetation", UNINITIALIZED_DATE);
    });

    // Storing it would blank the layer, which on the map is indistinguishable from a gap in the
    // record; following its own newest day is a state the row can actually show.
    expect(result.current.layerDates.vegetation).toBeUndefined();
    expect(resolveLayerDate(result.current.layerDates, result.current.capabilities, "vegetation"))
      .toBe("2019-02-20");
  });

  it("resetLayerDate deletes the entry so the layer tracks the live edge again", () => {
    const { result } = renderHook(() => useTimeSliderStore());

    act(() => {
      result.current.setCapabilities(capabilities);
      result.current.setLayerDate("vegetation", "2019-02-20");
      result.current.setLayerDate("weather", "2018-08-08");
    });
    // Pinned to a day that happens to BE its latest -- still an override, and still different
    // from following: only the second keeps moving as later payloads land.
    expect(result.current.layerDates.vegetation).toBe("2019-02-20");

    act(() => {
      result.current.resetLayerDate("vegetation");
      result.current.setCapabilities({
        ...capabilities,
        layers: [
          { ...vegetationLayer, latestObservedDate: "2019-02-25" },
          weatherLayer,
          firePerimeterLayer,
          sensorLayer,
        ],
      });
    });

    expect(result.current.layerDates).toEqual({ weather: "2018-08-08" });
    expect(resolveLayerDate(result.current.layerDates, result.current.capabilities, "vegetation"))
      .toBe("2019-02-25");
  });

  it("resetAllLayerDates returns every layer to its own newest day", () => {
    const { result } = renderHook(() => useTimeSliderStore());

    act(() => {
      result.current.setCapabilities(capabilities);
      result.current.setLayerDate("vegetation", "2018-08-08");
      result.current.setLayerDate("weather", "2018-08-08");
    });
    act(() => {
      result.current.resetAllLayerDates();
    });

    expect(result.current.layerDates).toEqual({});
    const readDay = (layerId: LayerToggleId) =>
      resolveLayerDate(result.current.layerDates, result.current.capabilities, layerId);
    expect(readDay("vegetation")).toBe("2019-02-20");
    expect(readDay("weather")).toBe("2019-03-06");
  });

  it("setCapabilities clamps a stored day whose axis moved under it", () => {
    const { result } = renderHook(() => useTimeSliderStore());

    act(() => {
      result.current.setCapabilities(capabilities);
      result.current.setLayerDate("weather", "2017-06-01");
    });
    expect(result.current.layerDates.weather).toBe("2017-06-01");

    // A refreshed payload moves weather-observations' record forward past the stored day.
    act(() => {
      result.current.setCapabilities({
        ...capabilities,
        layers: [
          vegetationLayer,
          { ...weatherLayer, earliestObservedDate: "2018-01-15" },
          firePerimeterLayer,
          sensorLayer,
        ],
      });
    });

    expect(result.current.layerDates.weather).toBe("2018-01-15");
  });

  it("setForecastVariant switches the forecast series", () => {
    const { result } = renderHook(() => useTimeSliderStore());

    act(() => {
      result.current.setForecastVariant("ml");
    });

    expect(result.current.forecastVariant).toBe("ml");
  });

  it("hydrates a returning user's old single-date blob without breaking any layer", () => {
    // The shape a build before per-layer dates would have written. It must not deserialize into
    // state at all -- and must not throw on the way past.
    const { result } = renderHook(() => useTimeSliderStore());

    act(() => {
      result.current.setCapabilities(capabilities);
      result.current.hydratePersistedLayerDates({
        selectedDate: "2016-04-09",
        forecastVariant: "ml",
        focusedLayerName: "vegetation",
      });
    });

    expect(result.current.layerDates).toEqual({});
    const readDay = (layerId: LayerToggleId) =>
      resolveLayerDate(result.current.layerDates, result.current.capabilities, layerId);
    expect(readDay("vegetation")).toBe("2019-02-20");
    expect(readDay("weather")).toBe("2019-03-06");
  });

  it("hydrates per-layer days and clamps them to the axes on hand", () => {
    const { result } = renderHook(() => useTimeSliderStore());

    act(() => {
      result.current.setCapabilities(capabilities);
      result.current.hydratePersistedLayerDates({
        layerDates: { vegetation: "2018-08-08", "fire-perimeters": "2016-04-09" },
      });
    });

    expect(result.current.layerDates).toEqual({
      vegetation: "2018-08-08",
      // Before fire-perimeters observed anything: a blob written when its record started
      // earlier must not leave the thumb off its own track.
      "fire-perimeters": "2018-07-04",
    });
  });
});
