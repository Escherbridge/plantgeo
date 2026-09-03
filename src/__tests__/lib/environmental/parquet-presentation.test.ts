import { describe, expect, it } from "vitest";
import {
  presentParquetDrought,
  presentParquetVegetation,
  presentParquetWater,
  presentParquetWeather,
} from "@/lib/environmental/parquet-presentation";
import type { AggregateEnvelopeSupport } from "@/lib/map/layer-render-contract";
import type { ZoomTier } from "@/lib/map/zoom-tiers";
import type {
  ParquetDroughtArea,
  ParquetReaderResult,
  ParquetVegetationWindow,
  ParquetWaterGauge,
  ParquetWeatherObservation,
} from "@/lib/server/services/parquet-trpc-readers";

/** A declared envelope; each test overrides only the fields it is about. */
function support(
  zoomTier: ZoomTier,
  overrides: Partial<AggregateEnvelopeSupport> = {}
): AggregateEnvelopeSupport {
  return {
    zoomTier,
    supportKind: zoomTier === 13 ? "raw_point" : "aggregate_cell",
    supportId: "support-" + zoomTier,
    origin: "cell_origin",
    cellWidthDegrees: 0.25,
    cellHeightDegrees: 0.25,
    aggregationMethod: zoomTier === 13 ? "none" : "mean",
    contributorCount: zoomTier === 13 ? 1 : 3,
    provenance: {
      sourceLayer: "water_gauges",
      observedDay: "2026-08-28",
      newestObservedAt: "2026-08-28T12:00:00Z",
      attribution: "USGS NWIS",
    },
    ...overrides,
  };
}

function waterRow(overrides: Partial<ParquetWaterGauge>): ParquetWaterGauge {
  return {
    siteNumber: "13172500",
    observedAt: "2026-08-28T12:00:00Z",
    observedDay: "2026-08-28",
    siteName: "Boise River",
    latitude: 43.6,
    longitude: -116.2,
    flowCfs: 500,
    percentile: null,
    condition: "unknown",
    trend: null,
    source: "USGS NWIS",
    geometryLinked: true,
    dataAvailableAt: null,
    ingestedAt: "2026-08-28T12:05:00Z",
    support: support(13),
    ...overrides,
  };
}

describe("Parquet browser presentation", () => {
  it("keeps named gauges, anonymous coarse cells, and unlocated rows distinct", () => {
    const result: ParquetReaderResult<readonly ParquetWaterGauge[]> = {
      state: "ready",
      requestedDay: "2026-08-28",
      servedDay: "2026-08-28",
      truncated: false,
      data: [
        waterRow({}),
        waterRow({
          siteNumber: null,
          siteName: null,
          latitude: 43.125,
          longitude: -116.125,
          support: support(9),
        }),
        waterRow({ latitude: null, longitude: null, geometryLinked: false }),
      ],
    };

    const presented = presentParquetWater(result);
    expect(presented.gauges.map((gauge) => gauge.siteNo)).toEqual(["13172500"]);
    expect(presented.cells).toEqual([
      expect.objectContaining({ latitude: 43.125, longitude: -116.125, flowCfs: 500 }),
    ]);
    expect(presented.cells[0].support.supportKind).toBe("aggregate_cell");
    expect(presented.unlocatedRows).toBe(1);
  });

  it("splits on the DECLARED form, not on whether a site number happens to be present", () => {
    // The rule this replaced -- "a null site number means aggregate" -- is the inference from a
    // missing id that the render contract exists to end. A coarse rung whose rows happen to
    // carry site numbers was indistinguishable from real z13 gauges under it.
    const result: ParquetReaderResult<readonly ParquetWaterGauge[]> = {
      state: "ready",
      requestedDay: "2026-08-28",
      servedDay: "2026-08-28",
      truncated: false,
      data: [waterRow({ siteNumber: "13172500", support: support(5) })],
    };

    const presented = presentParquetWater(result);
    expect(presented.gauges).toEqual([]);
    expect(presented.cells).toHaveLength(1);
  });

  it("presents a nameless raw point as a cell rather than as a gauge with no name", () => {
    // The other half of the split, and the half the site number is still load-bearing for: a row
    // may only be drawn as a NAMED gauge if it has a name to draw. `raw_point` with no site
    // number would otherwise be captioned with an empty string.
    //
    // What this case used to assert -- that a row carrying NO envelope stays on the wave-1
    // site-number rule -- is no longer expressible: `support` is required on the browser mirror as
    // of 2026-09-02, precisely so that an envelope-less row cannot silently reach a presenter.
    const result: ParquetReaderResult<readonly ParquetWaterGauge[]> = {
      state: "ready",
      requestedDay: "2026-08-28",
      servedDay: "2026-08-28",
      truncated: false,
      data: [
        waterRow({}),
        waterRow({ siteNumber: null, siteName: null, support: support(13) }),
      ],
    };

    const presented = presentParquetWater(result);
    expect(presented.gauges.map((gauge) => gauge.siteNo)).toEqual(["13172500"]);
    expect(presented.cells).toHaveLength(1);
    expect(presented.cells[0].support.supportKind).toBe("raw_point");
  });

  it("draws vegetation as the 0.25-degree cell its envelope declares", () => {
    const result: ParquetReaderResult<ParquetVegetationWindow> = {
      state: "ready",
      requestedDay: "2026-08-28",
      servedDay: "2026-08-27",
      truncated: false,
      data: {
        firstDay: "2026-07-30",
        lastDay: "2026-08-28",
        days: [],
        observations: [
          {
            cellId: "cell-1",
            gridName: "sentinel-2",
            metricName: "ndvi",
            metricUnit: "index",
            observedDay: "2026-08-27",
            metricValue: 0.61,
            observationChecksum: null,
            dataAvailableAt: "2026-08-28T00:00:00Z",
            releaseCount: 1,
            allowedClientExposure: true,
            longitude: -116.25,
            latitude: 43.5,
            support: support(9, {
              supportKind: "aggregate_cell",
              supportId: "veg-cell-1",
              aggregationMethod: "none",
              contributorCount: 1,
              provenance: {
                sourceLayer: "vegetation_index",
                observedDay: "2026-08-27",
                newestObservedAt: "2026-08-28T00:00:00Z",
                attribution: "Sentinel-2",
              },
            }),
          },
        ],
      },
    };

    const feature = presentParquetVegetation(result).features[0];
    // The square this platform measured, not a dot at its centre. The centre circle was the
    // recorded `raw_point` deviation on LAYER_RENDER_CONTRACT.vegetation, closed here.
    expect(feature.geometry).toEqual({
      type: "Polygon",
      coordinates: [
        [
          [-116.25, 43.5],
          [-116, 43.5],
          [-116, 43.75],
          [-116.25, 43.75],
          [-116.25, 43.5],
        ],
      ],
    });
    expect(feature.properties).toEqual(
      expect.objectContaining({
        ndvi: 0.61,
        // The reader may label the envelope with the generic aggregate form; the DRAWN form is
        // the only one the contract permits for this layer, and the geometry is the same square
        // either way.
        supportKind: "tessellated_cell",
        cellWidthDegrees: 0.25,
        cellHeightDegrees: 0.25,
      })
    );
  });

  it("leaves an observation whose envelope declares no size as a marker, never a 0.25 guess", () => {
    const result: ParquetReaderResult<ParquetVegetationWindow> = {
      state: "ready",
      requestedDay: "2026-08-28",
      servedDay: "2026-08-27",
      truncated: false,
      data: {
        firstDay: "2026-07-30",
        lastDay: "2026-08-28",
        days: [],
        observations: [
          {
            cellId: "cell-2",
            gridName: "sentinel-2",
            metricName: "ndvi",
            metricUnit: "index",
            observedDay: "2026-08-27",
            metricValue: 0.42,
            observationChecksum: null,
            dataAvailableAt: "2026-08-28T00:00:00Z",
            releaseCount: 1,
            allowedClientExposure: true,
            longitude: -116.2,
            latitude: 43.6,
            support: support(9, {
              supportId: "veg-cell-2",
              cellWidthDegrees: undefined,
              cellHeightDegrees: undefined,
            }),
          },
        ],
      },
    };

    // Assuming the contract's declared 0.25 would be the client inferring support again, one
    // indirection further out. VegetationLayer filters to Polygon, so this observation is
    // deliberately left undrawn rather than shown as the dot the deviation named. The envelope
    // itself is required now, so the only way to reach here is for the reader to DECLARE that it
    // has no footprint to state -- never by omitting the envelope and hoping.
    const feature = presentParquetVegetation(result).features[0];
    expect(feature.geometry.type).toBe("Point");
    expect(feature.properties).toEqual(
      expect.objectContaining({ supportKind: null, cellWidthDegrees: null })
    );
  });

  it("carries the weather envelope through to the layer's point vocabulary", () => {
    // The weather lane was the last Parquet read with no envelope on it, which left its dots
    // unable to say whether one of them was a single sampled observation or a coarse-rung mean.
    // The presenter forwards the envelope rather than dropping it, so a caption can.
    const observation: ParquetWeatherObservation = {
      latitude: 43.5,
      longitude: -114.25,
      observedAt: "2026-08-20T18:30:00Z",
      observedDay: "2026-08-20",
      externalId: "station-7",
      temperatureC: 24,
      relativeHumidityPct: 35,
      windSpeedMs: 3,
      windDirectionDeg: 190,
      precipitationMm: 0,
      source: "open-meteo",
      featureId: "new",
      ingestedAt: "2026-08-20T18:35:00Z",
      support: support(13, {
        supportId: "station-7",
        origin: "cell_center",
        cellWidthDegrees: undefined,
        cellHeightDegrees: undefined,
        provenance: {
          sourceLayer: "weather-observations",
          observedDay: "2026-08-20",
          newestObservedAt: "2026-08-20T18:30:00Z",
          attribution: "Open-Meteo",
        },
      }),
    };
    const result: ParquetReaderResult<readonly ParquetWeatherObservation[]> = {
      state: "ready",
      requestedDay: "2026-08-20",
      servedDay: "2026-08-20",
      truncated: false,
      data: [observation],
    };

    const [presented] = presentParquetWeather(result);
    expect(presented).toMatchObject({
      coordinates: [-114.25, 43.5],
      temperature: 24,
      windSpeed: 3,
    });
    // A sampled station is a point and declares no footprint, which is what keeps the layer
    // drawing a dot: `weather` is an `event_point` layer, and the contract permits no square
    // here. Whether the sampling LATTICE behind these points should itself be a declared support
    // is m0's open ruling; nothing here anticipates it.
    expect(presented.support.supportKind).toBe("raw_point");
    expect(presented.support.cellWidthDegrees).toBeUndefined();
    expect(presented.support.provenance.attribution).toBe("Open-Meteo");
  });

  it("preserves the drought release geometry and category in GeoJSON", () => {
    const area: ParquetDroughtArea = {
      areaId: "d2",
      validDate: "2026-08-25",
      droughtCategory: 2,
      sourceUrl: "https://example.test/usdm",
      ingestedAt: "2026-08-26T00:00:00Z",
      geometry: {
        type: "Polygon",
        coordinates: [
          [
            [-117, 43],
            [-116, 43],
            [-116, 44],
            [-117, 43],
          ],
        ],
      },
    };
    const result: ParquetReaderResult<readonly ParquetDroughtArea[]> = {
      state: "ready",
      requestedDay: "2026-08-28",
      servedDay: "2026-08-25",
      truncated: false,
      data: [area],
    };

    const feature = presentParquetDrought(result).features[0];
    expect(feature.geometry).toEqual(area.geometry);
    expect(feature.properties).toEqual(expect.objectContaining({ DM: 2, validDate: "2026-08-25" }));
  });
});
