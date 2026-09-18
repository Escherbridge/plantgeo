// @vitest-environment node
// jsdom (this repo's default environment) does not resolve `import.meta.url` to a real
// `file:` URL, so `fileURLToPath` below throws "The URL must be of scheme file"; this test
// needs the real filesystem to read the Python tree's `pnw.json`, same reason
// `redis-operations.test.ts` and `dashboard-access.test.ts` carry the same pragma.
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { PNW as pnwTypeScript } from "@/lib/region/pnw";

/**
 * Parses the Python manifest's own data file so this test compares the two trees' VALUES, not a
 * second hand-copy of them. `pnw.json` is the single source of truth both
 * `foundation/region/manifest.py`'s `load_region()` and this file read; see `src/lib/region/AGENTS.md`.
 */
const PYTHON_MANIFEST_PATH = fileURLToPath(
  new URL(
    "../../../services/agri-data-service/src/agri_data_service/foundation/region/pnw.json",
    import.meta.url
  )
);

interface PythonRegionEnvelope {
  west: number;
  south: number;
  east: number;
  north: number;
}

interface PythonLayerBinding {
  layer_slug: string;
  source_slug: string;
  coverage: "global" | "regional";
}

interface PythonRegion {
  slug: string;
  display_name: string;
  envelope: PythonRegionEnvelope;
  default_camera_envelope: PythonRegionEnvelope;
  sub_envelopes: Record<string, PythonRegionEnvelope>;
  crs: number | null;
  lattice_pitch_degrees: number;
  lattice_origin_rule: string;
  timezone: string;
  iso_country_codes: string[];
  admin_codes: string[];
  platform_layers: string[];
  enabled_layers: PythonLayerBinding[];
}

const pnwPython: PythonRegion = JSON.parse(readFileSync(PYTHON_MANIFEST_PATH, "utf-8"));

/** `displayName` -> `display_name`; the one direction this repo's field names ever need to cross. */
function camelToSnake(key: string): string {
  return key.replace(/[A-Z]/g, (letter) => `_${letter.toLowerCase()}`);
}

type Walkable = null | string | number | boolean | Walkable[] | { [key: string]: Walkable };

/** Recursively re-keys every object in a TS value from camelCase to snake_case, arrays untouched. */
function toSnakeKeyed(value: unknown): Walkable {
  if (Array.isArray(value)) {
    return value.map(toSnakeKeyed) as Walkable[];
  }
  if (value !== null && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value as Record<string, unknown>).map(([key, entry]) => [camelToSnake(key), toSnakeKeyed(entry)])
    );
  }
  return value as Walkable;
}

/**
 * Every object's own key set, tagged by its path, recursively -- so a field added to one tree and
 * forgotten in the other fails here regardless of depth, instead of only at whatever fields
 * `STYLE-REVIEW-W1.md` S3's predecessor happened to enumerate by hand.
 */
function keySetPaths(value: unknown, path = "$"): string[] {
  if (Array.isArray(value)) {
    return value.flatMap((entry, index) => keySetPaths(entry, `${path}[${index}]`));
  }
  if (value !== null && typeof value === "object") {
    const keys = Object.keys(value as Record<string, unknown>).sort();
    return [
      `${path}:{${keys.join(",")}}`,
      ...keys.flatMap((key) => keySetPaths((value as Record<string, unknown>)[key], `${path}.${key}`)),
    ];
  }
  return [];
}

describe("PNW manifest parity between the service and web trees", () => {
  it("agrees on the full recursive key set, camel/snake mapped", () => {
    const tsSnakeKeyed = toSnakeKeyed(pnwTypeScript);
    expect(keySetPaths(tsSnakeKeyed).sort()).toEqual(keySetPaths(pnwPython).sort());
  });

  it("agrees on the scalar fields", () => {
    expect(pnwTypeScript.slug).toBe(pnwPython.slug);
    expect(pnwTypeScript.displayName).toBe(pnwPython.display_name);
    expect(pnwTypeScript.crs).toBe(pnwPython.crs);
    expect(pnwTypeScript.latticePitchDegrees).toBe(pnwPython.lattice_pitch_degrees);
    expect(pnwTypeScript.latticeOriginRule).toBe(pnwPython.lattice_origin_rule);
    expect(pnwTypeScript.timezone).toBe(pnwPython.timezone);
  });

  it("agrees on the envelope", () => {
    expect(pnwTypeScript.envelope).toEqual(pnwPython.envelope);
  });

  it("agrees on the default camera envelope", () => {
    expect(pnwTypeScript.defaultCameraEnvelope).toEqual(pnwPython.default_camera_envelope);
  });

  it("agrees on every sub-envelope", () => {
    expect(Object.keys(pnwTypeScript.subEnvelopes).sort()).toEqual(Object.keys(pnwPython.sub_envelopes).sort());
    for (const [purpose, envelope] of Object.entries(pnwPython.sub_envelopes)) {
      expect(pnwTypeScript.subEnvelopes[purpose as keyof typeof pnwTypeScript.subEnvelopes]).toEqual(envelope);
    }
  });

  it("agrees on the ISO country and admin codes", () => {
    expect(pnwTypeScript.isoCountryCodes).toEqual(pnwPython.iso_country_codes);
    expect(pnwTypeScript.adminCodes).toEqual(pnwPython.admin_codes);
  });

  it("agrees on the platform layer vocabulary, in order", () => {
    // Order matters here and not for `enabledLayers`: this list is the platform's enumeration, and
    // the Python side asserts the same tuple against `PLATFORM_LAYER_SLUGS`, which is sorted. A
    // slug added to one tree's vocabulary and forgotten in the other makes the two trees disagree
    // about whether a layer is a governed absence or not federated at all (STYLE-REVIEW-W5 B1).
    expect(pnwTypeScript.platformLayers).toEqual(pnwPython.platform_layers);
  });

  it("states every bound layer in the platform vocabulary", () => {
    for (const binding of pnwTypeScript.enabledLayers) {
      expect(pnwTypeScript.platformLayers, `binding ${binding.layerSlug} is outside the vocabulary`).toContain(
        binding.layerSlug
      );
    }
  });

  it("agrees on every enabled layer binding, slug for slug", () => {
    expect(pnwTypeScript.enabledLayers).toHaveLength(pnwPython.enabled_layers.length);
    const pythonBindingsBySlug = new Map(pnwPython.enabled_layers.map((binding) => [binding.layer_slug, binding]));
    for (const binding of pnwTypeScript.enabledLayers) {
      const pythonBinding = pythonBindingsBySlug.get(binding.layerSlug);
      expect(pythonBinding, `no Python binding for layer ${binding.layerSlug}`).toBeDefined();
      expect(binding.sourceSlug).toBe(pythonBinding?.source_slug);
      expect(binding.coverage).toBe(pythonBinding?.coverage);
    }
  });
});
