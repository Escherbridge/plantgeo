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
  getParquetDrought,
  getParquetClimateField,
  type ParquetReaderResult,
  getParquetFireDetections,
  getParquetSoilField,
  getParquetVegetation,
  getParquetWaterGauges,
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
    cell_longitude: -114.25,
    cell_latitude: 43.5,
  };
}

function soilMoistureRow(overrides: Record<string, unknown> = {}) {
  return {
    support_key: "era5-land-0.1deg",
    signal_name: "soil_water_content_layer_2",
    normalized_unit: "m^3/m^3",
    cell_id: "era5-land:43.5:-114.25",
    observed_day: "2026-08-02",
    normalized_value: 0.23,
    observation_count: 2,
    newest_observed_at: "2026-08-02T00:00:00Z",
    coverage_fraction: 1,
    allowed_client_exposure: false,
    cell_longitude: -114.25,
    cell_latitude: 43.5,
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
    cell_longitude: -115,
    cell_latitude: 40,
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
    ...signalPlaneClimateRow("soil_temperature_level_3", "C", {
      support_key: "era5-land-0.1deg",
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
          id: "era5-land:43.5:-114.25",
          geometry: {
            type: "Polygon",
            coordinates: [
              [
                [-114.375, 43.375],
                [-114.125, 43.375],
                [-114.125, 43.625],
                [-114.375, 43.625],
                [-114.375, 43.375],
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
          geometry: {
            type: "Polygon",
            coordinates: [
              [
                [-115.125, 39.875],
                [-114.875, 39.875],
                [-114.875, 40.125],
                [-115.125, 40.125],
                [-115.125, 39.875],
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
