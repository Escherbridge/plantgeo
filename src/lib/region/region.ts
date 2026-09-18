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
 * The literal admin-code union this deployment declares, read off the ONE `const` tuple
 * (`pnw.ts`'s `PNW_ADMIN_CODES`) that both this union and `REGION_SUBDIVISION_CODES` derive from.
 *
 * `PNW.adminCodes` is the same tuple spread into the manifest, and `pnw.ts` pins that with a
 * `satisfies` clause, so the manifest's runtime array cannot be edited apart from the tuple the
 * types promise -- the joint `as SubdivisionCodesOf<...>` used to assert rather than check
 * (STYLE-REVIEW-W4 S2). `regionSchema` still validates only `z.array(z.string())`, because a future
 * region may bind codes this one never does; `assertAdminCodesMatchDeclaredTuple` is what enforces
 * the agreement for THIS deployment, at runtime, on the parsed manifest.
 */
export type RegionAdminCode = (typeof PNW_ADMIN_CODES)[number];

/** "US-WA" -> "WA": one admin code's subdivision suffix, at type level. */
type SubdivisionCodeOf<Code extends string> = Code extends `${string}-${infer Subdivision}`
  ? Subdivision
  : never;

/** The two-letter subdivision code every admin code in this region carries. */
export type RegionSubdivisionCode = SubdivisionCodeOf<RegionAdminCode>;

/**
 * Strip the country prefix off a whole tuple of admin codes, keeping the TUPLE shape.
 *
 * The value and the type are computed from the SAME argument, so the one cast inside cannot join
 * two independently editable sources the way the old module-level
 * `getRegion().adminCodes.map(...) as SubdivisionCodesOf<typeof PNW_ADMIN_CODES>` did: that took
 * its value from the manifest and its type from the tuple, and nothing checked they agreed
 * (STYLE-REVIEW-W4 S1/S2). Callers get the literal tuple `z.enum` and Drizzle's enum builders need.
 */
function subdivisionCodesOf<Codes extends readonly string[]>(
  adminCodes: Codes
): { readonly [Index in keyof Codes]: SubdivisionCodeOf<Codes[Index]> } {
  return adminCodes.map((adminCode) => adminCode.slice(adminCode.indexOf("-") + 1)) as {
    readonly [Index in keyof Codes]: SubdivisionCodeOf<Codes[Index]>;
  };
}

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
    const parsed = regionSchema.parse(PNW);
    assertAdminCodesMatchDeclaredTuple(parsed.adminCodes);
    cachedRegion = deepFreeze(parsed);
  }
  return cachedRegion;
}

/**
 * Refuse a manifest whose `adminCodes` are not exactly `PNW_ADMIN_CODES`, in order.
 *
 * `REGION_SUBDIVISION_CODES`, `RegionAdminCode` and `RegionSubdivisionCode` are all derived from
 * that tuple rather than from the parsed manifest, which is what keeps them off a module-level
 * `getRegion()` read (STYLE-REVIEW-W4 S1). This is the check that makes the derivation honest: a
 * manifest that binds a code the tuple does not carry fails closed on first read instead of leaving
 * every `RegionSubdivisionCode`-typed surface promising codes the deployment no longer has.
 */
export function assertAdminCodesMatchDeclaredTuple(adminCodes: readonly string[]): void {
  const declared: readonly string[] = PNW_ADMIN_CODES;
  const agrees =
    adminCodes.length === declared.length &&
    adminCodes.every((adminCode, index) => adminCode === declared[index]);
  if (!agrees) {
    throw new Error(
      `region manifest adminCodes [${adminCodes.join(", ")}] differ from the declared PNW_ADMIN_CODES tuple ` +
        `[${declared.join(", ")}]; the literal types every land-context surface carries are derived from the ` +
        `tuple, so the two may only be edited together`
    );
  }
}

/**
 * The deployment's admin codes reduced to their subdivision suffixes, as the literal tuple Zod's
 * `z.enum` and Drizzle's enum builders require AT MODULE SCOPE.
 *
 * Derived from `PNW_ADMIN_CODES` -- a compile-time tuple with no manifest read -- and NOT from
 * `getRegion()`. A module-level `getRegion()` call is the import-time region read `federation.md`
 * §1 forbids and W2 B1 removed from the Python tree: it freezes whichever region resolved first,
 * which is exactly the trap waiting for the `NEXT_PUBLIC_PLANTGEO_REGION`-keyed registry
 * `src/lib/region/AGENTS.md` promises inside `getRegion()` (STYLE-REVIEW-W4 S1). The manifest is
 * still checked against this tuple -- by `assertAdminCodesMatchDeclaredTuple`, on first
 * `getRegion()` call, where a read belongs.
 */
export const REGION_SUBDIVISION_CODES = subdivisionCodesOf(PNW_ADMIN_CODES);
