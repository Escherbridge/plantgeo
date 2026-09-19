import { z } from "zod";
import { KENYA_HIGHLANDS, KENYA_HIGHLANDS_ADMIN_CODES } from "@/lib/region/kenya_highlands";
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
    /**
     * The platform's whole layer vocabulary, mirroring `layer_availability.py`'s
     * `PLATFORM_LAYER_SLUGS`; NOT this region's bindings. `layerBindingInRegion` reads it to tell a
     * governed absence (in the vocabulary, not in `enabledLayers`) from a slug that is not a
     * federated layer at all (STYLE-REVIEW-W5 B1).
     */
    platformLayers: z.array(z.string()),
    enabledLayers: z.array(layerBindingSchema),
  })
  .strict()
  .refine(
    (region) => region.enabledLayers.every((binding) => region.platformLayers.includes(binding.layerSlug)),
    {
      message: "every enabledLayers binding must name a slug this manifest also lists in platformLayers",
      path: ["enabledLayers"],
    }
  );

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

/** One manifest this deployment ships: the literal object, beside the admin-code tuple declared with it. */
interface RegisteredManifest {
  /** Parsed through `regionSchema` on first read; never trusted as a `Region` before that. */
  readonly manifest: unknown;
  /** The `const` tuple declared next to the manifest's values, which its `adminCodes` must equal. */
  readonly declaredAdminCodes: readonly string[];
}

/** The manifest `getRegion()` resolves when nothing selects another; the pilot, and only by default. */
export const PILOT_REGION_SLUG = "pnw";

/**
 * Every region manifest compiled into this bundle, keyed by slug -- the registry
 * `src/lib/region/AGENTS.md` promised and `federation.md` §1 describes.
 *
 * Mirrors `foundation/region/manifest.py`'s `_MANIFEST_FILE_BY_SLUG`: same slugs, same defaulting,
 * same refusal for an unregistered one. A region is DATA here, so adding the next deployment is a
 * manifest module and one line in this table, not a branch in any caller.
 */
const REGISTERED_MANIFEST_BY_SLUG: Readonly<Record<string, RegisteredManifest>> = {
  [PILOT_REGION_SLUG]: { manifest: PNW, declaredAdminCodes: PNW_ADMIN_CODES },
  "kenya-highlands": { manifest: KENYA_HIGHLANDS, declaredAdminCodes: KENYA_HIGHLANDS_ADMIN_CODES },
};

/**
 * The slug this deployment selects, read fresh on every call rather than captured at import.
 *
 * `process.env.NEXT_PUBLIC_PLANTGEO_REGION` is written as the full static member expression Next
 * inlines at build time; an unset or empty value is the pilot, and an unrecognised one is refused
 * by `getRegion()` rather than silently served as the pilot's footprint under another name.
 */
function selectedRegionSlug(): string {
  const selected = process.env.NEXT_PUBLIC_PLANTGEO_REGION;
  return selected === undefined || selected === "" ? PILOT_REGION_SLUG : selected;
}

/** Populated lazily, one entry per slug actually resolved; keyed by slug so two regions cannot share one. */
const parsedRegionBySlug = new Map<string, Region>();

/**
 * Returns this deployment's one region manifest: the slug `NEXT_PUBLIC_PLANTGEO_REGION` selects.
 *
 * Parses the selected manifest through `regionSchema` and deep-freezes it on first read for that
 * slug, then returns the memoised value -- so the `.refine()` envelope-ordering checks actually run
 * once in production rather than only inside the parity tests, and no caller can widen a frozen
 * envelope in place. The cache is keyed by RESOLVED SLUG rather than held in one slot, so the
 * selection is re-read every call and a second region can never be served from the first one's
 * parse. See `src/lib/region/AGENTS.md`.
 */
export function getRegion(): Region {
  const slug = selectedRegionSlug();
  const alreadyParsed = parsedRegionBySlug.get(slug);
  if (alreadyParsed !== undefined) return alreadyParsed;
  const registered = REGISTERED_MANIFEST_BY_SLUG[slug];
  if (registered === undefined) {
    throw new Error(
      `unknown region '${slug}'; this bundle compiles in [${Object.keys(REGISTERED_MANIFEST_BY_SLUG).join(", ")}]. ` +
        `An unrecognised NEXT_PUBLIC_PLANTGEO_REGION is a configuration error, not a reason to serve the pilot's ` +
        `footprint under another region's name`
    );
  }
  const parsed = regionSchema.parse(registered.manifest);
  assertAdminCodesMatchDeclaredTuple(parsed.adminCodes, registered.declaredAdminCodes);
  const frozen = deepFreeze(parsed);
  parsedRegionBySlug.set(slug, frozen);
  return frozen;
}

/**
 * Refuse a manifest whose `adminCodes` are not exactly the tuple declared beside its values.
 *
 * For the pilot, `REGION_SUBDIVISION_CODES`, `RegionAdminCode` and `RegionSubdivisionCode` are all
 * derived from `PNW_ADMIN_CODES` rather than from the parsed manifest, which is what keeps them off
 * a module-level `getRegion()` read (STYLE-REVIEW-W4 S1). This is the check that makes the
 * derivation honest: a manifest that binds a code its own tuple does not carry fails closed on
 * first read instead of leaving every `RegionSubdivisionCode`-typed surface promising codes the
 * deployment no longer has.
 *
 * `declaredAdminCodes` is a PARAMETER rather than `PNW_ADMIN_CODES` read from inside, because the
 * rule is per manifest: each region declares its own tuple next to its own values, and the pilot's
 * would be the wrong thing to check a second region against.
 */
export function assertAdminCodesMatchDeclaredTuple(
  adminCodes: readonly string[],
  declaredAdminCodes: readonly string[]
): void {
  const agrees =
    adminCodes.length === declaredAdminCodes.length &&
    adminCodes.every((adminCode, index) => adminCode === declaredAdminCodes[index]);
  if (!agrees) {
    throw new Error(
      `region manifest adminCodes [${adminCodes.join(", ")}] differ from the tuple declared beside them ` +
        `[${declaredAdminCodes.join(", ")}]; the literal types every land-context surface carries are derived ` +
        `from the tuple, so the two may only be edited together`
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
 *
 * Still the PILOT'S tuple now that a second manifest exists, and the reason has been REPLACED
 * (STYLE-REVIEW-W8 B1). The old one -- "a second region never reaches them, `layerBindingInRegion`
 * answers `unbound` first" -- was false: that helper gated one map hook, while the land-context
 * tRPC router, bounded readers and agent tools all reached these literals region-independently and
 * would have offered a Kenya deployment WA/OR/ID. Those surfaces now read
 * `admittedSubdivisionCodes()` (`land-context/budgets.ts`) from the SELECTED manifest at call time
 * and refuse the layer outright where the region binds no land-context source
 * (`land-context/region-binding.ts`).
 *
 * What genuinely must stay a compile-time tuple is the STORAGE vocabulary: Drizzle's `pgEnum`
 * (`db/schema/land-context/shared.ts`) and the Parquet row schema (`parquet-reader.ts`) need a
 * literal tuple at module scope, and both describe the pilot's own physical land-context plane,
 * whose columns a second region could only gain through a migration of its own. That reason is
 * checked rather than asserted: `src/__tests__/region/land-context-second-region.test.ts` fails the
 * moment any registered manifest binds a `land-context` source, which is exactly when the storage
 * vocabulary stops being the pilot's alone.
 */
export const REGION_SUBDIVISION_CODES = subdivisionCodesOf(PNW_ADMIN_CODES);

/**
 * One region's admin codes reduced to their subdivision suffixes, as runtime strings.
 *
 * Takes the `Region` rather than reading one, so a caller's dependency stays visible and no module
 * scope can snapshot it. The literal-typed twin above is the pilot's compile-time tuple; this is
 * the answer for a surface that must be right in WHATEVER region this deployment selected.
 */
export function subdivisionCodesForRegion(region: Region): readonly string[] {
  return region.adminCodes.map((adminCode) => adminCode.slice(adminCode.indexOf("-") + 1));
}

/**
 * Whether a coverage census describes the region this bundle was compiled for.
 *
 * `PLANTGEO_REGION` (service) and `NEXT_PUBLIC_PLANTGEO_REGION` (bundle) are two independently
 * settable variables naming one fact, and nothing bound them: a deployment setting one and not the
 * other served one region's footprint over the other region's data undetectably (STYLE-REVIEW-W8
 * S1). `unstated` is a census that predates the field or states no region -- no claim, and never a
 * reason to refuse -- while `mismatch` is a STATED disagreement, which no reader may draw.
 */
export type RegionIdentityVerdict =
  | { kind: "agrees"; slug: string }
  | { kind: "unstated"; compiledSlug: string }
  | { kind: "mismatch"; compiledSlug: string; servedSlug: string };

export function regionIdentityVerdict(servedRegionSlug: string | null | undefined): RegionIdentityVerdict {
  const compiledSlug = getRegion().slug;
  if (servedRegionSlug === null || servedRegionSlug === undefined || servedRegionSlug === "") {
    return { kind: "unstated", compiledSlug };
  }
  return servedRegionSlug === compiledSlug
    ? { kind: "agrees", slug: compiledSlug }
    : { kind: "mismatch", compiledSlug, servedSlug: servedRegionSlug };
}
