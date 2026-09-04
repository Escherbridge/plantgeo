import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/server/services/parquet-plane-client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/server/services/parquet-plane-client")>();
  return {
    ...actual,
    getParquetLayerDay: vi.fn(),
    getParquetLayerDayWindow: vi.fn(),
    getParquetLatestRelease: vi.fn(),
  };
});

import {
  UpstreamAbortedError,
  UpstreamConfigurationError,
  UpstreamHttpError,
  UpstreamPayloadError,
  UpstreamTimeoutError,
} from "@/lib/server/http/bounded-upstream";
import { ZoomTierResolutionError } from "@/lib/map/zoom-tiers";
import {
  getParquetLatestRelease,
  getParquetLayerDay,
  getParquetLayerDayWindow,
  ParquetPlaneContractError,
  ParquetPlaneRequestError,
} from "@/lib/server/services/parquet-plane-client";
import {
  getParquetBurnSeverity,
  getParquetDrought,
  getParquetClimateField,
  getParquetEvacuationZones,
  getParquetFirePerimeters,
  getParquetSensorStations,
  type ParquetReaderResult,
  getParquetFireDetections,
  getParquetSoilField,
  getParquetVegetation,
  getParquetWaterGauges,
  getParquetWatersheds,
  getParquetWeatherObservations,
} from "@/lib/server/services/parquet-trpc-readers";

const mockedDay = vi.mocked(getParquetLayerDay);
const mockedWindow = vi.mocked(getParquetLayerDayWindow);
const mockedRelease = vi.mocked(getParquetLatestRelease);

const evidence = {
  reason: "source had no release",
  upstreamResponse: "200 {}",
  recordedAt: "2026-08-20T06:14:02Z",
  runId: "run-42",
};

function published(day: string, rows: readonly Record<string, unknown>[], servedDay = day) {
  return { state: "published" as const, requestedDay: day, servedDay, rows, truncated: false };
}

/**
 * The `ready` arm's payload, or a failed assertion naming the state that came instead.
 *
 * Narrowing by hand at each call site would be five lines of `if (result.state !== "ready")` per
 * support assertion; this keeps the assertion about the support rather than about the union.
 */
function readyData<T>(result: ParquetReaderResult<T>): T {
  if (result.state !== "ready") {
    throw new Error(`expected a ready result, got ${result.state}`);
  }
  return result.data;
}

function waterRow() {
  return {
    site_number: "13042500",
    observed_at: "2026-08-20T18:15:00Z",
    observed_day: "2026-08-20",
    site_name: "Big Wood River",
    latitude: 43.52,
    longitude: -114.31,
    flow_cfs: 122,
    percentile: 61,
    condition: "normal",
    trend: "stable",
    source: "usgs-nwis",
    geometry_linked: true,
    data_available_at: "2026-08-20T18:30:00Z",
    ingested_at: "2026-08-20T18:31:00Z",
  };
}

function vegetationRow(day: string, value: number) {
  return {
    cell_id: null,
    grid_name: "sentinel-2-quarter-degree",
    metric_name: "ndvi",
    metric_unit: "1",
    observed_day: day,
    metric_value: value,
    observation_checksum: null,
    data_available_at: `${day}T18:30:00Z`,
    release_count: 1,
    allowed_client_exposure: true,
    // A REAL cell of the quarter-degree lattice: `ingest/vegetation.py:344-347` centres each cell
    // a half step above `row * 0.25`, so the centroids are odd multiples of 0.125 and the cell
    // edges are the multiples of 0.25. The old -114.25/43.5 was a cell EDGE, and a fixture on no
    // real lattice cannot tell a correct phase from one shifted half a cell.
    cell_longitude: -124.875,
    cell_latitude: 42.125,
  };
}

function soilMoistureRow(overrides: Record<string, unknown> = {}) {
  return {
    support_key: "era5-land-0.1deg",
    signal_name: "soil_water_content_layer_2",
    normalized_unit: "m^3/m^3",
    // The pinned south-west cell of the ERA5-Land support lattice
    // (`pipeline/direct/soil/support.py:51-57`): centroid (-124.875, 42.125), cell edges on the
    // multiples of 0.25.
    cell_id: "era5-land:42.125:-124.875",
    observed_day: "2026-08-02",
    normalized_value: 0.23,
    observation_count: 2,
    newest_observed_at: "2026-08-02T00:00:00Z",
    coverage_fraction: 1,
    allowed_client_exposure: false,
    cell_longitude: -124.875,
    cell_latitude: 42.125,
    source_key: "open-meteo-era5-land-archive",
    source_parameter: "soil_moisture_7_to_28cm_mean",
    source_snapshot_id: "prod-20260826-full-signal-v1",
    source_manifest_sha256:
      "465abc4e813bf28c78acd7f97a4da9d19ad959e525de3eb1f422ca2f6e73e94f",
    precedence_contract: "release_retrieved_at_desc,fact_id_desc",
    selected_source_row_id: 12,
    selected_source_row_sha256: "b".repeat(64),
    selected_source_release_id: "release-12",
    selected_source_release_retrieved_at: "2026-08-03T00:00:00Z",
    selected_source_release_payload_checksum: "payload-12",
    selected_source_part_key: "raw/part.parquet",
    selected_source_part_sha256: "c".repeat(64),
    selected_source_row_ordinal: 7,
    input_source_row_count: 2,
    input_source_row_digest: "digest",
    input_source_row_ids: [11, 12],
    input_source_row_sha256s: ["d".repeat(64), "b".repeat(64)],
    input_source_release_ids: ["release-11", "release-12"],
    input_source_part_keys: ["raw/part.parquet"],
    input_source_part_sha256s: ["c".repeat(64)],
    input_source_row_ordinals: [6, 7],
    ...overrides,
  };
}

function soilVpdRow(overrides: Record<string, unknown> = {}) {
  return {
    support_key: "era5-land-0.1deg",
    signal_name: "vapor_pressure_deficit",
    normalized_unit: "kPa",
    cell_id: null,
    observed_day: "2026-08-02",
    normalized_value: 1.7,
    observation_count: 4,
    newest_observed_at: "2026-08-02T00:00:00Z",
    coverage_fraction: 1,
    allowed_client_exposure: false,
    // What z5 actually carries for the pinned south-west centroid: `floor(-124.875 / 0.2) * 0.2`
    // and `floor(42.125 / 0.2) * 0.2` (`floor_to_resolution`, tiers.py:313-315).
    cell_longitude: -125,
    cell_latitude: 42,
    ...overrides,
  };
}

function signalPlaneClimateRow(
  signalName: string,
  normalizedUnit: string,
  overrides: Record<string, unknown> = {}
) {
  return {
    support_key: "surface",
    signal_name: signalName,
    normalized_unit: normalizedUnit,
    cell_id: "nasa-power-001",
    observed_day: "2026-08-02",
    normalized_value: 21.5,
    observation_count: 2,
    newest_observed_at: "2026-08-02T00:00:00Z",
    coverage_fraction: 1,
    allowed_client_exposure: false,
    cell_longitude: -114.25,
    cell_latitude: 43.5,
    ...overrides,
  };
}

function soilWetnessRow(overrides: Record<string, unknown> = {}) {
  const sha = "a".repeat(64);
  return {
    ...signalPlaneClimateRow("soil_wetness_root_zone", "fraction_of_saturation", {
      normalized_value: 0.42,
    }),
    selected_observation_id: 42,
    selected_canonical_row_sha256: sha,
    selected_source_release_id: "release-42",
    selected_release_retrieved_at: "2026-08-03T00:00:00Z",
    physical_candidate_count: 2,
    lineage_sha256: sha,
    input_manifest_sha256:
      "465abc4e813bf28c78acd7f97a4da9d19ad959e525de3eb1f422ca2f6e73e94f",
    ...overrides,
  };
}

function soilTemperatureRow(overrides: Record<string, unknown> = {}) {
  const sha = "b".repeat(64);
  return {
    data_source_key: "open-meteo-era5-land-archive",
    source_parameter: "soil_temperature_28_to_100cm_mean",
    // An ERA5-Land lane, so its cell is one of that lattice's, not the NASA POWER one the base
    // fixture carries: centroid (-124.875, 42.125), `pipeline/direct/soil/support.py:51-57`.
    ...signalPlaneClimateRow("soil_temperature_level_3", "C", {
      support_key: "era5-land-0.1deg",
      cell_longitude: -124.875,
      cell_latitude: 42.125,
    }),
    selected_observation_id: 52,
    selected_canonical_row_sha256: sha,
    selected_source_release_id: "release-52",
    selected_release_retrieved_at: "2026-08-03T00:00:00Z",
    physical_candidate_count: 2,
    lineage_sha256: sha,
    input_manifest_sha256:
      "465abc4e813bf28c78acd7f97a4da9d19ad959e525de3eb1f422ca2f6e73e94f",
    ...overrides,
  };
}

function climateLineageRow(day = "2026-08-06") {
  const sha = "a".repeat(64);
  return {
    support_key: "surface",
    signal_name: "precipitation",
    normalized_unit: "mm/day",
    cell_id: "nasa-power-001",
    observed_day: day,
    normalized_value: 2.5,
    observation_count: 2,
    newest_observed_at: `${day}T00:00:00Z`,
    coverage_fraction: 1,
    allowed_client_exposure: false,
    cell_longitude: -114.25,
    cell_latitude: 43.5,
    source_key: "nasa-power-daily",
    source_parameter: "PRECTOTCORR",
    source_snapshot_id: "prod-20260826-full-signal-v1",
    source_manifest_sha256:
      "465abc4e813bf28c78acd7f97a4da9d19ad959e525de3eb1f422ca2f6e73e94f",
    precedence_contract: "release_retrieved_at_desc",
    selected_source_row_id: 42,
    selected_source_row_sha256: sha,
    selected_source_release_id: "release-42",
    selected_source_release_retrieved_at: `${day}T01:00:00Z`,
    selected_source_release_payload_checksum: sha,
    selected_source_part_key: "raw/part.parquet",
    selected_source_part_sha256: sha,
    selected_source_row_ordinal: 0,
    input_source_row_count: 2,
    input_source_row_digest: sha,
    input_source_row_ids: [41, 42],
    input_source_row_sha256s: [sha, sha],
    input_source_release_ids: ["release-41", "release-42"],
    input_source_part_keys: ["raw/part.parquet"],
    input_source_part_sha256s: [sha],
    input_source_row_ordinals: [0, 1],
  };
}

beforeEach(() => {
  mockedDay.mockReset();
  mockedWindow.mockReset();
  mockedRelease.mockReset();
});

describe("Parquet tRPC state adapter", () => {
  it("reads a climate field from its exact promoted z13 lane without PostgreSQL fallback", async () => {
    mockedDay.mockResolvedValue(published("2026-08-06", [climateLineageRow()]));

    const { result, zoomTier } = await getParquetClimateField({
      bbox: "-125,42,-111,49",
      date: "2026-08-06",
      mapZoom: 14,
      signal: "precipitation",
      variant: "mean",
    });

    expect(mockedDay).toHaveBeenCalledWith({
      layer: "climate-field-precipitation",
      day: "2026-08-06",
      zoomTier: 13,
      bbox: "-125,42,-111,49",
    });
    // The rung is reported back, not inferred by the caller: the renderer must be able to say
    // whether it is drawing stored cells or an aggregate without re-resolving the ladder.
    expect(zoomTier).toBe(13);
    expect(result).toMatchObject({
      state: "ready",
      data: [{ cellId: "nasa-power-001", value: 2.5, observationCount: 2 }],
    });
    // The envelope, not the nullability of `cellId`: the client is told the rung, the form, the
    // pitch and the phase outright, so it never re-derives a cell width from a private table.
    expect(readyData(result)[0].support).toEqual({
      zoomTier: 13,
      supportKind: "tessellated_cell",
      supportId: "nasa-power-001",
      origin: "cell_center",
      cellWidthDegrees: 1,
      cellHeightDegrees: 1,
      // The corner the serving lattice snapped this centre to. A one-degree lattice of CENTRES
      // has its edges on the half degrees, so the centre -114.25/43.5 sits in the cell whose
      // south-west corner is -114.5/43.5 -- and the client draws that square rather than
      // re-deriving one from a phase it cannot see.
      cellOriginDegrees: [-114.5, 43.5],
      aggregationMethod: "mean",
      contributorCount: 2,
      provenance: {
        sourceLayer: "climate-field-precipitation",
        observedDay: "2026-08-06",
        newestObservedAt: "2026-08-06T00:00:00Z",
        attribution: "NASA POWER (NASA LaRC)",
      },
    });
  });

  /**
   * A coarse climate rung declares the SAME cell size as the detail rung, and that is correct
   * rather than a bug: 0.01 at z9 and 0.2 at z5 are both finer than this lane's one-degree
   * sampling lattice, so those rungs re-floor the same measurement instead of merging several.
   * Only z0's five degrees is a real coarsening.
   */
  it.each([
    [11.4, 9, 1],
    [7, 5, 1],
    [3, 0, 5],
  ])("resolves map zoom %s to a z%s climate cell of %s degrees", async (mapZoom, zoomTier, cellDegrees) => {
    mockedDay.mockResolvedValue(
      published("2026-08-06", [{ ...climateLineageRow(), cell_id: null }])
    );

    const { result } = await getParquetClimateField({
      bbox: "-125,42,-111,49",
      date: "2026-08-06",
      mapZoom,
      signal: "precipitation",
      variant: "mean",
    });

    expect(readyData(result)[0].support).toMatchObject({
      zoomTier,
      supportKind: "tessellated_cell",
      // Minted from the rung and the position, never left null -- a client that had to read
      // "aggregate" off a missing id could not tell this rung from raw observations.
      supportId: `${zoomTier}:-114.25:43.5`,
      origin: "cell_origin",
      cellWidthDegrees: cellDegrees,
      cellHeightDegrees: cellDegrees,
    });
  });

  /**
   * The three coarse rungs are published and, until this change, were never read: the reader
   * pinned z13 at every zoom. Exactly one rung answers a request -- never two merged, which would
   * double-count the ground both describe.
   */
  it.each([
    [3, 0],
    [7, 5],
    [11.4, 9],
    [13, 13],
  ])("resolves map zoom %s onto the single serving rung z%s", async (mapZoom, zoomTier) => {
    const detail = zoomTier === 13;
    mockedDay.mockResolvedValue(
      published("2026-08-06", [
        detail ? climateLineageRow() : { ...climateLineageRow(), cell_id: null },
      ])
    );

    const read = await getParquetClimateField({
      bbox: "-125,42,-111,49",
      date: "2026-08-06",
      mapZoom,
      signal: "precipitation",
      variant: "mean",
    });

    expect(mockedDay).toHaveBeenCalledWith(expect.objectContaining({ zoomTier }));
    expect(mockedDay).toHaveBeenCalledTimes(1);
    expect(read.zoomTier).toBe(zoomTier);
    expect(read.result).toMatchObject({ state: "ready" });
  });

  /**
   * The same identity rule `decodeSoilFieldRows` enforces. A coarse row that kept its `cell_id`,
   * or a z13 row that lost one, means the reader is reading a rung it did not ask for -- and an
   * aggregate captioned as a stored cell is the exact confusion `aggregated` exists to prevent.
   */
  it.each([
    [3, "kept", climateLineageRow()],
    [14, "lost", { ...climateLineageRow(), cell_id: null }],
  ] as const)(
    "fails closed when a z%s row has %s its cell identity",
    async (mapZoom, _verb, row) => {
      mockedDay.mockResolvedValue(published("2026-08-06", [row]));

      const { result } = await getParquetClimateField({
        bbox: "-125,42,-111,49",
        date: "2026-08-06",
        mapZoom,
        signal: "precipitation",
        variant: "mean",
      });

      expect(result).toMatchObject({ state: "upstream_unavailable", fault: { kind: "contract" } });
    }
  );

  it("threads the caller's cancellation into the lane read as abortSignal", async () => {
    const controller = new AbortController();
    mockedDay.mockResolvedValue(published("2026-08-06", [climateLineageRow()]));

    await getParquetClimateField({
      bbox: "-125,42,-111,49",
      date: "2026-08-06",
      mapZoom: 14,
      signal: "precipitation",
      variant: "mean",
      abortSignal: controller.signal,
    });

    // `signal` here is the measured quantity and never the cancellation; the reader's `Omit`
    // makes handing one to the other a compile error rather than a runtime surprise.
    expect(mockedDay).toHaveBeenCalledWith(
      expect.objectContaining({ signal: controller.signal })
    );
  });

  it("fails a climate row from the wrong signal closed", async () => {
    mockedDay.mockResolvedValue(
      published("2026-08-06", [{ ...climateLineageRow(), signal_name: "wind_speed" }])
    );

    await expect(
      getParquetClimateField({
        bbox: "-125,42,-111,49",
        date: "2026-08-06",
        mapZoom: 14,
        signal: "precipitation",
        variant: "mean",
      }).then((read) => read.result)
    ).resolves.toMatchObject({ state: "upstream_unavailable", fault: { kind: "contract" } });
  });

  it.each([
    ["air-temperature", "max", "climate-field-air-temperature-max", "air_temperature_max", "C"],
    ["dew-point", "mean", "climate-field-dew-point", "dew_point_temperature", "C"],
    ["wind-speed", "mean", "climate-field-wind-speed", "wind_speed", "m/s"],
  ] as const)(
    "reads %s from the exact snapshot product",
    async (signal, variant, layer, signalName, unit) => {
      mockedDay.mockResolvedValue(
        published("2026-08-02", [signalPlaneClimateRow(signalName, unit)])
      );

      const { result } = await getParquetClimateField({
        bbox: "-125,42,-111,49",
        date: "2026-08-02",
        mapZoom: 14,
        signal,
        variant,
      });

      expect(mockedDay).toHaveBeenCalledWith({
        layer,
        day: "2026-08-02",
        zoomTier: 13,
        bbox: "-125,42,-111,49",
      });
      expect(result).toMatchObject({ state: "ready", data: [{ value: 21.5 }] });
    }
  );

  it("reads manifest-bound NASA soil wetness from its exact snapshot product", async () => {
    mockedDay.mockResolvedValue(published("2026-08-02", [soilWetnessRow()]));

    const { result } = await getParquetClimateField({
      bbox: "-125,42,-111,49",
      date: "2026-08-02",
      mapZoom: 14,
      signal: "soil-wetness-root-zone",
      variant: "mean",
    });

    expect(mockedDay).toHaveBeenCalledWith({
      layer: "soil-wetness-root-zone",
      day: "2026-08-02",
      zoomTier: 13,
      bbox: "-125,42,-111,49",
    });
    expect(result).toMatchObject({ state: "ready", data: [{ value: 0.42 }] });
  });

  it("fails NASA soil wetness from another source snapshot closed", async () => {
    mockedDay.mockResolvedValue(
      published("2026-08-02", [soilWetnessRow({ input_manifest_sha256: "f".repeat(64) })])
    );

    await expect(
      getParquetClimateField({
        bbox: "-125,42,-111,49",
        date: "2026-08-02",
        mapZoom: 14,
        signal: "soil-wetness-root-zone",
        variant: "mean",
      }).then((read) => read.result)
    ).resolves.toMatchObject({ state: "upstream_unavailable", fault: { kind: "contract" } });
  });

  it("maps a published water-gauge day to ready and resolves the shared zoom tier", async () => {
    mockedWindow.mockResolvedValue([
      published("2026-08-19", [
        {
          ...waterRow(),
          observed_at: "2026-08-19T23:30:00Z",
          observed_day: "2026-08-19",
          flow_cfs: 90,
        },
      ]),
      published("2026-08-20", [
        { ...waterRow(), observed_at: "2026-08-20T17:15:00Z", flow_cfs: 100 },
        waterRow(),
      ]),
    ]);

    const result = await getParquetWaterGauges({
      bbox: "-125,42,-111,49",
      mapZoom: 11.4,
      nowMs: Date.parse("2026-08-20T20:00:00Z"),
    });

    expect(mockedWindow).toHaveBeenCalledWith({
      layer: "water-gauges",
      firstDay: "2026-08-19",
      lastDay: "2026-08-20",
      zoomTier: 9,
      bbox: "-125,42,-111,49",
    });
    expect(result).toMatchObject({
      state: "ready",
      requestedDay: "2026-08-20",
      servedDay: "2026-08-20",
      data: [{ siteNumber: "13042500", flowCfs: 122 }],
    });
    // Above the base rung the derivation nulls `site_number` and `site_name`, so a row stands for
    // a CELL of gauges rather than one gauge. `contributorCount` is the two fresh readings this
    // envelope folded -- measured by the fold, not assumed, because the lane publishes no count.
    expect(readyData(result)[0].support).toMatchObject({
      zoomTier: 9,
      supportKind: "aggregate_cell",
      origin: "cell_origin",
      cellWidthDegrees: 0.01,
      // `mean`, not `count`: the number the row carries is `flowCfs`, the mean discharge over the
      // gauges this cell folded. How many folded is `contributorCount` on the line below.
      aggregationMethod: "mean",
      contributorCount: 2,
      provenance: { sourceLayer: "water-gauges", attribution: "U.S. Geological Survey NWIS" },
    });
  });

  /**
   * The one lane whose detail rung is genuinely a point. `LAYER_RENDER_CONTRACT` permits
   * `raw_point` for water at the detail band and nothing else, and a station has no footprint --
   * publishing a cell size for one would license a renderer to draw a square around a gauge.
   */
  it("serves a real gauge as a raw point with no cell size at the detail rung", async () => {
    mockedDay.mockResolvedValue(published("2026-08-19", [waterRow()]));

    const result = await getParquetWaterGauges({
      bbox: "-125,42,-111,49",
      date: "2026-08-19",
      mapZoom: 13,
    });

    const support = readyData(result)[0].support;
    expect(support).toMatchObject({
      zoomTier: 13,
      supportKind: "raw_point",
      supportId: "13042500",
      origin: "cell_center",
      aggregationMethod: "none",
      contributorCount: 1,
    });
    expect(support.cellWidthDegrees).toBeUndefined();
    expect(support.cellHeightDegrees).toBeUndefined();
  });

  it("maps governed absence with all of its evidence", async () => {
    mockedDay.mockResolvedValue({
      state: "governed_absence",
      requestedDay: "2026-08-20",
      servedDay: "2026-08-20",
      evidence,
    });

    await expect(
      getParquetWeatherObservations({
        bbox: "-125,42,-111,49",
        date: "2026-08-20",
        mapZoom: 13,
      })
    ).resolves.toEqual({
      state: "absent",
      requestedDay: "2026-08-20",
      servedDay: "2026-08-20",
      evidence,
    });
  });

  it.each(["day_not_written", "lane_never_written"] as const)(
    "retains the %s reason under not_generated",
    async (reason) => {
      mockedDay.mockResolvedValue({ state: reason, requestedDay: "2026-08-20" });

      const result = await getParquetWaterGauges({
        bbox: "-125,42,-111,49",
        date: "2026-08-20",
        mapZoom: 5,
      });

      expect(result).toEqual({ state: "not_generated", requestedDay: "2026-08-20", reason });
    }
  );

  it.each([
    ["configuration", new UpstreamConfigurationError("missing URL")],
    ["http", new UpstreamHttpError(503)],
    ["payload", new UpstreamPayloadError("oversized")],
    ["timeout", new UpstreamTimeoutError("timed out")],
    ["contract", new ParquetPlaneContractError("wire drift")],
    // Its own kind, not `timeout`: the two are the same DOMException on the wire and mean
    // opposite things, and calling a client that navigated away an outage would page someone.
    ["aborted", new UpstreamAbortedError("cancelled by its caller")],
  ] as const)("makes a %s failure typed and visible", async (kind, error) => {
    mockedDay.mockRejectedValue(error);

    const result = await getParquetWaterGauges({
      bbox: "-125,42,-111,49",
      date: "2026-08-20",
      mapZoom: 9,
    });

    expect(result).toMatchObject({ state: "upstream_unavailable", fault: { kind } });
  });

  /**
   * A signal accepted on the input and then dropped is the worst of both worlds: the caller
   * believes the read was abandoned while the upstream keeps working.
   */
  it("threads the caller's cancellation into the window read it fans out to", async () => {
    const controller = new AbortController();
    mockedWindow.mockResolvedValue([
      published("2026-08-19", []),
      published("2026-08-20", [waterRow()]),
    ]);

    await getParquetWaterGauges({
      bbox: "-125,42,-111,49",
      mapZoom: 9,
      nowMs: Date.parse("2026-08-20T20:00:00Z"),
      signal: controller.signal,
    });

    expect(mockedWindow).toHaveBeenCalledWith(
      expect.objectContaining({ signal: controller.signal })
    );
  });

  it("omits the field entirely for a caller with nothing to cancel", async () => {
    mockedDay.mockResolvedValue(published("2026-08-20", [waterRow()]));

    await getParquetWaterGauges({
      bbox: "-125,42,-111,49",
      date: "2026-08-20",
      mapZoom: 9,
      nowMs: Date.parse("2026-08-21T20:00:00Z"),
    });

    expect(Object.keys(mockedDay.mock.calls[0][0])).not.toContain("signal");
  });

  it("makes a fetch transport failure typed and visible", async () => {
    mockedDay.mockRejectedValue(new TypeError("fetch failed"));

    await expect(
      getParquetWaterGauges({ bbox: "-125,42,-111,49", date: "2026-08-20", mapZoom: 9 })
    ).resolves.toMatchObject({ state: "upstream_unavailable", fault: { kind: "network" } });
  });

  it("does not swallow request, zoom, or programmer errors", async () => {
    await expect(
      getParquetWaterGauges({ bbox: "-125,42,-111,49", date: "20 Aug", mapZoom: 9 })
    ).rejects.toBeInstanceOf(ParquetPlaneRequestError);
    await expect(
      getParquetWaterGauges({ bbox: "-125,42,-111,49", date: "2026-02-30", mapZoom: 9 })
    ).rejects.toBeInstanceOf(ParquetPlaneRequestError);
    await expect(
      getParquetWaterGauges({ bbox: "-125,42,-111,49", date: "2026-08-20", mapZoom: -1 })
    ).rejects.toBeInstanceOf(ZoomTierResolutionError);

    mockedDay.mockRejectedValue(new TypeError("programmer fault"));
    await expect(
      getParquetWaterGauges({ bbox: "-125,42,-111,49", date: "2026-08-20", mapZoom: 9 })
    ).rejects.toThrow("programmer fault");
  });

  it("fails a drifted lane row closed as an upstream contract fault", async () => {
    mockedDay.mockResolvedValue(published("2026-08-20", [{ site_number: "missing-the-rest" }]));

    const result = await getParquetWaterGauges({
      bbox: "-125,42,-111,49",
      date: "2026-08-20",
      mapZoom: 9,
    });

    expect(result).toMatchObject({ state: "upstream_unavailable", fault: { kind: "contract" } });
  });
});

describe("lane day and release semantics", () => {
  it("retains today's not-generated state when yesterday has no fresh water row", async () => {
    mockedWindow.mockResolvedValue([
      published("2026-08-19", [
        {
          ...waterRow(),
          observed_at: "2026-08-19T10:00:00Z",
          observed_day: "2026-08-19",
        },
      ]),
      { state: "day_not_written", requestedDay: "2026-08-20" },
    ]);

    await expect(
      getParquetWaterGauges({
        bbox: "-125,42,-111,49",
        date: "2026-08-20",
        mapZoom: 13,
        nowMs: Date.parse("2026-08-20T20:00:00Z"),
      })
    ).resolves.toEqual({
      state: "not_generated",
      requestedDay: "2026-08-20",
      reason: "day_not_written",
    });
  });

  it("labels a fresh midnight-rollover water row with its publisher day", async () => {
    mockedWindow.mockResolvedValue([
      published("2026-08-19", [
        {
          ...waterRow(),
          observed_at: "2026-08-19T23:30:00Z",
          observed_day: "2026-08-19",
        },
      ]),
      published("2026-08-20", []),
    ]);

    await expect(
      getParquetWaterGauges({
        bbox: "-125,42,-111,49",
        date: "2026-08-20",
        mapZoom: 13,
        nowMs: Date.parse("2026-08-20T02:00:00Z"),
      })
    ).resolves.toMatchObject({
      state: "ready",
      requestedDay: "2026-08-20",
      servedDay: "2026-08-19",
      data: [{ observedDay: "2026-08-19" }],
    });
  });

  it("refuses an empty live water answer when an earlier publisher day was truncated", async () => {
    mockedWindow.mockResolvedValue([
      { ...published("2026-08-19", []), truncated: true },
      published("2026-08-20", []),
    ]);

    await expect(
      getParquetWaterGauges({
        bbox: "-125,42,-111,49",
        mapZoom: 13,
        nowMs: Date.parse("2026-08-20T02:00:00Z"),
      })
    ).resolves.toMatchObject({
      state: "upstream_unavailable",
      fault: { kind: "contract" },
    });
  });

  it("keeps the newest weather row per coordinate and drops stale live-day observations", async () => {
    mockedWindow.mockResolvedValue([
      published("2026-08-19", []),
      published("2026-08-20", [
        {
          latitude: 43.5,
          longitude: -114.25,
          observed_at: "2026-08-20T12:00:00Z",
          observed_day: "2026-08-20",
          external_id: "43.5000:-114.2500:2026-08-20T12:00:00Z",
          temperature_c: 20,
          relative_humidity_pct: 40,
          wind_speed_ms: 2,
          wind_direction_deg: 180,
          precipitation_mm: 0,
          source: "open-meteo",
          feature_id: "old",
          ingested_at: "2026-08-20T12:05:00Z",
        },
        {
          latitude: 43.5,
          longitude: -114.25,
          observed_at: "2026-08-20T18:30:00Z",
          observed_day: "2026-08-20",
          external_id: "43.5000:-114.2500:2026-08-20T18:30:00Z",
          temperature_c: 24,
          relative_humidity_pct: 35,
          wind_speed_ms: 3,
          wind_direction_deg: 190,
          precipitation_mm: 0,
          source: "open-meteo",
          feature_id: "new",
          ingested_at: "2026-08-20T18:35:00Z",
        },
        {
          latitude: 43.5,
          longitude: -114.25,
          observed_at: "2026-08-20T22:00:00Z",
          observed_day: "2026-08-20",
          external_id: "43.5000:-114.2500:2026-08-20T22:00:00Z",
          temperature_c: 99,
          relative_humidity_pct: 1,
          wind_speed_ms: 1,
          wind_direction_deg: 1,
          precipitation_mm: 0,
          source: "open-meteo",
          feature_id: "future",
          ingested_at: "2026-08-20T22:01:00Z",
        },
      ]),
    ]);

    const result = await getParquetWeatherObservations({
      bbox: "-125,42,-111,49",
      date: "2026-08-20",
      mapZoom: 13,
      nowMs: Date.parse("2026-08-20T20:00:00Z"),
    });

    expect(result).toMatchObject({
      state: "ready",
      data: [{ observedAt: "2026-08-20T18:30:00Z", temperatureC: 24 }],
    });
    expect(mockedDay).not.toHaveBeenCalled();
  });

  it("reads both publisher days for live weather before freshness filtering and deduplication", async () => {
    mockedWindow.mockResolvedValue([
      published("2026-08-19", [
        {
          latitude: 43.5,
          longitude: -114.25,
          observed_at: "2026-08-19T23:30:00Z",
          observed_day: "2026-08-19",
          external_id: "prior-new",
          temperature_c: 19,
          relative_humidity_pct: 45,
          wind_speed_ms: 2,
          wind_direction_deg: 180,
          precipitation_mm: 0,
          source: "open-meteo",
          feature_id: "prior-new",
          ingested_at: "2026-08-19T23:35:00Z",
        },
        {
          latitude: 43.5,
          longitude: -114.25,
          observed_at: "2026-08-19T20:00:00Z",
          observed_day: "2026-08-19",
          external_id: "prior-stale",
          temperature_c: 17,
          relative_humidity_pct: 50,
          wind_speed_ms: 1,
          wind_direction_deg: 170,
          precipitation_mm: 0,
          source: "open-meteo",
          feature_id: "prior-stale",
          ingested_at: "2026-08-19T20:05:00Z",
        },
      ]),
      published("2026-08-20", [
        {
          latitude: 43.5,
          longitude: -114.25,
          observed_at: "2026-08-20T03:00:00Z",
          observed_day: "2026-08-20",
          external_id: "future",
          temperature_c: 99,
          relative_humidity_pct: 1,
          wind_speed_ms: 1,
          wind_direction_deg: 1,
          precipitation_mm: 0,
          source: "open-meteo",
          feature_id: "future",
          ingested_at: "2026-08-20T03:01:00Z",
        },
      ]),
    ]);

    const result = await getParquetWeatherObservations({
      bbox: "-125,42,-111,49",
      mapZoom: 13,
      nowMs: Date.parse("2026-08-20T01:00:00Z"),
    });

    expect(mockedWindow).toHaveBeenCalledWith({
      layer: "weather-observations",
      firstDay: "2026-08-19",
      lastDay: "2026-08-20",
      zoomTier: 13,
      bbox: "-125,42,-111,49",
    });
    expect(mockedDay).not.toHaveBeenCalled();
    expect(result).toMatchObject({
      state: "ready",
      requestedDay: "2026-08-20",
      servedDay: "2026-08-19",
      data: [{ observedAt: "2026-08-19T23:30:00Z", temperatureC: 19 }],
    });
  });

  /**
   * The weather lane is shaped exactly like the streamflow one -- a sampled point at the base
   * rung, a `GridAggregation` above it -- and until 2026-09-02 it was the one Parquet lane that
   * declared no envelope at all. A row with no envelope cannot say whether the dot on the map is
   * ONE observation or the mean of however many the derivation floored into a coarse cell, which
   * is the same ambiguity the fire and gauge lanes carry an envelope to end.
   */
  it("declares a weather observation as a raw point with no footprint at the detail rung", async () => {
    mockedWindow.mockResolvedValue([
      published("2026-08-19", []),
      published("2026-08-20", [
        {
          latitude: 43.5,
          longitude: -114.25,
          observed_at: "2026-08-20T18:30:00Z",
          observed_day: "2026-08-20",
          external_id: "station-7",
          temperature_c: 24,
          relative_humidity_pct: 35,
          wind_speed_ms: 3,
          wind_direction_deg: 190,
          precipitation_mm: 0,
          source: "open-meteo",
          feature_id: "new",
          ingested_at: "2026-08-20T18:35:00Z",
        },
      ]),
    ]);

    const result = await getParquetWeatherObservations({
      bbox: "-125,42,-111,49",
      mapZoom: 13,
      nowMs: Date.parse("2026-08-20T20:00:00Z"),
    });

    const support = readyData(result)[0].support;
    expect(support).toMatchObject({
      zoomTier: 13,
      supportKind: "raw_point",
      // The lane's own station identity, not a minted one: `external_id` is what it publishes.
      supportId: "station-7",
      origin: "cell_center",
      aggregationMethod: "none",
      contributorCount: 1,
      provenance: { sourceLayer: "weather-observations", attribution: "Open-Meteo" },
    });
    // A sampled observation has no footprint. Publishing one would license a renderer to draw a
    // square around a reading nobody took over that square -- the same rule the gauge lane
    // follows, and the reason `weather` is an `event_point` layer in LAYER_RENDER_CONTRACT.
    expect(support.cellWidthDegrees).toBeUndefined();
    expect(support.cellHeightDegrees).toBeUndefined();
    expect(support.cellOriginDegrees).toBeUndefined();
  });

  it("declares a derived weather rung as an aggregate cell on the ladder's own grid", async () => {
    mockedDay.mockResolvedValue(
      published("2026-08-19", [
        {
          latitude: 43.5,
          longitude: -114.25,
          observed_at: "2026-08-19T18:30:00Z",
          observed_day: "2026-08-19",
          external_id: null,
          temperature_c: 21,
          relative_humidity_pct: 38,
          wind_speed_ms: 2,
          wind_direction_deg: 200,
          precipitation_mm: 0,
          source: "open-meteo",
          feature_id: null,
          ingested_at: "2026-08-19T18:35:00Z",
        },
      ])
    );

    const result = await getParquetWeatherObservations({
      bbox: "-125,42,-111,49",
      date: "2026-08-19",
      mapZoom: 9,
      nowMs: Date.parse("2026-08-20T20:00:00Z"),
    });

    expect(readyData(result)[0].support).toMatchObject({
      zoomTier: 9,
      supportKind: "aggregate_cell",
      // Minted from the rung and the position, because a derived row carries neither station id
      // nor feature id -- the derivation nulls both when it folds several samples into one cell.
      supportId: "9:-114.25:43.5",
      origin: "cell_origin",
      cellWidthDegrees: 0.01,
      cellHeightDegrees: 0.01,
      // The corner the serving lattice snapped it to, so the client draws the server's square.
      cellOriginDegrees: [-114.25, 43.5],
      // `mean`, not `count`: a derived row carries averaged temperature, humidity and wind, not a
      // tally of the samples behind them.
      aggregationMethod: "mean",
    });
  });

  it("uses the release route and preserves drought's served release day", async () => {
    mockedRelease.mockResolvedValue(
      published(
        "2026-08-24",
        [
          {
            area_id: "area-1",
            valid_date: "2026-08-18",
            dm_category: 2,
            source_url: "https://droughtmonitor.unl.edu/",
            ingested_at: "2026-08-20T12:00:00Z",
            geom: JSON.stringify({
              type: "Polygon",
              coordinates: [
                [
                  [-120, 40],
                  [-119, 40],
                  [-119, 41],
                  [-120, 40],
                ],
              ],
            }),
          },
        ],
        "2026-08-18"
      )
    );

    const result = await getParquetDrought({
      bbox: "-125,42,-111,49",
      date: "2026-08-24",
      mapZoom: 13,
      nowMs: Date.parse("2026-08-24T12:00:00Z"),
    });

    expect(mockedRelease).toHaveBeenCalledWith({
      layer: "drought",
      asOfDay: "2026-08-24",
      zoomTier: 13,
      bbox: "-125,42,-111,49",
    });
    expect(result).toMatchObject({
      state: "ready",
      requestedDay: "2026-08-24",
      servedDay: "2026-08-18",
      data: [{ droughtCategory: 2, geometry: { type: "Polygon" } }],
    });
  });

  it("does not carry a drought release across a skipped historical release week", async () => {
    mockedRelease
      .mockResolvedValueOnce(published("2026-08-12", [], "2026-08-04"))
      .mockResolvedValueOnce(published("2026-08-24", [], "2026-08-18"));

    await expect(
      getParquetDrought({
        date: "2026-08-12",
        mapZoom: 13,
        nowMs: Date.parse("2026-08-24T12:00:00Z"),
      })
    ).resolves.toEqual({
      state: "not_generated",
      requestedDay: "2026-08-12",
      reason: "day_not_written",
    });
    expect(mockedRelease).toHaveBeenNthCalledWith(2, {
      layer: "drought",
      asOfDay: "2026-08-24",
      zoomTier: 13,
    });
  });

  it("does not carry an arbitrarily old newest drought release at the live edge", async () => {
    mockedRelease.mockResolvedValue(published("2026-08-24", [], "2026-08-09"));

    await expect(
      getParquetDrought({
        date: "2026-08-24",
        mapZoom: 13,
        nowMs: Date.parse("2026-08-24T12:00:00Z"),
      })
    ).resolves.toEqual({
      state: "not_generated",
      requestedDay: "2026-08-24",
      reason: "day_not_written",
    });
  });

  it("allows the newest drought release through the exact 14-day live-edge bound", async () => {
    mockedRelease.mockResolvedValue(published("2026-08-24", [], "2026-08-10"));

    await expect(
      getParquetDrought({
        date: "2026-08-24",
        mapZoom: 13,
        nowMs: Date.parse("2026-08-24T12:00:00Z"),
      })
    ).resolves.toMatchObject({
      state: "ready",
      requestedDay: "2026-08-24",
      servedDay: "2026-08-10",
    });
  });

  it("rejects future drought and vegetation days before calling the Parquet plane", async () => {
    const nowMs = Date.parse("2026-08-24T12:00:00Z");

    await expect(
      getParquetDrought({ date: "2026-08-25", mapZoom: 13, nowMs })
    ).rejects.toBeInstanceOf(ParquetPlaneRequestError);
    await expect(
      getParquetVegetation({
        bbox: "-125,42,-111,49",
        date: "2026-08-25",
        mapZoom: 13,
        nowMs,
      })
    ).rejects.toBeInstanceOf(ParquetPlaneRequestError);
    expect(mockedRelease).not.toHaveBeenCalled();
    expect(mockedWindow).not.toHaveBeenCalled();
  });

  it("reads vegetation through a trailing 30-day window and keeps the newest cell value", async () => {
    mockedWindow.mockResolvedValue([
      published("2026-07-22", [vegetationRow("2026-07-22", 0.31)]),
      { state: "day_not_written", requestedDay: "2026-08-19" },
      published("2026-08-20", [vegetationRow("2026-08-20", 0.72)]),
    ]);

    const result = await getParquetVegetation({
      bbox: "-125,42,-111,49",
      date: "2026-08-20",
      mapZoom: 7,
    });

    expect(mockedWindow).toHaveBeenCalledWith({
      layer: "vegetation",
      firstDay: "2026-07-22",
      lastDay: "2026-08-20",
      zoomTier: 5,
      bbox: "-125,42,-111,49",
    });
    expect(result).toMatchObject({
      state: "ready",
      data: {
        observations: [{ observedDay: "2026-08-20", metricValue: 0.72 }],
        days: [{ state: "ready" }, { state: "not_generated" }, { state: "ready" }],
      },
    });
    // `tessellated_cell` at 0.25 degrees, never `raw_point`: the contract pins this lane's support
    // at the ground it measured, and a centre dot for a quarter-degree cell is exactly the
    // fictitious finer footprint `declaredSupportDegrees` exists to forbid. The z5 grid (0.2) is
    // finer than that, so this rung keeps the base cell rather than shrinking it.
    expect(readyData(result).observations[0].support).toMatchObject({
      zoomTier: 5,
      supportKind: "tessellated_cell",
      origin: "cell_origin",
      cellWidthDegrees: 0.25,
      cellHeightDegrees: 0.25,
      aggregationMethod: "mean",
      contributorCount: 1,
      provenance: {
        sourceLayer: "vegetation",
        observedDay: "2026-08-20",
        // An AVAILABILITY instant is not an observation instant, so the envelope says null rather
        // than passing off a publication time as a measurement time.
        newestObservedAt: null,
      },
    });
  });

  it("fails the whole vegetation read closed when a row is not approved for client exposure", async () => {
    mockedWindow.mockResolvedValue([
      published("2026-08-20", [
        { ...vegetationRow("2026-08-20", 0.72), allowed_client_exposure: false },
      ]),
    ]);

    await expect(
      getParquetVegetation({
        bbox: "-125,42,-111,49",
        date: "2026-08-20",
        mapZoom: 7,
        nowMs: Date.parse("2026-08-24T12:00:00Z"),
      })
    ).resolves.toMatchObject({
      state: "upstream_unavailable",
      fault: { kind: "contract" },
    });
  });

  it("uses an exact one-day fire window for a named day", async () => {
    mockedWindow.mockResolvedValue([
      published("2026-08-18", [
        {
          cell_longitude: -114.2,
          cell_latitude: 43.5,
          observed_day: "2026-08-18",
          detection_count: 4,
          frp_sum: 18.5,
          frp_observation_count: 3,
          high_confidence_detection_count: 2,
          newest_observed_at: "2026-08-18T22:15:00Z",
        },
      ]),
    ]);

    const result = await getParquetFireDetections({
      date: "2026-08-18",
      dayRange: 10,
      mapZoom: 3,
    });

    expect(mockedWindow).toHaveBeenCalledWith({
      layer: "fire-detections",
      firstDay: "2026-08-18",
      lastDay: "2026-08-18",
      zoomTier: 0,
    });
    expect(result).toMatchObject({ state: "ready", data: { cells: [{ detectionCount: 4 }] } });
    // `aggregate_cell` at EVERY rung, the detail one included: FIRMS publishes no raw rung, so
    // even a z13 row is a 0.005-degree detection-density cell rather than one hotspot.
    expect(readyData(result).cells[0].support).toEqual({
      zoomTier: 0,
      supportKind: "aggregate_cell",
      supportId: "0:-114.2:43.5",
      origin: "cell_origin",
      cellWidthDegrees: 5,
      cellHeightDegrees: 5,
      // The corner `servedCellLattice` places this row on, stated by the reader rather than
      // re-derived by the client -- which is the whole point of the field.
      //
      // The latitude is worth reading twice: this fixture's 43.5 is NOT a z0 served coordinate
      // (the fire export floors onto the 5-degree grid and writes 40 or 45, never 43.5), and
      // `latticeCellIndex` recovers the index of the cell a SERVED coordinate denotes by adding
      // back half a grid step -- so it answers 45 here, the cell 43.5 would have been floored
      // INTO had it been half a step lower. Both sides now agree on that answer, where before
      // this the client drew [43.5, 48.5] and the server drew [45, 50] for the same row.
      cellOriginDegrees: [-115, 45],
      aggregationMethod: "count",
      contributorCount: 4,
      provenance: {
        sourceLayer: "fire-detections",
        observedDay: "2026-08-18",
        newestObservedAt: "2026-08-18T22:15:00Z",
        attribution: "NASA FIRMS (LANCE/ESDIS)",
      },
    });
  });

  it("serves the requested ERA5-Land moisture depth from its exact Parquet day", async () => {
    mockedDay.mockResolvedValue(published("2026-08-02", [soilMoistureRow()]));

    const result = await getParquetSoilField("-125,42,-111,49", {
      measure: "moisture",
      depth: "root-zone",
      date: "2026-08-02",
      zoom: 13,
    });

    expect(mockedDay).toHaveBeenCalledWith({
      layer: "soil-field-moisture-7-28cm",
      day: "2026-08-02",
      zoomTier: 13,
      bbox: "-125,42,-111,49",
    });
    expect(result).toMatchObject({
      availability: "published",
      reason: null,
      measure: "moisture",
      depth: "root-zone",
      observedDay: "2026-08-02",
      requestedDay: "2026-08-02",
      granularity: "detail",
      maxObservationAgeDays: 0,
      sourceClientExposureApproved: false,
      features: [
        {
          id: "era5-land:42.125:-124.875",
          // The centroid's OWN cell: edges on the multiples of 0.25, centroid in the middle of it.
          geometry: {
            type: "Polygon",
            coordinates: [
              [
                [-125, 42],
                [-124.75, 42],
                [-124.75, 42.25],
                [-125, 42.25],
                [-125, 42],
              ],
            ],
          },
          properties: { value: 0.23, aggregated: false },
        },
      ],
    });
    // ONE envelope for the whole collection: every cell here shares the rung, the pitch, the
    // origin semantics and the attribution, and the part that varies is already on each feature
    // as `cellKey`. `supportId` names the LATTICE -- one lane, one day, one rung.
    expect(result.support).toEqual({
      zoomTier: 13,
      supportKind: "tessellated_cell",
      supportId: "soil-field-moisture-7-28cm:2026-08-02:z13",
      origin: "cell_center",
      cellWidthDegrees: 0.25,
      cellHeightDegrees: 0.25,
      aggregationMethod: "mean",
      contributorCount: 2,
      provenance: {
        sourceLayer: "soil-field-moisture-7-28cm",
        observedDay: "2026-08-02",
        newestObservedAt: "2026-08-02T00:00:00Z",
        attribution: "ERA5-Land (Copernicus/ECMWF) via Open-Meteo, CC-BY 4.0",
      },
    });
  });

  it("serves a VPD aggregate from its selected rung without a PostgreSQL fallback", async () => {
    mockedDay.mockResolvedValue(published("2026-08-02", [soilVpdRow()]));

    const result = await getParquetSoilField("-125,35,-105,50", {
      measure: "vpd",
      date: "2026-08-02",
      zoom: 5,
    });

    expect(mockedDay).toHaveBeenCalledWith({
      layer: "soil-field-vpd",
      day: "2026-08-02",
      zoomTier: 5,
      bbox: "-125,35,-105,50",
    });
    expect(result).toMatchObject({
      availability: "published",
      measure: "vpd",
      depth: "surface",
      granularity: "coarse-average",
      // 0.25, not the ladder's 0.2. The z5 grid is FINER than this lane's quarter-degree cell, so
      // the rung merged nothing and drawing 0.2-degree cells from it would both shrink the
      // measurement's footprint and leave one lattice column in five empty -- 0.2 does not divide
      // 0.25, which is precisely how background showed through the ERA5 field.
      latticeDegrees: 0.25,
      smoothingSigmaDegrees: null,
      features: [
        {
          // The z5 coordinate is recovered onto the BASE cell it came from, edges on the
          // multiples of 0.25 -- the same square the z13 rung draws for the same measurement.
          geometry: {
            type: "Polygon",
            coordinates: [
              [
                [-125, 42],
                [-124.75, 42],
                [-124.75, 42.25],
                [-125, 42.25],
                [-125, 42],
              ],
            ],
          },
          properties: { value: 1.7, aggregated: true, cellKey: null },
        },
      ],
    });
    expect(result.support).toMatchObject({
      zoomTier: 5,
      supportKind: "tessellated_cell",
      origin: "cell_origin",
      cellWidthDegrees: 0.25,
      cellHeightDegrees: 0.25,
    });
  });

  it("does not carry an earlier soil release across a governed gap", async () => {
    mockedDay.mockResolvedValue({
      state: "governed_absence",
      requestedDay: "2026-08-02",
      servedDay: "2026-08-02",
      evidence,
    });

    await expect(
      getParquetSoilField("-125,42,-111,49", {
        measure: "moisture",
        depth: "root-zone",
        date: "2026-08-02",
        zoom: 13,
      })
    ).resolves.toMatchObject({
      availability: "unavailable",
      reason: "not_published",
      newestAvailableDay: null,
      features: [],
    });
    expect(mockedRelease).not.toHaveBeenCalled();
  });

  it("fails closed when a moisture row belongs to a different signal contract", async () => {
    mockedDay.mockResolvedValue(
      published("2026-08-02", [
        soilMoistureRow({ signal_name: "soil_water_content_layer_1" }),
      ])
    );

    await expect(
      getParquetSoilField("-125,42,-111,49", {
        measure: "moisture",
        depth: "root-zone",
        date: "2026-08-02",
        zoom: 13,
      })
    ).rejects.toBeInstanceOf(ParquetPlaneContractError);
  });

  it("fails moisture from the wrong depth parameter closed", async () => {
    mockedDay.mockResolvedValue(
      published("2026-08-02", [
        soilMoistureRow({ source_parameter: "soil_moisture_0_to_7cm_mean" }),
      ])
    );

    await expect(
      getParquetSoilField("-125,42,-111,49", {
        measure: "moisture",
        depth: "root-zone",
        date: "2026-08-02",
        zoom: 13,
      })
    ).rejects.toBeInstanceOf(ParquetPlaneContractError);
  });

  it("fails moisture from another canonical snapshot manifest closed", async () => {
    mockedDay.mockResolvedValue(
      published("2026-08-02", [
        soilMoistureRow({ source_manifest_sha256: "f".repeat(64) }),
      ])
    );

    await expect(
      getParquetSoilField("-125,42,-111,49", {
        measure: "moisture",
        depth: "root-zone",
        date: "2026-08-02",
        zoom: 13,
      })
    ).rejects.toBeInstanceOf(ParquetPlaneContractError);
  });

  it("serves a monthly soil-temperature snapshot through the exact-day route", async () => {
    mockedDay.mockResolvedValue(published("2026-08-02", [soilTemperatureRow()]));

    const result = await getParquetSoilField("-125,42,-111,49", {
      measure: "temperature",
      depth: "deep",
      date: "2026-08-02",
      zoom: 13,
    });

    expect(mockedDay).toHaveBeenCalledWith({
      layer: "soil-temperature-28-to-100cm",
      day: "2026-08-02",
      zoomTier: 13,
      bbox: "-125,42,-111,49",
    });
    expect(result).toMatchObject({
      availability: "published",
      measure: "temperature",
      depth: "deep",
      observedDay: "2026-08-02",
      features: [{ properties: { value: 21.5 } }],
    });
  });

  it("fails soil temperature from the wrong depth parameter closed", async () => {
    mockedDay.mockResolvedValue(
      published("2026-08-02", [
        soilTemperatureRow({ source_parameter: "soil_temperature_7_to_28cm_mean" }),
      ])
    );

    await expect(
      getParquetSoilField("-125,42,-111,49", {
        measure: "temperature",
        depth: "deep",
        date: "2026-08-02",
        zoom: 13,
      })
    ).rejects.toBeInstanceOf(ParquetPlaneContractError);
  });

  it("returns an explicit not-forecastable soil collection without calling the Parquet service", async () => {
    await expect(
      getParquetSoilField("-125,42,-111,49", {
        measure: "vpd",
        date: "9999-01-01",
        zoom: 13,
      })
    ).resolves.toMatchObject({
      availability: "unavailable",
      reason: "not_forecastable",
      requestedDay: "9999-01-01",
    });
    expect(mockedRelease).not.toHaveBeenCalled();
    expect(mockedDay).not.toHaveBeenCalled();
  });
});

/*
 * The environmental_postgres_retirement_20260904 track: the five layers that moved off Martin's
 * tile functions. Each case below is a PARITY claim about what the retired function returned,
 * stated against the fake Parquet plane above rather than against a live database:
 *
 *   - geo.sensor_tiles()          DISTINCT ON (sensor_id, geom, observation_day), four attributes
 *   - geo.evacuation_zone_tiles() every published Oregon OEM row, unconditionally
 *   - geo.burn_severity_tiles()   every published MTBS scar, at every zoom, unsimplified
 *   - geo.watershed_tiles()       HUC12 at z>=10, the rollup below it, huc_level naming the rung
 *   - geo.fire_risk_tiles()       every published WFIGS incident, plus the `observed_day <= day`
 *                                 style filter the client applied on top of it -- undated rows
 *                                 INCLUDED, because ST_AsMVT emitted no attribute for them and
 *                                 `["!", ["has", "observed_day"]]` keeps them at every date
 *
 * The last of the five arrived with lane FP3, once its lane was re-registered `static_lookup`.
 * While it was `daily_series` on a per-incident observation day its 177 perimeters sat across 45
 * partition days and no bounded read reproduced their union; that is history now, and the two
 * in-frame cases below are what stops it being rebuilt by accident.
 */

function evacuationZoneRow(overrides: Record<string, unknown> = {}) {
  return {
    global_id: "{9E0C-1}",
    natural_key: "or-oem:9E0C-1",
    producer: "or-oem-evacuation-areas",
    snapshot_day: "2026-08-25",
    evacuation_area_name: "Camp Creek Zone 3",
    fire_name: "Camp Creek Fire",
    county: "Lane",
    hazard_type: "wildfire",
    evacuation_level: 3,
    evacuation_level_label: "Level 3 - Go Now",
    severity: "critical",
    structures_within: 412,
    addresses_within: 388,
    population_within: 1104,
    editor_name: "OEM GIS",
    observed_at: "2026-08-24T19:02:00Z",
    source: "or-oem",
    geometry_wkb: JSON.stringify({
      type: "Polygon",
      coordinates: [
        [
          [-122.4, 43.9],
          [-122.3, 43.9],
          [-122.3, 44.0],
          [-122.4, 43.9],
        ],
      ],
    }),
    geometry_version_id: "geom-1",
    geometry_version_valid_from: "2026-08-01T00:00:00Z",
    geometry_last_confirmed_at: "2026-08-24T19:02:00Z",
    data_available_at: "2026-08-24T19:05:00Z",
    feature_updated_at: "2026-08-24T19:05:00Z",
    ...overrides,
  };
}

function burnScarRow(overrides: Record<string, unknown> = {}) {
  return {
    feature_id: "feat-1",
    fire_id: "OR4318712201820180722",
    natural_key: "mtbs:OR4318712201820180722",
    release_identifier: "mtbs-2024",
    mapping_revision: "1",
    fire_year: 2018,
    ignition_date: "2018-07-22",
    observed_day: "2024-03-01",
    data_available_at: "2024-03-01T00:00:00Z",
    fire_name: "Terwilliger",
    fire_type: "Wildfire",
    assessment_type: "Extended",
    acres: 11419,
    severity_class: null,
    dnbr_offset: 12,
    dnbr_standard_deviation: 30,
    nodata_threshold: -970,
    greenness_threshold: -150,
    low_threshold: 90,
    moderate_threshold: 320,
    high_threshold: 670,
    allowed_client_exposure: false,
    geom: JSON.stringify({
      type: "Polygon",
      coordinates: [
        [
          [-122.2, 44.0],
          [-122.1, 44.0],
          [-122.1, 44.1],
          [-122.2, 44.0],
        ],
      ],
    }),
    ...overrides,
  };
}

function watershedRow(overrides: Record<string, unknown> = {}) {
  return {
    huc12: "170501220201",
    name: "Cottonwood Creek-Shafer Creek",
    areasqkm: 118.4,
    tohuc: "170501220202",
    states: "ID",
    hutype: "S",
    source: "USGS NHDPlus HR WBDHU12",
    observed_at: "2013-01-01T00:00:00Z",
    data_available_at: "2026-08-07T00:00:00Z",
    release_day: "2026-08-07",
    feature_id: "feat-w1",
    geom: JSON.stringify({
      type: "Polygon",
      coordinates: [
        [
          [-116.3, 43.5],
          [-116.2, 43.5],
          [-116.2, 43.6],
          [-116.3, 43.5],
        ],
      ],
    }),
    ...overrides,
  };
}

function sensorRow(overrides: Record<string, unknown> = {}) {
  return {
    sensor_id: "KBOI",
    station_name: "Boise Air Terminal",
    network: "ASOS",
    observed_day: "2026-08-25",
    observed_at: "2026-08-25T18:53:00Z",
    measurement_name: "temperature",
    value: 31.1,
    unit_code: "wmoUnit:degC",
    quality_control: "V",
    feature_id: "feat-s1",
    data_available_at: "2026-08-25T19:00:00Z",
    station_longitude: -116.22,
    station_latitude: 43.56,
    ...overrides,
  };
}

/**
 * One WFIGS incident as the re-registered `static_lookup` lane publishes it.
 *
 * Every registered arrow column (`warehouse/schemas/fire_perimeters.py`), because the reader's
 * schema is `.strict()`: a fixture short of one column would pass for the wrong reason, and one
 * carrying an extra column is what the strictness case below asserts is rejected. `snapshot_day`
 * is the VERSION stamp and `observed_day` the incident's own date -- deliberately different values
 * here, since a fixture where the two agree cannot tell them apart.
 */
function firePerimeterRow(overrides: Record<string, unknown> = {}) {
  return {
    feature_id: "3f1c9a52-0d1e-4a2c-9c0f-2f6d5b8a7e11",
    unique_fire_identifier: "2026-ORWIF-000412",
    snapshot_day: "2026-08-25",
    observed_day: "2026-08-24",
    incident_name: "Camp Creek",
    irwin_id: "{2C7A-9F}",
    fire_discovery_at: "2026-08-18T14:20:00Z",
    polygon_at: "2026-08-24T09:05:00Z",
    gis_acres: 8412.5,
    fire_cause: "Natural",
    incident_type_category: "WF",
    poo_state: "US-OR",
    percent_contained: 35,
    severity: "high",
    status: "published",
    data_available_at: null,
    updated_at: "2026-08-24T09:30:00Z",
    geometry_wkb: JSON.stringify({
      type: "Polygon",
      coordinates: [
        [
          [-122.5, 43.8],
          [-122.4, 43.8],
          [-122.4, 43.9],
          [-122.5, 43.8],
        ],
      ],
    }),
    ...overrides,
  };
}

describe("the five layers that left Martin's tile functions", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("reads evacuation zones as one release at the camera's rung, geometry decoded", async () => {
    mockedRelease.mockResolvedValue(
      published("2026-08-26", [evacuationZoneRow()], "2026-08-25")
    );

    const result = await getParquetEvacuationZones({
      bbox: "-125,42,-111,49",
      date: "2026-08-26",
      mapZoom: 7,
      nowMs: Date.parse("2026-08-26T12:00:00Z"),
    });

    // z7 resolves to the z5 rung, which is the point of the cutover: the tile function served
    // identical unsimplified geometry at every zoom.
    expect(mockedRelease).toHaveBeenCalledWith({
      layer: "evacuation-zones",
      asOfDay: "2026-08-26",
      zoomTier: 5,
      bbox: "-125,42,-111,49",
    });
    expect(result).toMatchObject({
      state: "ready",
      requestedDay: "2026-08-26",
      servedDay: "2026-08-25",
      data: [
        {
          naturalKey: "or-oem:9E0C-1",
          severity: "critical",
          evacuationLevelLabel: "Level 3 - Go Now",
          structuresWithin: 412,
          geometry: { type: "Polygon" },
        },
      ],
    });
  });

  it("reads fire perimeters as the newest snapshot at or before the day, at the camera's rung", async () => {
    mockedRelease.mockResolvedValue(
      published("2026-08-26", [firePerimeterRow()], "2026-08-25")
    );

    const result = await getParquetFirePerimeters({
      bbox: "-125,42,-111,49",
      date: "2026-08-26",
      mapZoom: 7,
      nowMs: Date.parse("2026-08-26T12:00:00Z"),
    });

    // The RELEASE route, not the day route: a `static_lookup` snapshot resolves as "newest at or
    // before", the same rule `resolve_fire_perimeters_as_of` states for the Polars path. z7
    // resolves to the z5 rung -- the retired tile function served identical unsimplified geometry
    // at every zoom.
    expect(mockedRelease).toHaveBeenCalledWith({
      layer: "fire-perimeters",
      asOfDay: "2026-08-26",
      zoomTier: 5,
      bbox: "-125,42,-111,49",
    });
    expect(mockedDay).not.toHaveBeenCalled();
    expect(result).toMatchObject({
      state: "ready",
      // The day asked for and the day that answered are different values and stay different: one
      // is the slider date, the other the snapshot's capture day.
      requestedDay: "2026-08-26",
      servedDay: "2026-08-25",
      data: [
        {
          featureId: "3f1c9a52-0d1e-4a2c-9c0f-2f6d5b8a7e11",
          uniqueFireIdentifier: "2026-ORWIF-000412",
          snapshotDay: "2026-08-25",
          observedDay: "2026-08-24",
          severity: "high",
          geometry: { type: "Polygon" },
        },
      ],
    });
  });

  it("keeps an incident WFIGS never dated, and drops one whose day is still ahead", async () => {
    mockedRelease.mockResolvedValue(
      published(
        "2026-08-01",
        [
          firePerimeterRow({
            unique_fire_identifier: "undated",
            snapshot_day: "2026-08-01",
            observed_day: null,
          }),
          firePerimeterRow({
            unique_fire_identifier: "later",
            snapshot_day: "2026-08-01",
            observed_day: "2026-08-06",
          }),
        ],
        "2026-08-01"
      )
    );

    const result = await getParquetFirePerimeters({
      date: "2026-08-01",
      mapZoom: 13,
      nowMs: Date.parse("2026-08-10T12:00:00Z"),
    });

    // `observed_day IS NULL` is never excluded. `geo.feature_observation_day` returns NULL for a
    // row it cannot date, and `tileLayerDateFilter` keeps such a row at EVERY date with
    // `["!", ["has", "observed_day"]]`; the retired daily_series export deleted them outright,
    // because its `= :observed_day` predicate can never match NULL.
    expect(readyData(result).map((row) => row.uniqueFireIdentifier)).toEqual(["undated"]);
    expect(readyData(result)[0]?.observedDay).toBeNull();
  });

  it("filters in frame against the REQUESTED day, never the snapshot day that answered", async () => {
    // The TypeScript twin of `test_the_in_frame_filter_uses_the_requested_as_of_not_the_answering
    // _snapshot_day` (tests/parquet/test_fire_perimeters_serving.py:541). ONE snapshot, captured
    // on the 1st, holding one incident dated the 6th. Both requests resolve to that same snapshot,
    // so the only thing differing between them is the requested day -- and that alone flips the
    // incident from out of frame to in frame. A filter comparing against the snapshot's own
    // capture day would answer zero both times, which is the retired `== observed_day` equality
    // bug wearing a new hat.
    mockedRelease.mockResolvedValue(
      published(
        "2026-08-10",
        [firePerimeterRow({ snapshot_day: "2026-08-01", observed_day: "2026-08-06" })],
        "2026-08-01"
      )
    );

    const beforeTheObservedDay = await getParquetFirePerimeters({
      date: "2026-08-01",
      mapZoom: 13,
      nowMs: Date.parse("2026-08-10T12:00:00Z"),
    });
    const atOrAfterTheObservedDay = await getParquetFirePerimeters({
      date: "2026-08-10",
      mapZoom: 13,
      nowMs: Date.parse("2026-08-10T12:00:00Z"),
    });

    expect(readyData(beforeTheObservedDay)).toHaveLength(0);
    expect(readyData(atOrAfterTheObservedDay)).toHaveLength(1);
    // The SAME answering snapshot in both, stated rather than assumed: without this the case
    // could pass because the two requests happened to resolve to different releases.
    expect(beforeTheObservedDay).toMatchObject({ servedDay: "2026-08-01" });
    expect(atOrAfterTheObservedDay).toMatchObject({ servedDay: "2026-08-01" });
  });

  it("fails a fire-perimeter row carrying an unregistered column closed", async () => {
    mockedRelease.mockResolvedValue(
      published("2026-08-26", [firePerimeterRow({ risk_level: "extreme" })], "2026-08-25")
    );

    // `risk_level` is precisely the attribute `geo.fire_risk_tiles()` projected from a JSONB key
    // no producer has ever written. A column appearing upstream must fail this reader loudly
    // rather than arrive unread.
    await expect(
      getParquetFirePerimeters({
        date: "2026-08-26",
        mapZoom: 13,
        nowMs: Date.parse("2026-08-26T12:00:00Z"),
      })
    ).resolves.toMatchObject({
      state: "upstream_unavailable",
      fault: { kind: "contract" },
    });
  });

  it("unions every burn-severity release at or before the day, walking back by served day", async () => {
    mockedRelease
      .mockResolvedValueOnce(
        published("2026-08-26", [burnScarRow({ fire_id: "newest" })], "2024-03-01")
      )
      .mockResolvedValueOnce(
        published("2024-02-29", [burnScarRow({ fire_id: "older" })], "2022-05-10")
      )
      .mockResolvedValueOnce({
        state: "day_not_written" as const,
        requestedDay: "2022-05-09",
      });

    const result = await getParquetBurnSeverity({
      date: "2026-08-26",
      mapZoom: 13,
      nowMs: Date.parse("2026-08-26T12:00:00Z"),
    });

    // Each step asks for the day BEFORE the release just served. Asking for requestedDay minus a
    // day would re-serve the newest release forever.
    expect(mockedRelease).toHaveBeenNthCalledWith(2, {
      layer: "burn-severity",
      asOfDay: "2024-02-29",
      zoomTier: 13,
    });
    expect(mockedRelease).toHaveBeenCalledTimes(3);
    expect(result).toMatchObject({
      state: "ready",
      requestedDay: "2026-08-26",
      // The freshest release in the union names the day; an older member does not age the answer.
      servedDay: "2024-03-01",
      truncated: false,
    });
    expect(readyData(result).map((scar) => scar.fireId)).toEqual(["newest", "older"]);
  });

  it("reports the plane's own refusal when no burn-severity release precedes the day", async () => {
    mockedRelease.mockResolvedValue({
      state: "lane_never_written" as const,
      requestedDay: "2026-08-26",
    });

    await expect(
      getParquetBurnSeverity({
        date: "2026-08-26",
        mapZoom: 13,
        nowMs: Date.parse("2026-08-26T12:00:00Z"),
      })
    ).resolves.toEqual({
      state: "not_generated",
      requestedDay: "2026-08-26",
      reason: "lane_never_written",
    });
  });

  it("bounds the burn-severity walk and says so rather than dropping the oldest releases", async () => {
    // Every call answers with a release one day older, so the walk can only end at its ceiling.
    let servedDay = "2026-08-20";
    mockedRelease.mockImplementation(async () => {
      const answer = published("2026-08-26", [burnScarRow({ fire_id: servedDay })], servedDay);
      servedDay = new Date(Date.parse(servedDay + "T00:00:00Z") - 86_400_000)
        .toISOString()
        .slice(0, 10);
      return answer;
    });

    const result = await getParquetBurnSeverity({
      date: "2026-08-26",
      mapZoom: 13,
      nowMs: Date.parse("2026-08-26T12:00:00Z"),
    });

    expect(mockedRelease).toHaveBeenCalledTimes(12);
    expect(result).toMatchObject({ state: "ready", truncated: true });
    expect(readyData(result)).toHaveLength(12);
  });

  it("names the HUC rung from the code's own length, never from the zoom asked for", async () => {
    mockedRelease.mockResolvedValue(
      published(
        "2026-08-26",
        [
          watershedRow({
            huc12: "1705012202",
            name: null,
            tohuc: null,
            states: null,
            feature_id: null,
          }),
        ],
        "2026-08-07"
      )
    );

    const result = await getParquetWatersheds({
      bbox: "-125,42,-111,49",
      date: "2026-08-26",
      mapZoom: 10,
      nowMs: Date.parse("2026-08-26T12:00:00Z"),
    });

    expect(mockedRelease).toHaveBeenCalledWith({
      layer: "watersheds",
      asOfDay: "2026-08-26",
      zoomTier: 9,
      bbox: "-125,42,-111,49",
    });
    expect(readyData(result)).toEqual([
      expect.objectContaining({ huc: "1705012202", hucLevel: 10, name: null, toHuc: null }),
    ]);
  });

  it("collapses the tall sensor grain to one station, keeping its newest reading", async () => {
    mockedDay.mockResolvedValue(
      published("2026-08-25", [
        sensorRow({ measurement_name: "temperature", observed_at: "2026-08-25T17:53:00Z" }),
        sensorRow({
          measurement_name: "relative_humidity",
          value: 24,
          observed_at: "2026-08-25T18:53:00Z",
        }),
        sensorRow({
          sensor_id: "KTWF",
          station_name: "Twin Falls",
          station_longitude: -114.48,
          station_latitude: 42.48,
        }),
      ])
    );

    const result = await getParquetSensorStations({
      bbox: "-125,42,-111,49",
      date: "2026-08-25",
      mapZoom: 13,
      nowMs: Date.parse("2026-08-25T20:00:00Z"),
    });

    const stations = readyData(result);
    expect(stations.map((station) => station.sensorId)).toEqual(["KBOI", "KTWF"]);
    // One dot per station, exactly as DISTINCT ON (sensor_id, geom, observation_day) gave, and
    // the station's own timestamp is its newest reading of the day.
    expect(stations[0]?.measurements).toHaveLength(2);
    expect(stations[0]?.observedAt).toBe("2026-08-25T18:53:00Z");
  });

  it("merges a coarse sensor rung on its cell, because no station identity survives there", async () => {
    mockedDay.mockResolvedValue(
      published("2026-08-25", [
        sensorRow({ sensor_id: null, station_name: null, measurement_name: "temperature" }),
        sensorRow({
          sensor_id: null,
          station_name: null,
          measurement_name: "wind_speed",
          value: 3.4,
        }),
      ])
    );

    const result = await getParquetSensorStations({
      bbox: "-125,42,-111,49",
      date: "2026-08-25",
      mapZoom: 5,
      nowMs: Date.parse("2026-08-25T20:00:00Z"),
    });

    expect(mockedDay).toHaveBeenCalledWith({
      layer: "sensors",
      day: "2026-08-25",
      zoomTier: 5,
      bbox: "-125,42,-111,49",
    });
    const stations = readyData(result);
    expect(stations).toHaveLength(1);
    expect(stations[0]?.sensorId).toBeNull();
    expect(stations[0]?.measurements.map((measurement) => measurement.name)).toEqual([
      "temperature",
      "wind_speed",
    ]);
  });

  it("drops a sensor row with no coordinates rather than plotting a fabricated origin", async () => {
    mockedDay.mockResolvedValue(
      published("2026-08-25", [
        sensorRow({ sensor_id: "KNOWHERE", station_longitude: null, station_latitude: null }),
      ])
    );

    await expect(
      getParquetSensorStations({
        date: "2026-08-25",
        mapZoom: 13,
        nowMs: Date.parse("2026-08-25T20:00:00Z"),
      })
    ).resolves.toMatchObject({ state: "ready", data: [] });
  });

  it("fails closed on a lane column the registered schema does not declare", async () => {
    mockedRelease.mockResolvedValue(
      published("2026-08-26", [watershedRow({ unexpected_column: 1 })], "2026-08-07")
    );

    await expect(
      getParquetWatersheds({
        date: "2026-08-26",
        mapZoom: 13,
        nowMs: Date.parse("2026-08-26T12:00:00Z"),
      })
    ).resolves.toMatchObject({ state: "upstream_unavailable", fault: { kind: "contract" } });
  });

  it("rejects a future day for all four before calling the Parquet plane", async () => {
    const nowMs = Date.parse("2026-08-26T12:00:00Z");

    await expect(
      getParquetEvacuationZones({ date: "2026-08-27", mapZoom: 13, nowMs })
    ).rejects.toBeInstanceOf(ParquetPlaneRequestError);
    await expect(
      getParquetBurnSeverity({ date: "2026-08-27", mapZoom: 13, nowMs })
    ).rejects.toBeInstanceOf(ParquetPlaneRequestError);
    await expect(
      getParquetWatersheds({ date: "2026-08-27", mapZoom: 13, nowMs })
    ).rejects.toBeInstanceOf(ParquetPlaneRequestError);
    await expect(
      getParquetSensorStations({ date: "2026-08-27", mapZoom: 13, nowMs })
    ).rejects.toBeInstanceOf(ParquetPlaneRequestError);
    expect(mockedRelease).not.toHaveBeenCalled();
    expect(mockedDay).not.toHaveBeenCalled();
  });

  it("reports an upstream outage as data, so the map can caption it instead of blanking", async () => {
    mockedRelease.mockRejectedValue(new UpstreamTimeoutError("burn-severity read timed out"));

    await expect(
      getParquetBurnSeverity({
        date: "2026-08-26",
        mapZoom: 13,
        nowMs: Date.parse("2026-08-26T12:00:00Z"),
      })
    ).resolves.toMatchObject({
      state: "upstream_unavailable",
      fault: { kind: "timeout" },
    });
  });
});
