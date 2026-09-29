// @vitest-environment node
// jsdom (this repo's default environment) does not resolve `import.meta.url` to a real `file:`
// URL, so the `fileURLToPath` below throws "The URL must be of scheme file"; this file needs the
// real filesystem to read the golden fixture, same reason `manifest-parity.test.ts` and
// `footprint-literals.test.ts` carry the same pragma.
import { existsSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import {
  buildSiteBrief,
  canonicalJson,
  describeSiteBrief,
  literatureSeed,
  roundHalfUp,
  textureClassFromTenths,
  usdaTextureClass,
  withSiteBrief,
  type SiteBriefInputs,
} from "@/lib/server/services/site-brief";

/** CONTRACT C5.1 worked example. */
function workedInputs(): SiteBriefInputs {
  return {
    built_on: "2026-09-27",
    point: { longitude_e5: -11620000, latitude_e5: 4360000 },
    soil: {
      state: "available", release_id: "soilgrids-v2.0/2020-06-02", distance_m: 140,
      mapped: {
        "0-5cm": { phh2o: 57, soc: 243, nitrogen: 190, bdod: 121, cec: 182, ocd: 380, clay: 189, sand: 371, silt: 440, cfvo: 98 },
        "5-15cm": { phh2o: 57, soc: 210, nitrogen: 160, bdod: 127, cec: 170, ocd: 340, clay: 195, sand: 380, silt: 425, cfvo: 102 },
        "15-30cm": { phh2o: 58, soc: 180, nitrogen: 130, bdod: 131, cec: 160, ocd: 300, clay: 201, sand: 390, silt: 409, cfvo: 106 },
      },
    },
    fire: {
      state: "available", latest_fire_day: "2025-08-11", burn_severity: "high",
      detections_last_30_days: 0, source: "fire-perimeters + burn-severity (MTBS)",
    },
    drought: { state: "available", usdm_class: "D1", week_of: "2026-09-22" },
    weather: {
      state: "available", observed_at: "2026-09-27T15:00:00Z", distance_m: 12400,
      temperature_tenths_c: 142, relative_humidity_pct: 38,
    },
    land_cover: {
      state: "available", class_name: "Grassland/Pasture", class_code: 176,
      fraction_permille: 612, edition_year: 2025, release_day: "2026-01-30", cell_m: 3000,
    },
  };
}

/** CONTRACT C5.3 output for the worked example. */
const WORKED_EXPECTED = {
  brief_version: "site-brief/1",
  built_on: "2026-09-27",
  point: { longitude: -116.2, latitude: 43.6 },
  soil: {
    state: "available", basis: "model_estimate", source: "SoilGrids v2.0",
    release_id: "soilgrids-v2.0/2020-06-02", resolution_m: 250, cell_deg: 0.005, distance_m: 140,
    label: "SoilGrids v2.0 250 m model estimate, sampled at the centre of a ~500 m cell 140 m from this point (release soilgrids-v2.0/2020-06-02)",
    depths: {
      "0-5cm": { ph: 5.7, soc_g_kg: 24.3, nitrogen_g_kg: 1.9, bdod_g_cm3: 1.21, cec_cmolc_kg: 18.2, ocd_kg_m3: 38, clay_pct: 18.9, sand_pct: 37.1, silt_pct: 44, cfvo_vol_pct: 9.8 },
      "5-15cm": { ph: 5.7, soc_g_kg: 21, nitrogen_g_kg: 1.6, bdod_g_cm3: 1.27, cec_cmolc_kg: 17, ocd_kg_m3: 34, clay_pct: 19.5, sand_pct: 38, silt_pct: 42.5, cfvo_vol_pct: 10.2 },
      "15-30cm": { ph: 5.8, soc_g_kg: 18, nitrogen_g_kg: 1.3, bdod_g_cm3: 1.31, cec_cmolc_kg: 16, ocd_kg_m3: 30, clay_pct: 20.1, sand_pct: 39, silt_pct: 40.9, cfvo_vol_pct: 10.6 },
    },
    topsoil_0_30cm: {
      ph: 5.8, soc_pct: 2, clay_pct: 19.7, sand_pct: 38.4, silt_pct: 42, cfvo_vol_pct: 10.3, bdod_g_cm3: 1.28,
      texture_class: "loam", reaction_class: "moderately acid", soc_band: "high organic carbon",
      label: "SoilGrids v2.0 250 m model estimate, 0-30 cm (thickness-weighted)",
    },
  },
  fire: {
    state: "available", basis: "measured", days_since_fire: 412, latest_fire_day: "2025-08-11",
    burn_severity: "high", detections_last_30_days: 0, source: "fire-perimeters + burn-severity (MTBS)",
    label: "Mapped fire perimeter and MTBS burn severity, burned 2025-08-11",
  },
  drought: {
    state: "available", basis: "classified", usdm_class: "D1", week_of: "2026-09-22",
    label: "US Drought Monitor class, week of 2026-09-22",
  },
  weather: {
    state: "available", basis: "measured", observed_at: "2026-09-27T15:00:00Z", distance_m: 12400,
    temperature_c: 14.2, relative_humidity_pct: 38,
    label: "Nearest weather-station observation, 2026-09-27T15:00:00Z, 12.4 km away",
  },
  land_cover: {
    state: "available", basis: "classified_remote_sensing", source: "USDA CDL",
    class_name: "Grassland/Pasture", class_code: 176, fraction_pct: 61, edition_year: 2025,
    release_day: "2026-01-30", cell_km: 3,
    label: "USDA CDL 2025 (released 2026-01-30), dominant class of a 3 km cell (61%)",
  },
  descriptors: [
    { text: "moderately acid loam topsoil (pH 5.8, SoilGrids model estimate 0-30 cm)", seed: "moderately acid loam topsoil" },
    { text: "high organic carbon topsoil (2.0% SOC, SoilGrids model estimate 0-30 cm)", seed: "high organic carbon" },
    { text: "burned 2025-08-11, high severity (mapped perimeter and MTBS, measured)", seed: "burned 2025, high severity" },
    { text: "moderate drought D1 (US Drought Monitor, week of 2026-09-22)", seed: "moderate drought" },
    { text: "grassland/pasture, 61% of a 3 km cell (USDA CDL 2025 classified imagery)", seed: "grassland/pasture" },
  ],
  literature_seed: "moderately acid loam topsoil; high organic carbon; burned 2025, high severity; moderate drought; grassland/pasture",
};

const GOLDEN_PATH = fileURLToPath(new URL("../../../services/agri-data-service/tests/fixtures/site_brief_golden.json", import.meta.url));

describe("site brief builder (CONTRACT C5)", () => {
  it("reproduces the pinned worked example exactly, after canonicalisation", () => {
    expect(canonicalJson(buildSiteBrief(workedInputs()))).toBe(canonicalJson(WORKED_EXPECTED));
  });

  it("rounds half up by integer division, including exact ties", () => {
    expect(roundHalfUp(1725, 30)).toBe(58); // 57.5 -> 58, the worked-example pH tie
    expect(roundHalfUp(5, 10)).toBe(1);
    expect(roundHalfUp(15, 10)).toBe(2);
    expect(roundHalfUp(25, 10)).toBe(3);
    expect(roundHalfUp(14, 10)).toBe(1);
    expect(roundHalfUp(0, 30)).toBe(0);
    expect(() => roundHalfUp(-1, 10)).toThrow(RangeError);
    expect(() => roundHalfUp(1.5, 10)).toThrow(RangeError);
  });

  it("classifies every point of a 1% texture grid, so `unclassified` is unreachable", () => {
    for (let sand = 0; sand <= 1000; sand += 10) {
      for (let clay = 0; clay <= 1000 - sand; clay += 10) {
        expect(usdaTextureClass(sand, 1000 - sand - clay, clay)).not.toBe("unclassified");
      }
    }
    expect(textureClassFromTenths(0, 0, 0)).toBe("unclassified");
  });

  it("puts the clay loam / loam boundary at normalised clay 270", () => {
    expect(usdaTextureClass(430, 300, 270)).toBe("clay loam");
    expect(usdaTextureClass(431, 300, 269)).toBe("loam");
  });

  it("states every unavailable section with its reason and contributes no descriptor for it", () => {
    const inputs = workedInputs();
    inputs.soil = { state: "unavailable", reason: "no_cell_within_radius", radius_m: 1000 };
    inputs.fire = { state: "unavailable", reason: "serving_at_capacity" };
    inputs.drought = { state: "unavailable", reason: "not_published" };
    inputs.weather = { state: "unavailable", reason: "timeout" };
    inputs.land_cover = { state: "unavailable", reason: "not_bound_in_region" };
    const brief = buildSiteBrief(inputs);
    expect(brief.soil).toEqual({ state: "unavailable", reason: "no_cell_within_radius", radius_m: 1000 });
    expect(brief.fire).toEqual({ state: "unavailable", reason: "serving_at_capacity" });
    expect(brief.descriptors).toEqual([]);
    expect(brief.literature_seed).toBe("");
  });

  it("reports reads_disabled soil as unavailable, never as an absence of soil", () => {
    const inputs = workedInputs();
    inputs.soil = { state: "unavailable", reason: "reads_disabled" };
    const brief = buildSiteBrief(inputs);
    expect(brief.soil).toEqual({ state: "unavailable", reason: "reads_disabled" });
    expect(brief.descriptors.map((descriptor) => descriptor.seed)).toEqual([
      "burned 2025, high severity", "moderate drought", "grassland/pasture",
    ]);
    expect(describeSiteBrief(brief)).toContain("soil: not available (reads_disabled)");
  });

  it("describes a detections-only fire and says nothing for a fire-free, detection-free point", () => {
    const detectionsOnly = workedInputs();
    detectionsOnly.fire = { state: "available", latest_fire_day: null, burn_severity: null, detections_last_30_days: 3, source: "fire-perimeters + burn-severity (MTBS)" };
    const withDetections = buildSiteBrief(detectionsOnly);
    expect(withDetections.descriptors[2]).toEqual({
      text: "3 satellite fire detections nearby in the last 30 days (measured)", seed: "recent fire activity",
    });
    expect(withDetections.fire).toMatchObject({ days_since_fire: null, latest_fire_day: null });

    const quiet = workedInputs();
    quiet.fire = { state: "available", detections_last_30_days: 0, source: "fire-perimeters + burn-severity (MTBS)" };
    expect(buildSiteBrief(quiet).descriptors.some((descriptor) => descriptor.seed.includes("fire"))).toBe(false);
  });

  it("names 'no drought' and an alkaline silt loam in their own words", () => {
    const inputs = workedInputs();
    inputs.drought = { state: "available", usdm_class: "none", week_of: "2026-09-22" };
    const alkalineSilt = { phh2o: 80, soc: 60, nitrogen: 60, bdod: 140, cec: 150, ocd: 200, clay: 150, sand: 150, silt: 700, cfvo: 20 };
    inputs.soil = { state: "available", release_id: "soilgrids-v2.0/2020-06-02", distance_m: 0,
      mapped: { "0-5cm": alkalineSilt, "5-15cm": alkalineSilt, "15-30cm": alkalineSilt } };
    const brief = buildSiteBrief(inputs);
    expect(brief.descriptors[0].seed).toBe("moderately alkaline silt loam topsoil");
    expect(brief.descriptors[1].seed).toBe("low organic carbon");
    expect(brief.descriptors[3]).toEqual({ text: "no drought (US Drought Monitor, week of 2026-09-22)", seed: "no drought" });
  });

  it("cuts the literature seed at a word boundary within 600 characters", () => {
    const long = Array.from({ length: 80 }, (_, index) => ({ text: "", seed: `seed number ${index}` }));
    const seed = literatureSeed(long);
    expect(seed.length).toBeLessThanOrEqual(600);
    expect(seed.endsWith(" ")).toBe(false);
    expect(`${seed} `.length).toBeGreaterThan(560);
  });

  it("labels every SoilGrids number as a model estimate and never as a measurement", () => {
    const brief = buildSiteBrief(workedInputs());
    expect(brief.soil).toMatchObject({ basis: "model_estimate" });
    const soilText = JSON.stringify(brief.soil) + brief.descriptors.slice(0, 2).map((descriptor) => descriptor.text).join(" ");
    expect(soilText).toContain("model estimate");
    expect(soilText).not.toMatch(/measured|observed/i);
  });
});

describe("canonical JSON (CONTRACT C5.5)", () => {
  it("sorts keys, is compact and renders integral floats as integers", () => {
    expect(canonicalJson({ b: 2.0, a: [1.5, "x\"y"], c: { e: null, d: true } })).toBe('{"a":[1.5,"x\\"y"],"b":2,"c":{"d":true,"e":null}}');
    expect(canonicalJson(-0)).toBe("0");
    expect(canonicalJson({ a: undefined, b: 1 })).toBe('{"b":1}');
    expect(() => canonicalJson(Number.NaN)).toThrow(RangeError);
  });
});

/**
 * Parity with agri `agent/site_brief.py`: both builders must match the golden fixture WS-D owns.
 * A missing fixture fails loudly rather than skipping, so parity can never pass unexamined.
 */
describe("golden parity with the Python builder", () => {
  it("matches every case of services/agri-data-service/tests/fixtures/site_brief_golden.json", () => {
    expect(existsSync(GOLDEN_PATH), `golden fixture missing at ${GOLDEN_PATH} (WS-D owns it)`).toBe(true);
    const golden = JSON.parse(readFileSync(GOLDEN_PATH, "utf8")) as {
      fixture_version: string;
      cases: { name: string; inputs: SiteBriefInputs; expected: unknown }[];
    };
    expect(golden.fixture_version).toBe("site-brief-golden/1");
    expect(golden.cases.length).toBeGreaterThanOrEqual(14);
    for (const testCase of golden.cases) {
      expect(canonicalJson(buildSiteBrief(testCase.inputs)), testCase.name).toBe(canonicalJson(testCase.expected));
    }
  });
});

describe("withSiteBrief (CONTRACT C3)", () => {
  const baseContext = {
    user_question: "what can I plant here?",
    point: { longitude: -116.2, latitude: 43.6 },
    site_facts: { soil_ph: 6.6, electrical_conductivity_ds_m: 0.4, burn_severity: "low" as const, days_since_fire: 900 },
  };

  /** The request's read ledger behind `baseContext.site_facts` (regional-analysis-workflow `SiteFactObservation`). */
  const observations = [
    { fact: "soil_ph", value: 6.6, readId: "local-1" },
    { fact: "electrical_conductivity_ds_m", value: 0.4, readId: "local-2" },
    { fact: "fire_day", value: "2024-02-18", readId: "local-3" },
    { fact: "burn_severity_by_day", value: "2024-02-18|low", readId: "local-3" },
  ];
  const sourceByReadId = new Map([["local-1", "soil-survey"], ["local-2", "soil-survey"], ["local-3", "burn-severity"]]);

  it("overrides soil keys with the topsoil estimate and labels every known key", () => {
    const context = withSiteBrief(baseContext, buildSiteBrief(workedInputs()), { observations, sourceByReadId });
    expect(context.user_question).toBe("what can I plant here?");
    expect(context.site_facts).toEqual({
      soil_ph: 5.8, soil_organic_carbon_pct: 2, sand_pct: 38.4, clay_pct: 19.7,
      electrical_conductivity_ds_m: 0.4, burn_severity: "high", days_since_fire: 412, land_cover: "Grassland/Pasture",
    });
    expect(context.site_facts_provenance?.soil_ph).toEqual({
      basis: "model_estimate", source: "SoilGrids v2.0", release_id: "soilgrids-v2.0/2020-06-02",
      depth: "0-30 cm (thickness-weighted)", resolution_m: 250, distance_m: 140,
      label: "SoilGrids v2.0 250 m model estimate, 0-30 cm (thickness-weighted), cell centre 140 m away",
    });
    expect(context.site_facts_provenance?.days_since_fire).toMatchObject({ basis: "measured" });
    expect(context.site_facts_provenance?.land_cover).toMatchObject({ basis: "classified_remote_sensing", source: "USDA CDL" });
    // Review M1: a residual measured key keeps its provenance from the read that produced it.
    expect(context.site_facts_provenance?.electrical_conductivity_ds_m).toEqual({
      basis: "measured", source: "soil-survey",
      label: "Measured value, read by the server this request from soil-survey (read local-2)",
    });
    expect(context.site_brief_query).toBe("moderately acid loam topsoil; high organic carbon; burned 2025, high severity; moderate drought; grassland/pasture");
  });

  it.each([1001, 1340])("never presents a %i m nearest-cell estimate as this site's soil (owner 2026-09-28)", (distanceM) => {
    const inputs = workedInputs();
    inputs.soil = { ...(inputs.soil as Extract<SiteBriefInputs["soil"], { state: "available" }>), distance_m: distanceM };
    const brief = buildSiteBrief(inputs);
    if (brief.soil.state !== "available") throw new Error("soil should be available");
    const context = withSiteBrief(baseContext, brief);
    const soilTexts = [
      brief.soil.label, brief.soil.topsoil_0_30cm.label, ...brief.descriptors.slice(0, 2).map((descriptor) => descriptor.text),
      ...(["soil_ph", "soil_organic_carbon_pct", "sand_pct", "clay_pct"] as const).map((key) => context.site_facts_provenance?.[key]?.label),
    ];
    for (const text of soilTexts) expect(text).toContain("none within 1,000 m");
    expect(brief.descriptors.slice(0, 2).map((descriptor) => descriptor.seed)).toEqual(["", ""]);
    expect(brief.literature_seed).toBe("burned 2025, high severity; moderate drought; grassland/pasture");
    expect(context.site_brief_query).toBe(brief.literature_seed);
    expect(describeSiteBrief(brief)).toContain("- soil (nearest SoilGrids cell, NOT this point): SoilGrids v2.0 250 m model estimate, nearest cell centre");
  });

  it("never lets a severity from another fire ride with the brief's fire", () => {
    const inputs = workedInputs();
    inputs.fire = { state: "available", latest_fire_day: "2025-08-11", burn_severity: null, detections_last_30_days: 0, source: "fire-perimeters + burn-severity (MTBS)" };
    const context = withSiteBrief(baseContext, buildSiteBrief(inputs));
    expect(context.site_facts).not.toHaveProperty("burn_severity");
    expect(context.site_facts?.days_since_fire).toBe(412);
  });

  it("omits site_brief_query when the seed is empty and passes a missing brief through untouched", () => {
    const inputs = workedInputs();
    inputs.soil = { state: "unavailable", reason: "reads_disabled" };
    inputs.fire = { state: "unavailable", reason: "read_failed" };
    inputs.drought = { state: "unavailable", reason: "read_failed" };
    inputs.land_cover = { state: "unavailable", reason: "read_failed" };
    const context = withSiteBrief({ point: baseContext.point }, buildSiteBrief(inputs));
    expect(context).toEqual({ point: baseContext.point });
    expect(withSiteBrief(baseContext, null)).toBe(baseContext);
  });

  it("keeps soil values no read produced out of the provenance map, so agri drops them", () => {
    const inputs = workedInputs();
    inputs.soil = { state: "unavailable", reason: "reads_disabled" };
    const context = withSiteBrief(baseContext, buildSiteBrief(inputs));
    expect(context.site_facts?.soil_ph).toBe(6.6);
    expect(context.site_facts_provenance).not.toHaveProperty("soil_ph");
  });

  /** Review M1: with the brief's soil section off, wave 2's measured facts must still reach agri. */
  it("gives every remaining measured key provenance when the soil section is off", () => {
    const inputs = workedInputs();
    inputs.soil = { state: "unavailable", reason: "reads_disabled" };
    inputs.fire = { state: "unavailable", reason: "read_failed" };
    const precipitation = { fact: "annual_precip_mm", value: 310, readId: "local-4" };
    const context = withSiteBrief(
      { ...baseContext, site_facts: { ...baseContext.site_facts, annual_precip_mm: 310 } },
      buildSiteBrief(inputs),
      { observations: [...observations, precipitation], sourceByReadId: new Map([...sourceByReadId, ["local-4", "climate-field"]]) },
    );
    for (const key of ["soil_ph", "electrical_conductivity_ds_m", "annual_precip_mm", "days_since_fire", "burn_severity"] as const) {
      expect(context.site_facts_provenance?.[key], key).toMatchObject({ basis: "measured" });
    }
    expect(context.site_facts_provenance?.soil_ph?.label).toContain("soil-survey (read local-1)");
    expect(context.site_facts_provenance?.annual_precip_mm?.source).toBe("climate-field");
    expect(context.site_facts_provenance?.days_since_fire).toMatchObject({ basis: "measured", source: "burn-severity" });
    // Agri keeps a key only when it has provenance: every key the server read has one.
    expect(Object.keys(context.site_facts ?? {}).sort()).toEqual(Object.keys(context.site_facts_provenance ?? {}).sort());
  });

  it("labels a payload SoilGrids value a 0-5 cm model estimate, never measured, even with no brief", () => {
    const soilProperties = {
      ph: 5.7, organicCarbon: 24.3, nitrogen: 1.9, bulkDensity: 1.21, cec: 18.2, ocd: 38, basis: "model_estimate" as const,
      label: "SoilGrids v2.0 250 m model estimate, 0-5 cm, cell centre 140 m away (release soilgrids-v2.0/2020-06-02)",
      releaseId: "soilgrids-v2.0/2020-06-02", distanceM: 140,
    };
    const context = withSiteBrief(
      { point: baseContext.point, site_facts: { soil_ph: 5.7, soil_organic_carbon_pct: 2.43 } },
      null,
      // A warehouse read that happens to agree never makes the SoilGrids value "measured".
      { observations: [{ fact: "soil_ph", value: 5.7, readId: "local-9" }], soilProperties },
    );
    expect(context.site_facts_provenance?.soil_ph).toMatchObject({
      basis: "model_estimate", source: "SoilGrids v2.0", depth: "0-5 cm", distance_m: 140, release_id: "soilgrids-v2.0/2020-06-02",
    });
    expect(context.site_facts_provenance?.soil_ph?.label).toContain("model estimate");
    expect(context.site_facts_provenance?.soil_organic_carbon_pct).toMatchObject({ basis: "model_estimate" });
    expect(context).not.toHaveProperty("site_brief_query");
  });

  it("passes the wave-2 context through untouched with neither a brief nor a soil read (review M7)", () => {
    expect(withSiteBrief(baseContext, null, { observations, sourceByReadId, soilProperties: null })).toBe(baseContext);
  });
});
