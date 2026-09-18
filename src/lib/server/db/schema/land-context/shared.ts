/**
 * Shared primitives for the `land_context` reference plane.
 *
 * See `conductor/tracks/pnw_land_context_reference_plane_20260911/spec.md`
 * ("Reference records and identity") for the governing contract, and
 * `conductor/code_styleguides/layer-lanes.md` §1a for the `static_lookup`
 * temporal nature these tables declare.
 *
 * This module is NOT wired into `src/lib/server/db/schema.ts` yet -- these
 * are proposed physical names per the spec's "physical names and grain must
 * be frozen before coding" instruction. An integrator re-exports the tables
 * from `schema.ts` (adding `land_context` to `drizzle.config.ts`'s schema
 * glob) once the freeze is confirmed. Nothing here is queried by app code.
 */
import { pgSchema, customType } from "drizzle-orm/pg-core";
import { getRegion, type RegionAdminCode } from "@/lib/region/region";

export const landContextSchema = pgSchema("land_context");

// Mirrors the customType shape in `schema.ts` (that module does not export its
// customTypes, so they can't be imported directly). Kept structurally
// identical on purpose -- see `src/lib/server/db/AGENTS.md`.
export const spatialGeometry = customType<{ data: string; driverData: string }>({
  dataType: () => "geometry(GEOMETRY,4326)",
});

export const spatialPoint = customType<{ data: string; driverData: string }>({
  dataType: () => "geometry(POINT,4326)",
});

/**
 * WA/OR/ID per the spec's geographic boundary; not a general US state enum.
 *
 * Deprecated aliases for `getRegion().adminCodes`'s two-letter suffixes
 * (`federation.md` §5 step 2); new code should read `RegionAdminCode`
 * directly rather than importing these.
 */
export const PNW_STATE_CODES = getRegion().adminCodes.map(
  (adminCode) => adminCode.split("-")[1],
) as unknown as readonly ["WA", "OR", "ID"];
export type PnwStateCode = (typeof PNW_STATE_CODES)[number];
export type { RegionAdminCode };

/**
 * Source admission verdict, verbatim from spec §"Source/releases" and
 * §"Admission, refresh, gaps and absences". A source owes exactly one of
 * these for its exact distribution/field-set/coverage/version -- endpoint
 * reachability alone is never "current".
 */
export const ADMISSION_VERDICTS = [
  "not_checked",
  "current",
  "stale",
  "partial",
  "source_unavailable",
  "withheld_by_terms",
  "unsupported_history",
  "out_of_coverage",
] as const;
export type AdmissionVerdict = (typeof ADMISSION_VERDICTS)[number];

/**
 * `static_lookup` per layer-lanes.md §1a: a version stamp, never a
 * measurement taken on a date. `release_series` is reserved for the
 * separately dated annual crop-summary facet the spec calls out -- it must
 * never share one temporal column with boundary/office/territory data.
 */
export const TEMPORAL_NATURES = ["static_lookup", "release_series"] as const;
export type TemporalNature = (typeof TEMPORAL_NATURES)[number];

/**
 * The four typed route meanings from spec §"Contact meanings and contact
 * discovery". `introduction_forwarding` is the one that needs the explicit
 * `forwardingDocumented` flag on `publicContactRoutes` -- unknown forwarding
 * capability must never be inferred true from this type alone.
 */
export const CONTACT_ROUTE_TYPES = [
  "direct_responsible_agency",
  "records_property_assistance",
  "subject_matter_adviser",
  "introduction_forwarding",
] as const;
export type ContactRouteType = (typeof CONTACT_ROUTE_TYPES)[number];
