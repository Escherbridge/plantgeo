import { describe, expect, it } from "vitest";
import { buildRegionalMeasurementFacts } from "@/lib/server/services/regional-measurement-facts";

const DAY = "2026-09-14";
const BEFORE = "2026-08-14";
const AFTER = "2026-10-14";

function feature(value = 1.63, day = DAY) {
  return {
    covers_probe_point: true,
    spatial_relation: "contains_selection",
    support_bbox: [-116.25, 43.5, -116, 43.75],
    served_day: day,
    properties: {
      normalized_value: value, normalized_unit: "kPa", signal_name: "vapor_pressure_deficit",
      observed_day: day, newest_observed_at: `${day}T00:00:00+00:00`,
      cell_id: "a9cb82bd-2323-4736-88c6-d311075bd3cf", support_key: "era5-land-0.1deg",
      selected_source_part_key: "s3://private-bucket/record.parquet", input_source_row_digest: "a".repeat(64),
      observation_count: 1, allowed_client_exposure: true, coverage_fraction: 1,
    },
  };
}

function read(features: unknown[] = [feature()], source = "soil-field-vpd", id = "local-1") {
  return {
    id, source,
    result: {
      surface_name: source, requested_day: DAY,
      selection: { longitude: -116.2, latitude: 43.6, range_start: BEFORE, range_end: AFTER, tile: { z: 13, x: 1451, y: 2991 } },
      lanes: [{ parquet_lane: source, selected: { state: "published", requested_day: DAY, served_day: DAY, features }, history: [] as unknown[] }],
    },
  };
}

describe("server-authored regional measurement facts", () => {
  it("renders captured VPD fields, actual dates and containing source support without hashes or storage keys", () => {
    const { facts, omittedFacts } = buildRegionalMeasurementFacts([read()]);
    expect(facts).toHaveLength(1);
    expect(omittedFacts).toBe(0);
    expect(facts[0]).toMatchObject({ source: "soil-field-vpd", evidenceReadIds: ["local-1"] });
    expect(facts[0].id).toMatch(/^local-1:fact-1:[a-f0-9]{12}$/);
    expect(facts[0].statement).toContain('signal_name="vapor_pressure_deficit", normalized_value=1.63, normalized_unit="kPa"');
    expect(facts[0].statement).toContain(`Observed day ${DAY}; Served day ${DAY}`);
    expect(facts[0].statement).toContain("Source support contains selection");
    expect(facts[0].statement).toContain("Support bbox [-116.25,43.5,-116,43.75]");
    expect(facts[0].statement).not.toMatch(/cell_id|digest|s3:|observation_count|coverage_fraction/);
    expect(facts[0].statement.length).toBeLessThanOrEqual(500);
  });

  it("retains zero values and emits only published selected/history records", () => {
    const sample = read([feature(0)]);
    sample.result.lanes[0].history = [
      { state: "published", requested_day: BEFORE, served_day: BEFORE, features: [feature(0.5, BEFORE)] },
      { state: "day_not_written", requested_day: AFTER, features: [feature(99, AFTER)] },
      { state: "governed_absence", requested_day: AFTER, features: [] },
    ];
    const { facts } = buildRegionalMeasurementFacts([sample]);
    expect(facts).toHaveLength(2);
    expect(facts[0].statement).toContain("normalized_value=0,");
    expect(facts[1].statement).toContain("sampled history record");
    expect(facts[1].statement).toContain(`Observed day ${BEFORE}`);
    expect(facts.map((fact) => fact.statement).join(" ")).not.toMatch(/2026-10-14|no data|absence|trend|increase|decrease/);
  });

  it("keeps observed dates distinct from a later served release and the requested day", () => {
    const row = feature(0.72, BEFORE);
    row.served_day = DAY;
    const { facts } = buildRegionalMeasurementFacts([read([row])]);
    expect(facts[0].statement).toContain(`Observed day ${BEFORE}; Served day ${DAY}`);
  });

  it("reads the metric-name/value/unit contract without knowing the layer", () => {
    const { facts } = buildRegionalMeasurementFacts([read([{
      covers_probe_point: false,
      properties: { metric_name: "canopy_fraction", metric_value: 0.7123456789, metric_unit: "unitless", observed_day: BEFORE },
    }], "future-published-layer")]);
    expect(facts[0].source).toBe("future-published-layer");
    expect(facts[0].statement).toContain('metric_name="canopy_fraction", metric_value=0.7123456789, metric_unit="unitless"');
    expect(facts[0].statement).toContain("Spatial neighbor; does not contain selection");
  });

  it("renders generic numeric and categorical fields, including false, without interpretation", () => {
    const { facts } = buildRegionalMeasurementFacts([read([{
      properties: { discharge_cfs: 0, flood_condition: "not assessed", provisional: false, observed_day: DAY, site_number: "00123" },
    }], "another-map-layer")]);
    expect(facts[0].statement).toContain("discharge_cfs=0");
    expect(facts[0].statement).toContain('flood_condition="not assessed"');
    expect(facts[0].statement).toContain("provisional=false");
    expect(facts[0].statement).not.toMatch(/normal|low flow|no flood/);
  });

  it("supports botanical event intervals without fabricating a daily observation", () => {
    const sample = read([{
      observed_interval: { start: "1995-01-01", end: "1995-12-31" },
      properties: { scientific_name: "Quercus garryana", basis_of_record: "PRESERVED_SPECIMEN", occurrence_id: "opaque-occurrence" },
    }], "botanical-occurrences");
    const envelope = sample.result.lanes[0].selected as Record<string, unknown>;
    envelope.served_day = null;
    envelope.published_at = "2026-09-01T12:00:00Z";
    const { facts } = buildRegionalMeasurementFacts([sample]);
    expect(facts[0].statement).toContain("Observation interval 1995-01-01 to 1995-12-31");
    expect(facts[0].statement).toContain('scientific_name="Quercus garryana"');
    expect(facts[0].statement).toContain("Published at 2026-09-01T12:00:00Z");
    expect(facts[0].statement).not.toContain(`Observed day ${DAY}`);
    expect(facts[0].statement).not.toContain("occurrence_id");
  });

  it("keeps current application snapshot timing separate from an observation date", () => {
    const sample = read([{ properties: { category: "riparian_buffer", status: "published", title: "Community planting proposal" } }], "interventions");
    const envelope = sample.result.lanes[0].selected as Record<string, unknown>;
    envelope.snapshot_as_of = `${DAY}T12:00:00Z`;
    const { facts } = buildRegionalMeasurementFacts([sample]);
    expect(facts[0].statement).toContain("Observation date not provided");
    expect(facts[0].statement).toContain(`Snapshot as of ${DAY}T12:00:00Z`);
    expect(facts[0].statement).not.toContain(`Observed day ${DAY}`);
  });

  it("never renders catalogue, raster legend, refusal or metadata-only rows as measurement facts", () => {
    expect(buildRegionalMeasurementFacts([
      { id: "catalogue", source: "catalogue", result: { layers: [{ name: "metric", count: 40 }] } },
      { id: "raster", source: "soil-phh2o", result: { state: "numeric_values_unavailable", features: [feature()], raster_publication: { valueMin: 2, valueMax: 10 } } },
      { id: "refusal", source: "vegetation", result: { refusal_code: "not_available_in_region", features: [feature()] } },
      read([{ properties: { observation_count: 5, coverage_fraction: 1, cell_id: "identifier", observed_day: DAY } }]),
      read([{ ...feature(), properties: { ...feature().properties, allowed_client_exposure: false } }]),
    ])).toEqual({ facts: [], omittedFacts: 0 });
  });

  it.each([null, Number.NaN, Number.POSITIVE_INFINITY])("does not mint a measurement from descriptors when its value is %s", (value) => {
    const sample = read([{
      properties: { normalized_value: value, signal_name: "air_temperature", normalized_unit: "C", source_parameter: "T2M", observed_day: DAY, grid_name: "publisher-grid", release_count: 1 },
    }]);
    expect(buildRegionalMeasurementFacts([sample])).toEqual({ facts: [], omittedFacts: 0 });
  });

  it("omits complete oversized fields with disclosure instead of cutting strings or inventing units", () => {
    const { facts } = buildRegionalMeasurementFacts([read([{
      properties: { score: 12.3456789012345, category: "retained", description: "x".repeat(800), observed_day: DAY },
    }], "generic-record")]);
    expect(facts[0].statement).toContain("score=12.3456789012345");
    expect(facts[0].statement).toContain('category="retained"');
    expect(facts[0].statement).toContain("Additional record fields omitted");
    expect(facts[0].statement).not.toContain("xxx");
    expect(facts[0].statement.length).toBeLessThanOrEqual(500);
  });

  it("discloses an omitted fact when no complete record value fits", () => {
    expect(buildRegionalMeasurementFacts([read([{ properties: { category: "x".repeat(800) } }])]))
      .toEqual({ facts: [], omittedFacts: 1 });
  });

  it("bounds each read fairly across lanes and selected/history records without starving another source", () => {
    const sample = read();
    sample.result.lanes = Array.from({ length: 4 }, (_, lane) => ({
      parquet_lane: `metric-depth-${lane}`,
      selected: { state: "published", requested_day: DAY, served_day: DAY, features: Array.from({ length: 4 }, (_, row) => feature(lane + row / 10)) },
      history: [{ state: "published", requested_day: BEFORE, served_day: BEFORE, features: [feature(lane + 5, BEFORE)] }],
    }));
    const result = buildRegionalMeasurementFacts([sample, read([feature()], "other-source", "local-2")]);
    expect(result.facts.filter((fact) => fact.source === "soil-field-vpd")).toHaveLength(10);
    expect(result.facts.filter((fact) => fact.source === "other-source")).toHaveLength(1);
    expect(result.omittedFacts).toBe(10);
    for (let lane = 0; lane < 4; lane += 1) {
      expect(result.facts.some((fact) => fact.statement.includes(`metric-depth-${lane}`) && fact.statement.includes("selected record"))).toBe(true);
      expect(result.facts.some((fact) => fact.statement.includes(`metric-depth-${lane}`) && fact.statement.includes("sampled history record"))).toBe(true);
    }
  });

  it("binds deterministic fact IDs to the source, actual record, selection and calendar window", () => {
    const original = read();
    const id = buildRegionalMeasurementFacts([original]).facts[0].id;
    expect(buildRegionalMeasurementFacts([structuredClone(original)]).facts[0].id).toBe(id);
    const changedValue = read([feature(2)]);
    const changedSource = read([feature()], "different-source");
    const changedLocation = structuredClone(original);
    changedLocation.result.selection.longitude = -115.2;
    const changedDate = read([feature(1.63, BEFORE)]);
    const changedRange = structuredClone(original);
    changedRange.result.selection.range_start = "2020-01-01";
    for (const changed of [changedValue, changedSource, changedLocation, changedDate, changedRange]) {
      expect(buildRegionalMeasurementFacts([changed]).facts[0].id).not.toBe(id);
    }
  });
});
