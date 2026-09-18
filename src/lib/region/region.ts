import { z } from "zod";
import { PNW, PNW_ADMIN_CODES } from "@/lib/region/pnw";

/** A WGS84 west/south/east/north bounding box; the manifest's own footprint claim. */
export const regionEnvelopeSchema = z
  .object({
    west: z.number(),
    south: z.number(),
    east: z.number(),
    north: z.number(),
  })
  .strict()
  .refine((envelope) => envelope.west < envelope.east, {
    message: "envelope west must be < east",
    path: ["west"],
  })
  .refine((envelope) => envelope.south < envelope.north, {
    message: "envelope south must be < north",
    path: ["south"],
  });

export type RegionEnvelope = z.infer<typeof regionEnvelopeSchema>;

/** How `floor_to_resolution` (`warehouse/parquet/tiers.py`) maps a coordinate onto its cell. */
export const latticeOriginRuleSchema = z.literal("floor_to_cell_origin");
export type LatticeOriginRule = z.infer<typeof latticeOriginRuleSchema>;

export const sourceCoverageSchema = z.enum(["global", "regional"]);
export type SourceCoverage = z.infer<typeof sourceCoverageSchema>;

/** One `geo.layers` slug bound to the source that fills it in this region, per `federation.md` §2. */
export const layerBindingSchema = z
  .object({
    layerSlug: z.string(),
    sourceSlug: z.string(),
    coverage: sourceCoverageSchema,
  })
  .strict();

export type LayerBinding = z.infer<typeof layerBindingSchema>;

/**
 * A deployment's one typed footprint: envelope, lattice, timezone, admin scope and layer bindings.
 *
 * Mirrors `services/agri-data-service/src/agri_data_service/foundation/region/manifest.py`'s
 * `Region` model field for field; see `src/lib/region/AGENTS.md` and
 * `src/__tests__/region/manifest-parity.test.ts` for how the two trees stay in sync.
 */
export const regionSchema = z
  .object({
    slug: z.string(),
    displayName: z.string(),
    envelope: regionEnvelopeSchema,
    /** The narrower opening-camera/burn-envelope box; see `FALLBACK_COVERAGE_BBOX`'s caller. */
    defaultCameraEnvelope: regionEnvelopeSchema,
    subEnvelopes: z.record(z.string(), regionEnvelopeSchema),
    crs: z.number().int().nullable(),
    latticePitchDegrees: z.number().positive(),
    latticeOriginRule: latticeOriginRuleSchema,
    timezone: z.string(),
    isoCountryCodes: z.array(z.string()),
    adminCodes: z.array(z.string()),
    enabledLayers: z.array(layerBindingSchema),
  })
  .strict();

export type Region = z.infer<typeof regionSchema>;

/**
 * The literal admin-code union this deployment's manifest declares, INDEXED off the manifest rather
 * than restated: `pnw.ts` keeps `PNW_ADMIN_CODES` as a `const` tuple and spreads it into
 * `adminCodes`, so the literals survive and a code added or renamed there changes this union with
 * no second edit (STYLE-REVIEW-W2 S1). `regionSchema` still validates only `z.array(z.string())`,
 * because a future region may bind codes this one never does.
 */
export type RegionAdminCode = (typeof PNW.adminCodes)[number];

/** "US-WA" -> "WA": one admin code's subdivision suffix, at type level. */
type SubdivisionCodeOf<Code extends string> = Code extends `${string}-${infer Subdivision}`
  ? Subdivision
  : never;

/** The same map across a whole tuple, so the TUPLE shape (not just the union) survives. */
type SubdivisionCodesOf<Codes extends readonly string[]> = {
  -readonly [Index in keyof Codes]: SubdivisionCodeOf<Codes[Index]>;
};

/** The two-letter subdivision code every admin code in this region carries. */
export type RegionSubdivisionCode = SubdivisionCodeOf<RegionAdminCode>;

/**
 * Recursively `Object.freeze`s a parsed manifest so no caller can mutate a value reachable from
 * `getRegion()` -- the trap `src/lib/map/coverage-region.ts` hit by aliasing
 * `getRegion().defaultCameraEnvelope` into a mutable exported constant. Only walks plain objects and
 * arrays; `Region`'s leaves are strings, numbers and arrays of those, so that is everything reachable.
 */
function deepFreeze<T>(value: T): T {
  if (value !== null && typeof value === "object" && !Object.isFrozen(value)) {
    Object.freeze(value);
    for (const key of Object.keys(value as Record<string, unknown>)) {
      deepFreeze((value as Record<string, unknown>)[key]);
    }
  }
  return value;
}

let cachedRegion: Region | undefined;

/**
 * Returns the deployment's one region manifest.
 *
 * Parses `PNW` through `regionSchema` and deep-freezes the result on first call, then returns the
 * memoised value -- so the `.refine()` envelope-ordering checks actually run once in production
 * rather than only inside `manifest-parity.test.ts`, and no caller can widen a frozen envelope in
 * place. Only the PNW pilot exists today, so this is a constant lookup; a multi-region deployment
 * adds a `NEXT_PUBLIC_PLANTGEO_REGION`-keyed registry here rather than a caller picking a manifest
 * itself. See `src/lib/region/AGENTS.md`.
 */
export function getRegion(): Region {
  if (cachedRegion === undefined) {
    cachedRegion = deepFreeze(regionSchema.parse(PNW));
  }
  return cachedRegion;
}

/**
 * The manifest's admin codes reduced to their subdivision suffixes, as the literal tuple Zod's
 * `z.enum` and Drizzle's enum builders require.
 *
 * The values come from the VALIDATED, frozen manifest (`getRegion()` -- which is why this sits
 * below it); only the tuple SHAPE is asserted, and it is computed from `PNW_ADMIN_CODES`, so it cannot drift from the manifest the way
 * the old hand-written `as unknown as readonly ["WA","OR","ID"]` could (STYLE-REVIEW-W2 S1/S2).
 */
export const REGION_SUBDIVISION_CODES: Readonly<SubdivisionCodesOf<typeof PNW_ADMIN_CODES>> =
  getRegion().adminCodes.map((adminCode) =>
    adminCode.slice(adminCode.indexOf("-") + 1)
  ) as SubdivisionCodesOf<typeof PNW_ADMIN_CODES>;
