import { z } from "zod";
import { PNW } from "@/lib/region/pnw";

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

/** How `floor_to_resolution` (`warehouse/parquet/tiers.py:401`) maps a coordinate onto its cell. */
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
 * The literal admin-code union the PNW pilot manifest declares. `regionSchema` itself only asserts
 * `z.array(z.string())` (a future multi-region manifest may add codes this deployment never binds),
 * so this literal mirrors `pnw.ts`'s `adminCodes` values by hand rather than indexing `typeof PNW`,
 * which would widen to `string` the moment `pnw.ts`'s array literal is read outside a `const`
 * context. Callers that need a typed two-letter state code (`PnwStateCode` in
 * `src/lib/server/db/schema/land-context/shared.ts`) narrow from this.
 */
export type RegionAdminCode = "US-WA" | "US-OR" | "US-ID";

/**
 * Returns the deployment's one region manifest.
 *
 * Only the PNW pilot exists today, so this is a constant lookup; a multi-region deployment adds a
 * `NEXT_PUBLIC_PLANTGEO_REGION`-keyed registry here rather than a caller picking a manifest itself.
 * See `src/lib/region/AGENTS.md`.
 */
export function getRegion(): Region {
  return PNW;
}
