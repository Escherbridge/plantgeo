// @vitest-environment node
// jsdom does not resolve `import.meta.url` to a real `file:` URL, and `manifest-parity-support.ts`
// reads the Python tree's JSON off the filesystem; same pragma, same reason, as the pilot's parity
// test beside it.
/**
 * The second-region proof, web side: `kenya_highlands.ts` against the service tree's own
 * `kenya_highlands.json`, plus the cross-manifest invariant that the layer VOCABULARY is
 * platform-wide while only the BINDINGS are per region.
 *
 * `conductor/code_styleguides/federation.md` §1 (one typed manifest per deployment, parity-tested
 * against the service copy) and §4 (the boot-with-global-lanes-only checkbox). The catalogue half
 * of the proof -- the disabled toggle, the caption and the fetch that is never issued -- is
 * `second-region-catalogue.test.tsx`; the service half is
 * `services/agri-data-service/tests/foundation/test_second_region_manifest.py`.
 *
 * What this file does NOT assert: that any data, tile archive or lane run exists for this
 * footprint. None does, deliberately. A manifest declares what a deployment would serve and what
 * it refuses honestly; filling it is separate work.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { KENYA_HIGHLANDS, KENYA_HIGHLANDS_ADMIN_CODES } from "@/lib/region/kenya_highlands";
import { PNW } from "@/lib/region/pnw";
import { getRegion, regionSchema } from "@/lib/region/region";
import { keySetPaths, readPythonManifest, toSnakeKeyed } from "./manifest-parity-support";

const SECOND_REGION_SLUG = "kenya-highlands";
const kenyaPython = readPythonManifest("kenya_highlands.json");
const pnwPython = readPythonManifest("pnw.json");

/** The six global sources this manifest may bind; every other source this build knows is US-scoped. */
const EXPECTED_GLOBAL_BINDINGS: Readonly<Record<string, string>> = {
  "fire-detections": "firms",
  vegetation: "sentinel2_ndvi",
  "weather-observations": "open_meteo",
  watersheds: "hydrosheds",
  "climate-field-air-temperature": "nasa_power",
  "climate-field-dew-point": "nasa_power",
  "climate-field-precipitation": "nasa_power",
  "climate-field-relative-humidity": "nasa_power",
  "climate-field-shortwave-radiation": "nasa_power",
  "climate-field-soil-wetness-profile": "nasa_power",
  "climate-field-soil-wetness-root-zone": "nasa_power",
  "climate-field-soil-wetness-surface": "nasa_power",
  "climate-field-wind-speed": "nasa_power",
  "soil-field-moisture": "era5_land",
  "soil-field-temperature": "era5_land",
  "soil-field-vpd": "era5_land",
  "botanical-occurrences": "gbif",
};

afterEach(() => {
  vi.unstubAllEnvs();
});

describe("Kenya Highlands manifest parity between the service and web trees", () => {
  it("agrees on the full recursive key set, camel/snake mapped", () => {
    expect(keySetPaths(toSnakeKeyed(KENYA_HIGHLANDS)).sort()).toEqual(keySetPaths(kenyaPython).sort());
  });

  it("agrees on the scalar fields, including a null crs", () => {
    expect(KENYA_HIGHLANDS.slug).toBe(kenyaPython.slug);
    expect(KENYA_HIGHLANDS.displayName).toBe(kenyaPython.display_name);
    expect(KENYA_HIGHLANDS.crs).toBe(kenyaPython.crs);
    expect(KENYA_HIGHLANDS.crs).toBeNull();
    expect(KENYA_HIGHLANDS.latticePitchDegrees).toBe(kenyaPython.lattice_pitch_degrees);
    expect(KENYA_HIGHLANDS.latticeOriginRule).toBe(kenyaPython.lattice_origin_rule);
    expect(KENYA_HIGHLANDS.timezone).toBe(kenyaPython.timezone);
  });

  it("agrees on both envelopes and on carrying no sub-envelope at all", () => {
    expect(KENYA_HIGHLANDS.envelope).toEqual(kenyaPython.envelope);
    expect(KENYA_HIGHLANDS.defaultCameraEnvelope).toEqual(kenyaPython.default_camera_envelope);
    expect(KENYA_HIGHLANDS.subEnvelopes).toEqual({});
    expect(kenyaPython.sub_envelopes).toEqual({});
  });

  it("agrees on the ISO country and admin codes, which are declared as one tuple", () => {
    expect(KENYA_HIGHLANDS.isoCountryCodes).toEqual(kenyaPython.iso_country_codes);
    expect(KENYA_HIGHLANDS.adminCodes).toEqual(kenyaPython.admin_codes);
    expect(KENYA_HIGHLANDS.adminCodes).toEqual([...KENYA_HIGHLANDS_ADMIN_CODES]);
  });

  it("agrees on every enabled layer binding, slug for slug", () => {
    expect(KENYA_HIGHLANDS.enabledLayers).toHaveLength(kenyaPython.enabled_layers.length);
    const pythonBindingsBySlug = new Map(kenyaPython.enabled_layers.map((binding) => [binding.layer_slug, binding]));
    for (const binding of KENYA_HIGHLANDS.enabledLayers) {
      const pythonBinding = pythonBindingsBySlug.get(binding.layerSlug);
      expect(pythonBinding, `no Python binding for layer ${binding.layerSlug}`).toBeDefined();
      expect(binding.sourceSlug).toBe(pythonBinding?.source_slug);
      expect(binding.coverage).toBe(pythonBinding?.coverage);
    }
  });

  it("binds only global sources, because every other source this build knows is US-scoped", () => {
    const bindings = Object.fromEntries(
      KENYA_HIGHLANDS.enabledLayers.map((binding) => [binding.layerSlug, binding.sourceSlug])
    );
    expect(bindings).toEqual(EXPECTED_GLOBAL_BINDINGS);
    expect(KENYA_HIGHLANDS.enabledLayers.every((binding) => binding.coverage === "global")).toBe(true);
  });

  it("parses against the schema every manifest is held to", () => {
    expect(() => regionSchema.parse(KENYA_HIGHLANDS)).not.toThrow();
  });
});

describe("the vocabulary is platform-wide, the bindings are per region", () => {
  it("states the same platformLayers in both manifests, in both trees", () => {
    // Four copies of one enumeration, pinned to each other here and, from the other end, to
    // `PLATFORM_LAYER_SLUGS` in `test_second_region_manifest.py`. If these could disagree,
    // "unbound" and "not a federated layer at all" would mean different things per deployment.
    expect(KENYA_HIGHLANDS.platformLayers).toEqual(PNW.platformLayers);
    expect(KENYA_HIGHLANDS.platformLayers).toEqual(kenyaPython.platform_layers);
    expect(kenyaPython.platform_layers).toEqual(pnwPython.platform_layers);
  });

  it("binds a strictly smaller set of layers than the pilot, over different ground", () => {
    const pilotLayers = new Set(PNW.enabledLayers.map((binding) => binding.layerSlug));
    const secondLayers = KENYA_HIGHLANDS.enabledLayers.map((binding) => binding.layerSlug);
    expect(secondLayers.every((layerSlug) => pilotLayers.has(layerSlug))).toBe(true);
    expect(secondLayers.length).toBeLessThan(pilotLayers.size);
    expect(KENYA_HIGHLANDS.isoCountryCodes).not.toEqual(PNW.isoCountryCodes);
  });

  it("states every bound layer in its own platform vocabulary", () => {
    for (const binding of KENYA_HIGHLANDS.enabledLayers) {
      expect(KENYA_HIGHLANDS.platformLayers, `binding ${binding.layerSlug} is outside the vocabulary`).toContain(
        binding.layerSlug
      );
    }
  });
});

describe("getRegion resolves the manifest the deployment selects", () => {
  it("returns the second manifest when NEXT_PUBLIC_PLANTGEO_REGION names it", () => {
    vi.stubEnv("NEXT_PUBLIC_PLANTGEO_REGION", SECOND_REGION_SLUG);
    const region = getRegion();
    expect(region.slug).toBe(SECOND_REGION_SLUG);
    expect(region.adminCodes).toEqual([...KENYA_HIGHLANDS_ADMIN_CODES]);
    // Parsed and frozen like the pilot: the selection changes which manifest, never how it is read.
    expect(Object.isFrozen(region.envelope)).toBe(true);
  });

  it("keeps the pilot as the default when nothing selects a region", () => {
    vi.stubEnv("NEXT_PUBLIC_PLANTGEO_REGION", "");
    expect(getRegion().slug).toBe("pnw");
  });

  it("memoises per slug, so two regions never share one parse", () => {
    vi.stubEnv("NEXT_PUBLIC_PLANTGEO_REGION", SECOND_REGION_SLUG);
    const second = getRegion();
    vi.stubEnv("NEXT_PUBLIC_PLANTGEO_REGION", "pnw");
    const pilot = getRegion();
    vi.stubEnv("NEXT_PUBLIC_PLANTGEO_REGION", SECOND_REGION_SLUG);
    expect(getRegion()).toBe(second);
    expect(pilot.slug).toBe("pnw");
    expect(second.slug).toBe(SECOND_REGION_SLUG);
  });

  it("refuses an unregistered slug rather than serving the pilot under its name", () => {
    vi.stubEnv("NEXT_PUBLIC_PLANTGEO_REGION", "nonexistent-region");
    expect(() => getRegion()).toThrow(/unknown region/);
  });
});
