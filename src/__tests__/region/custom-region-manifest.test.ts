// @vitest-environment node
import { readFileSync } from "node:fs";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import fixture from "../../../services/agri-data-service/src/agri_data_service/foundation/region/custom_colorado.fixture.json";
import { analysisLatticeSchema, getRegion } from "@/lib/region/region";
import { PNW } from "@/lib/region/pnw";
import { CUSTOM_REGION_CACHE_SIZE, decodeCustomRegionManifest, MAX_REGION_MANIFEST_BYTES } from "@/lib/region/custom-region-manifest";
import { MAX_ANALYSIS_LATTICE_CELLS } from "@/lib/region/region-semantics";
import { toSnakeKeyed } from "./manifest-parity-support";

function configure(document: unknown = fixture, selected = fixture.slug) {
  vi.stubEnv("NEXT_PUBLIC_PLANTGEO_REGION", selected);
  vi.stubEnv("NEXT_PUBLIC_PLANTGEO_REGION_MANIFEST_JSON", JSON.stringify(document));
}

beforeEach(() => {
  vi.stubEnv("NEXT_PUBLIC_PLANTGEO_REGION", "");
  vi.stubEnv("NEXT_PUBLIC_PLANTGEO_REGION_MANIFEST_JSON", "");
});
afterEach(() => { vi.unstubAllEnvs(); });

describe("canonical custom region deployment manifest", () => {
  it("loads the same canonical fixture as the service without a registry edit", () => {
    configure();
    const region = getRegion();
    expect(toSnakeKeyed(region)).toEqual(fixture);
    expect(region.adminCodes).toEqual(["US-CO"]);
    expect(region).toBe(getRegion());
    expect(Object.isFrozen(region.analysisLattices?.["analysis-0p25"].envelope)).toBe(true);
    expect(() => { region.adminCodes.push("US-UT"); }).toThrow();
  });

  it("preserves dictionary keys without applying field-name conversion to them", () => {
    configure({ ...fixture, sub_envelopes: { sourceScope: fixture.sub_envelopes.test_scope } });
    expect(getRegion().subEnvelopes).toHaveProperty("sourceScope");
    expect(getRegion().subEnvelopes).not.toHaveProperty("source_scope");
  });

  it("does not reuse stale content under the same slug and bounds cache retention", () => {
    configure();
    const original = getRegion();
    for (let index = 0; index < CUSTOM_REGION_CACHE_SIZE; index++) {
      configure({ ...fixture, display_name: `Revision ${index}` });
      expect(getRegion().displayName).toBe(`Revision ${index}`);
    }
    configure();
    expect(getRegion()).toEqual(original);
    expect(getRegion()).not.toBe(original);
  });

  it("retains packaged defaults for an exactly empty custom build argument", () => {
    expect(getRegion()).toEqual(PNW);
    vi.stubEnv("NEXT_PUBLIC_PLANTGEO_REGION", "kenya-highlands");
    expect(getRegion().slug).toBe("kenya-highlands");
    vi.stubEnv("NEXT_PUBLIC_PLANTGEO_REGION", "unregistered");
    expect(() => getRegion()).toThrow("unknown region");
  });

  it("requires explicit matching selection even if another manifest is cached", () => {
    configure();
    getRegion();
    vi.stubEnv("NEXT_PUBLIC_PLANTGEO_REGION", "");
    expect(() => getRegion()).toThrow("requires NEXT_PUBLIC_PLANTGEO_REGION");
    vi.stubEnv("NEXT_PUBLIC_PLANTGEO_REGION", "different-region");
    expect(() => getRegion()).toThrow("must match");
  });

  it.each(["pnw", "kenya-highlands"])("refuses shadowing packaged %s, even after caching it", (slug) => {
    vi.stubEnv("NEXT_PUBLIC_PLANTGEO_REGION", slug);
    getRegion();
    configure({ ...fixture, slug }, slug);
    expect(() => getRegion()).toThrow("must not shadow");
  });

  it.each([" ", "{", "[]", "null", "42", '{"slug":"one","slug":"two"}', '{"a":{"key":1,"k\\u0065y":2}}'])("refuses malformed or ambiguous payload %s", (payload) => {
    vi.stubEnv("NEXT_PUBLIC_PLANTGEO_REGION", fixture.slug);
    vi.stubEnv("NEXT_PUBLIC_PLANTGEO_REGION_MANIFEST_JSON", payload);
    expect(() => getRegion()).toThrow();
  });

  it("counts UTF-8 bytes and rejects an unpaired literal surrogate", () => {
    vi.stubEnv("NEXT_PUBLIC_PLANTGEO_REGION", fixture.slug);
    vi.stubEnv("NEXT_PUBLIC_PLANTGEO_REGION_MANIFEST_JSON", "é".repeat(MAX_REGION_MANIFEST_BYTES / 2 + 1));
    expect(() => getRegion()).toThrow("exceeds");
    expect(() => decodeCustomRegionManifest("\ud800")).toThrow("valid UTF-8");
  });

  it.each([
    { slug: "../outside" }, { extra: "not canonical" }, { displayName: "not canonical" },
    { timezone: "Invalid/Nowhere" }, { timezone: "../UTC" },
    { iso_country_codes: [] }, { iso_country_codes: ["US", "US"] }, { iso_country_codes: ["us"] },
    { admin_codes: ["CA-BC"] }, { admin_codes: ["US-CO", "US-CO"] },
    { platform_layers: ["weather-observations"] }, { platform_layers: [...fixture.platform_layers, "weather-observations"] },
    { enabled_layers: [...fixture.enabled_layers, ...fixture.enabled_layers] },
    { envelope: { ...fixture.envelope, east: 181 } },
    { envelope: { ...fixture.envelope, east: "-106" } },
    { default_camera_envelope: { ...fixture.envelope, west: -109 } },
    { sub_envelopes: { outside: { ...fixture.envelope, west: -109 } } },
    { analysis_lattices: { invalid_key: fixture.analysis_lattices["analysis-0p25"] } },
    { analysis_lattices: { "analysis-0p25": { ...fixture.analysis_lattices["analysis-0p25"], extra: 1 } } },
  ])("refuses invalid canonical structure or semantics %#", (changes) => {
    configure({ ...fixture, ...changes });
    expect(() => getRegion()).toThrow();
  });

  it.each(["value", "root-key", "sub-envelope-key", "lattice-key"] as const)("does not expose %s in errors or stacks", (location) => {
    const secret = "accidentally-pasted-credential";
    const updates = {
      "value": { timezone: secret },
      "root-key": { [secret]: "unexpected" },
      "sub-envelope-key": { sub_envelopes: { [secret]: { west: "invalid" } } },
      "lattice-key": { analysis_lattices: { [secret]: { pitch_degrees: "invalid" } } },
    }[location];
    configure({ ...fixture, ...updates });
    try { getRegion(); throw new Error("expected refusal"); }
    catch (error) {
      expect(String(error)).toContain("invalid custom region manifest");
      expect(String(error)).not.toContain(secret);
      expect(error instanceof Error ? error.stack : String(error)).not.toContain(secret);
    }
  });

  it.each(["4326", true, false, 4326.5])("refuses coerced CRS %s", (crs) => {
    configure({ ...fixture, crs });
    expect(() => getRegion()).toThrow("invalid custom region manifest");
  });

  it.each([4326, null])("accepts integer or null CRS %s", (crs) => {
    configure({ ...fixture, crs });
    expect(getRegion().crs).toBe(crs);
  });
});

describe("bounded exact analysis-lattice identity", () => {
  const lattice = (pitch: number, west: number, south: number, east: number, north: number) => ({
    pitchDegrees: pitch, originRule: "half_step", envelope: { west, south, east, north }, cellKeyPrefix: "test:",
  });
  it("preserves the pilot and accepts exact decimal steps and half-step origins", () => {
    expect(analysisLatticeSchema.parse(PNW.analysisLattices["analysis-0p25"])).toEqual(PNW.analysisLattices["analysis-0p25"]);
    expect(analysisLatticeSchema.safeParse(lattice(0.0002, 0, 0, 0.0004, 0.0004)).success).toBe(true);
    expect(analysisLatticeSchema.safeParse(lattice(0.0001, 0.00005, 0.00005, 0.00025, 0.00025)).success).toBe(true);
  });
  it.each([
    lattice(0.00001, 0, 0, 0.0001, 0.0001),
    lattice(0.0001, 0, 0, 0.0002, 0.0002),
    lattice(0.25, 0, 0, 0.3, 0.5),
    lattice(0.0002, 1e-100, 0, 0.0004, 0.0004),
    lattice(1e308, 0, 0, 1, 1),
  ])("rejects imprecise centroids or incomplete cells %#", (value) => {
    expect(analysisLatticeSchema.safeParse(value).success).toBe(false);
  });
  it("admits the exact cell cap and refuses the next column without enumeration", () => {
    expect(MAX_ANALYSIS_LATTICE_CELLS).toBe(100000);
    expect(analysisLatticeSchema.safeParse(lattice(0.01, 0, 0, 10, 1)).success).toBe(true);
    expect(analysisLatticeSchema.safeParse(lattice(0.01, 0, 0, 10.01, 1)).success).toBe(false);
  });
});

it("passes both public region settings into the Docker build without changing blank defaults", () => {
  const dockerfile = readFileSync("Dockerfile", "utf8").replaceAll("\r\n", "\n");
  for (const name of ["NEXT_PUBLIC_PLANTGEO_REGION", "NEXT_PUBLIC_PLANTGEO_REGION_MANIFEST_JSON"]) {
    expect(dockerfile).toContain(`ARG ${name}\n`);
    expect(dockerfile).toContain(`ENV ${name}=\${${name}}`);
  }
});
