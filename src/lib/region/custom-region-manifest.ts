import { z } from "zod";

export const MAX_REGION_MANIFEST_BYTES = 64 * 1024;
export const CUSTOM_REGION_CACHE_SIZE = 8;

const bindingSchema = z.object({
  layer_slug: z.string(), source_slug: z.string(), coverage: z.enum(["global", "regional"]),
}).strict();
const latticeSchema = z.object({
  pitch_degrees: z.number(), origin_rule: z.literal("half_step"),
  envelope: z.unknown(), cell_key_prefix: z.string(),
}).strict();
const canonicalSchema = z.object({
  slug: z.string(), display_name: z.string(), envelope: z.unknown(),
  default_camera_envelope: z.unknown(), sub_envelopes: z.record(z.string(), z.unknown()).optional(),
  crs: z.number().int().nullable(), lattice_pitch_degrees: z.number(),
  lattice_origin_rule: z.literal("floor_to_cell_origin"), timezone: z.string(),
  iso_country_codes: z.array(z.string()), admin_codes: z.array(z.string()),
  platform_layers: z.array(z.string()), enabled_layers: z.array(bindingSchema),
  analysis_lattices: z.record(z.string(), latticeSchema).optional(),
}).strict();

/** Reject duplicate keys, including escaped spellings, after JSON syntax has been validated. */
function requireUniqueObjectKeys(payload: string): void {
  const tokens = payload.match(/"(?:\\.|[^"\\])*"|[{}[\]:]/g) ?? [];
  const stack: (Set<string> | null)[] = [];
  for (let index = 0; index < tokens.length; index++) {
    const token = tokens[index];
    if (token === "{") stack.push(new Set());
    else if (token === "[") stack.push(null);
    else if (token === "}" || token === "]") stack.pop();
    else if (token.startsWith('"') && tokens[index + 1] === ":") {
      const keys = stack[stack.length - 1];
      const key = JSON.parse(token) as string;
      if (keys?.has(key)) throw new Error("region manifest JSON contains a duplicate object key");
      keys?.add(key);
    }
  }
}

/** Decode the canonical service fields without renaming dictionary keys; see AGENTS.md. */
export function decodeCustomRegionManifest(payload: string): unknown {
  for (const character of payload) {
    const code = character.codePointAt(0)!;
    if (code >= 0xd800 && code <= 0xdfff) throw new Error("region manifest must contain valid UTF-8 text");
  }
  if (new TextEncoder().encode(payload).byteLength > MAX_REGION_MANIFEST_BYTES) {
    throw new Error(`region manifest exceeds ${MAX_REGION_MANIFEST_BYTES} UTF-8 bytes`);
  }
  let raw: unknown;
  try {
    raw = JSON.parse(payload);
  } catch {
    throw new Error("region manifest must be valid JSON");
  }
  requireUniqueObjectKeys(payload);
  const result = canonicalSchema.safeParse(raw);
  if (!result.success) throw new Error("invalid custom region manifest fields");
  const value = result.data;
  return {
    slug: value.slug,
    displayName: value.display_name,
    envelope: value.envelope,
    defaultCameraEnvelope: value.default_camera_envelope,
    subEnvelopes: value.sub_envelopes ?? {},
    crs: value.crs,
    latticePitchDegrees: value.lattice_pitch_degrees,
    latticeOriginRule: value.lattice_origin_rule,
    timezone: value.timezone,
    isoCountryCodes: value.iso_country_codes,
    adminCodes: value.admin_codes,
    platformLayers: value.platform_layers,
    enabledLayers: value.enabled_layers.map((binding) => ({
      layerSlug: binding.layer_slug, sourceSlug: binding.source_slug, coverage: binding.coverage,
    })),
    analysisLattices: Object.fromEntries(Object.entries(value.analysis_lattices ?? {}).map(([key, lattice]) => [key, {
      pitchDegrees: lattice.pitch_degrees, originRule: lattice.origin_rule,
      envelope: lattice.envelope, cellKeyPrefix: lattice.cell_key_prefix,
    }])),
  };
}
