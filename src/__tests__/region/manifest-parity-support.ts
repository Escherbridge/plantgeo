// Shared by `manifest-parity.test.ts` (the pilot) and `second-region-manifest.test.ts` (the second
// region): both diff a TypeScript manifest against the Python tree's own JSON data file, and the
// diff is the same diff. Not a `*.test.ts` file, so vitest's `include` never collects it.
//
// Requires the `node` environment in the file that imports it: jsdom does not resolve
// `import.meta.url` to a real `file:` URL, so `fileURLToPath` below throws there.
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

export interface PythonRegionEnvelope {
  west: number;
  south: number;
  east: number;
  north: number;
}

export interface PythonLayerBinding {
  layer_slug: string;
  source_slug: string;
  coverage: "global" | "regional";
}

export interface PythonRegion {
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

/**
 * Parses one manifest's own data file out of the Python package tree, so a parity test compares the
 * two trees' VALUES rather than a second hand-copy of them. These JSON files are the single source
 * of truth `foundation/region/manifest.py`'s `load_region()` reads; see `src/lib/region/AGENTS.md`.
 */
export function readPythonManifest(fileName: string): PythonRegion {
  const manifestPath = fileURLToPath(
    new URL(
      `../../../services/agri-data-service/src/agri_data_service/foundation/region/${fileName}`,
      import.meta.url
    )
  );
  return JSON.parse(readFileSync(manifestPath, "utf-8")) as PythonRegion;
}

/** `displayName` -> `display_name`; the one direction this repo's field names ever need to cross. */
function camelToSnake(key: string): string {
  return key.replace(/[A-Z]/g, (letter) => `_${letter.toLowerCase()}`);
}

export type Walkable = null | string | number | boolean | Walkable[] | { [key: string]: Walkable };

/** Recursively re-keys every object in a TS value from camelCase to snake_case, arrays untouched. */
export function toSnakeKeyed(value: unknown): Walkable {
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
 * forgotten in the other fails regardless of depth, instead of only at whatever fields a test
 * happened to enumerate by hand.
 */
export function keySetPaths(value: unknown, path = "$"): string[] {
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
