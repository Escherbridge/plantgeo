import { describe, expect, it } from "vitest";
import {
  presentParquetBurnSeverity,
  presentParquetDrought,
  presentParquetEvacuationZones,
  presentParquetFirePerimeters,
  presentParquetSensorStations,
  presentParquetVegetation,
  presentParquetWater,
  presentParquetWatersheds,
  presentParquetWeather,
  type ParquetBrowserBurnScar,
  type ParquetBrowserEvacuationZone,
  type ParquetBrowserFirePerimeter,
  type ParquetBrowserReaderResult,
  type ParquetBrowserSensorStation,
  type ParquetBrowserWatershed,
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

/*
 * The MVT attribute tables the five cutover presenters rebuild.
 *
 * Every style expression and hover formatter these features feed was written against Martin tiles
 * and is UNCHANGED by the cutover, so the presenters owe those readers the exact vocabulary
 * ST_AsMVT gave them -- including its treatment of a null attribute, which is to omit the key
 * entirely. `["has", "acres"]` and `["!", ["has", "observed_day"]]` both answer differently for a
 * present-and-null key than for an absent one, and both are live in the shipped style.
 */

const POLYGON: GeoJSON.Polygon = {
  type: "Polygon",
  coordinates: [
    [
      [-122.4, 43.9],
      [-122.3, 43.9],
      [-122.3, 44.0],
      [-122.4, 43.9],
    ],
  ],
};

function ready<T>(data: T): ParquetBrowserReaderResult<T> {
  return {
    state: "ready",
    requestedDay: "2026-08-28",
    servedDay: "2026-08-28",
    truncated: false,
    data,
  };
}

function evacuationZone(
  overrides: Partial<ParquetBrowserEvacuationZone> = {}
): ParquetBrowserEvacuationZone {
  return {
    naturalKey: "or-oem:9E0C-1",
    snapshotDay: "2026-08-25",
    evacuationAreaName: "Camp Creek Zone 3",
    fireName: "Camp Creek Fire",
    county: "Lane",
    hazardType: "wildfire",
    evacuationLevel: 3,
    evacuationLevelLabel: "Level 3 - Go Now",
    severity: "critical",
    structuresWithin: 412,
    populationWithin: 1104,
    observedAt: "2026-08-24T19:02:00Z",
    geometry: POLYGON,
    ...overrides,
  };
}

function burnScar(overrides: Partial<ParquetBrowserBurnScar> = {}): ParquetBrowserBurnScar {
  return {
    fireId: "OR4318712201820180722",
    fireName: "Terwilliger",
    fireYear: 2018,
    fireType: "Wildfire",
    assessmentType: "Extended",
    ignitionDate: "2018-07-22",
    observedDay: "2024-03-01",
    acres: 11419,
    severityClass: null,
    dataAvailableAt: "2024-03-01T00:00:00Z",
    geometry: POLYGON,
    ...overrides,
  };
}

function firePerimeter(
  overrides: Partial<ParquetBrowserFirePerimeter> = {}
): ParquetBrowserFirePerimeter {
  return {
    featureId: "3f1c9a52-0d1e-4a2c-9c0f-2f6d5b8a7e11",
    uniqueFireIdentifier: "2026-ORWIF-000412",
    snapshotDay: "2026-08-25",
    observedDay: "2026-08-24",
    severity: "high",
    geometry: POLYGON,
    ...overrides,
  };
}

function basin(overrides: Partial<ParquetBrowserWatershed> = {}): ParquetBrowserWatershed {
  return {
    huc: "170501220201",
    hucLevel: 12,
    name: "Cottonwood Creek-Shafer Creek",
    areaSquareKm: 118.4,
    toHuc: "170501220202",
    states: "ID",
    huType: "S",
    releaseDay: "2026-08-07",
    observedAt: "2013-01-01T00:00:00Z",
    geometry: POLYGON,
    ...overrides,
  };
}

function station(
  overrides: Partial<ParquetBrowserSensorStation> = {}
): ParquetBrowserSensorStation {
  return {
    sensorId: "KBOI",
    stationName: "Boise Air Terminal",
    network: "ASOS",
    observedDay: "2026-08-25",
    observedAt: "2026-08-25T18:53:00Z",
    longitude: -116.22,
    latitude: 43.56,
    measurements: [
      { name: "temperature", value: 31.1, unitCode: "wmoUnit:degC", observedAt: "2026-08-25T18:53:00Z" },
    ],
    ...overrides,
  };
}

describe("the cutover presenters rebuild the retired tile functions' attribute tables", () => {
  it("gives evacuation zones the six fields the tooltip reads and the one the fill paints", () => {
    const feature = presentParquetEvacuationZones(ready([evacuationZone()])).features[0];

    expect(feature.geometry).toEqual(POLYGON);
    expect(feature.properties).toEqual({
      evacuation_area_name: "Camp Creek Zone 3",
      fire_name: "Camp Creek Fire",
      county: "Lane",
      hazard_type: "wildfire",
      // The fill is a match on this, with the neutral grey as its fallback arm.
      severity: "critical",
      evacuation_level: 3,
      evacuation_level_label: "Level 3 - Go Now",
      structures_within: 412,
      population_within: 1104,
      // The date part of the producer's own observedAt, which is what
      // geo.feature_observation_day returned and so what the slider filters on.
      observed_day: "2026-08-24",
    });
  });

  it("omits observed_day for an evacuation zone the producer never dated", () => {
    const feature = presentParquetEvacuationZones(
      ready([evacuationZone({ observedAt: null })])
    ).features[0];

    // Absent, not null. tileLayerDateFilter keeps an undated feature alive with
    // ["!", ["has", "observed_day"]], and a null-valued key answers `has` with TRUE -- which
    // would silently delete every undated zone from the map on any scrub.
    expect(feature.properties).not.toHaveProperty("observed_day");
  });

  it("gives a fire perimeter exactly the two attributes fire_risk_tiles ever emitted", () => {
    const feature = presentParquetFirePerimeters(ready([firePerimeter()])).features[0];

    expect(feature.geometry).toEqual(POLYGON);
    // The MVT feature id was `f.id` -- geo.features.id -- so the same feature keeps the same
    // identity across the cutover.
    expect(feature.id).toBe("3f1c9a52-0d1e-4a2c-9c0f-2f6d5b8a7e11");
    // EQUAL, not objectContaining: the vocabulary is the claim. firePerimetersLayer's fill is a
    // match on `severity`, and `tileLayerDateFilter` reads `observed_day`; the SELECT list also
    // named `risk_level` and `name`, which came from JSONB keys no producer writes, so ST_AsMVT
    // omitted both from every tile this platform ever served.
    expect(feature.properties).toEqual({ severity: "high", observed_day: "2026-08-24" });
  });

  it("never emits incidentName under `name`, which no tile ever carried", () => {
    const feature = presentParquetFirePerimeters(ready([firePerimeter()])).features[0];

    // The lane HAS `incident_name`, so this is a live temptation rather than a hypothetical one:
    // publishing it as `name` would invent an attribute the layer never had. Widening the tooltip
    // is a hover-fields change with its own review.
    expect(feature.properties).not.toHaveProperty("name");
    expect(feature.properties).not.toHaveProperty("risk_level");
  });

  it("omits observed_day for an incident WFIGS never dated, so the filter keeps it", () => {
    const feature = presentParquetFirePerimeters(
      ready([firePerimeter({ observedDay: null })])
    ).features[0];

    // Absent, not null -- the sharpest instance of the mvtProperties rule in this file. The
    // server-side in-frame filter deliberately KEEPS an undated incident at every date; a
    // present-and-null key answers ["has", "observed_day"] with TRUE, which would flip
    // tileLayerDateFilter from keeping those rows to comparing them against a date they do not
    // have, and hide exactly the rows the reader went out of its way to preserve.
    expect(feature.properties).not.toHaveProperty("observed_day");
    // The rest of the vocabulary is untouched by the omission.
    expect(feature.properties).toEqual({ severity: "high" });
  });

  it("omits severity for a perimeter WFIGS reported no containment for", () => {
    const feature = presentParquetFirePerimeters(
      ready([firePerimeter({ severity: null })])
    ).features[0];

    // perimeter_severity() returns None rather than a fabricated bucket, and the fill's `match`
    // falls through to UNCLASSIFIED_FILL_COLOR on a missing property -- the neutral grey the
    // legend captions "Containment not reported".
    expect(feature.properties).not.toHaveProperty("severity");
  });

  it("omits acres for a scar with no reported acreage, so the case arm paints it grey", () => {
    const painted = presentParquetBurnSeverity(ready([burnScar()])).features[0];
    const unreported = presentParquetBurnSeverity(
      ready([burnScar({ acres: null })])
    ).features[0];

    expect(painted.properties).toEqual(
      expect.objectContaining({
        fire_name: "Terwilliger",
        fire_year: 2018,
        fire_type: "Wildfire",
        acres: 11419,
        observed_day: "2024-03-01",
      })
    );
    // burnSeverityLayer paints ["case", ["has", "acres"], <log ramp>, <grey>]. A present-and-null
    // acres would send an unmeasured scar through the ramp instead.
    expect(unreported.properties).not.toHaveProperty("acres");
  });

  it("carries severity_class off the row even though MTBS publishes none", () => {
    const withClass = presentParquetBurnSeverity(
      ready([burnScar({ severityClass: "moderate" })])
    ).features[0];

    expect(withClass.properties).toEqual(
      expect.objectContaining({ severity_class: "moderate" })
    );
    // Null on every published row today, so the tooltip line simply does not appear.
    expect(
      presentParquetBurnSeverity(ready([burnScar()])).features[0].properties
    ).not.toHaveProperty("severity_class");
  });

  it("emits huc12 only at the base rung, so a rollup is never captioned as a basin", () => {
    const huc12 = presentParquetWatersheds(ready([basin()])).features[0];
    const huc10 = presentParquetWatersheds(
      ready([
        basin({ huc: "1705012202", hucLevel: 10, name: null, toHuc: null, states: null, huType: null }),
      ])
    ).features[0];

    expect(huc12.properties).toEqual({
      huc: "170501220201",
      huc_level: 12,
      huc12: "170501220201",
      name: "Cottonwood Creek-Shafer Creek",
      areasqkm: 118.4,
      tohuc: "170501220202",
      states: "ID",
      hutype: "S",
    });
    // geo.watershed_tiles emitted NULL::text AS huc12 on its rollup branch for exactly this
    // reason: a ten-digit code under a huc12 key presents a merged basin as a published one.
    expect(huc10.properties).toEqual({ huc: "1705012202", huc_level: 10, areasqkm: 118.4 });
  });

  it("publishes no basin_count, because the lane has no such column to publish", () => {
    const feature = presentParquetWatersheds(
      ready([basin({ huc: "170501", hucLevel: 6 })])
    ).features[0];

    // The tile function computed it with count(*) while building geo.watershed_rollup. The
    // HierarchicalDissolve declares no counting aggregation, so the number does not exist --
    // formatWatershed drops that line rather than being handed an invented one.
    expect(feature.properties).not.toHaveProperty("basin_count");
    expect(feature.properties).toEqual(
      expect.objectContaining({ huc: "170501", huc_level: 6 })
    );
  });

  it("draws one Point per station with the four attributes sensor_tiles projected", () => {
    const feature = presentParquetSensorStations(ready([station()])).features[0];

    expect(feature.geometry).toEqual({ type: "Point", coordinates: [-116.22, 43.56] });
    expect(feature.properties).toEqual({
      // The circle colour is a match on this. It was the fabricated-field bug in the tile
      // function: sensor_tiles projected sensor_type/status/name, none of which any producer
      // writes, so every station drew in the neutral grey fallback.
      network: "ASOS",
      sensor_id: "KBOI",
      station_name: "Boise Air Terminal",
      observed_at: "2026-08-25T18:53:00Z",
      observed_day: "2026-08-25",
    });
  });

  it("gives a coarse sensor cell a stable id without inventing a station identity", () => {
    const collection = presentParquetSensorStations(
      ready([station({ sensorId: null, stationName: null })])
    );

    expect(collection.features[0].id).toBe("-116.22:43.56:0");
    expect(collection.features[0].properties).not.toHaveProperty("sensor_id");
    expect(collection.features[0].properties).not.toHaveProperty("station_name");
  });

  it("draws nothing for any state but ready, so a refusal never paints as an empty map", () => {
    const notGenerated = {
      state: "not_generated" as const,
      requestedDay: "2026-08-28",
      reason: "day_not_written" as const,
    };

    expect(presentParquetEvacuationZones(notGenerated).features).toEqual([]);
    expect(presentParquetBurnSeverity(notGenerated).features).toEqual([]);
    expect(presentParquetWatersheds(notGenerated).features).toEqual([]);
    expect(presentParquetSensorStations(notGenerated).features).toEqual([]);
    // A fire-perimeters lane that has not yet written its first post-re-registration snapshot
    // answers exactly this, so the empty-on-refusal rule is load-bearing for it today rather
    // than defensive: an empty collection draws nothing, where a thrown presenter would take the
    // map with it.
    expect(presentParquetFirePerimeters(notGenerated).features).toEqual([]);
    expect(presentParquetEvacuationZones(undefined).features).toEqual([]);
    expect(presentParquetFirePerimeters(undefined).features).toEqual([]);
  });
});
