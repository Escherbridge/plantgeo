// @vitest-environment node
// jsdom (this repo's default environment) does not resolve `import.meta.url` to a real
// `file:` URL, so the `fileURLToPath` inside `manifest-parity-support.ts` throws "The URL must be
// of scheme file"; this test needs the real filesystem to read the Python tree's `pnw.json`, same
// reason `redis-operations.test.ts` and `dashboard-access.test.ts` carry the same pragma.
import { describe, expect, it } from "vitest";
import { PNW as pnwTypeScript } from "@/lib/region/pnw";
import {
  keySetPaths,
  readPythonManifest,
  toSnakeKeyed,
} from "./manifest-parity-support";

// The helpers live beside this file so `second-region-manifest.test.ts` runs the identical diff
// against the second manifest rather than a second hand-written copy of it.
const pnwPython = readPythonManifest("pnw.json");

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
