/**
 * Shared types for the land-context bounded readers.
 *
 * Re-exported from `@/lib/environmental/land-context-contract` (the client-safe home for these
 * pure type declarations) so this module's existing server-side importers are unaffected. See
 * that file's own docstring for why the types moved: browser components need them too, and
 * `scripts/check-client-server-imports.mjs` enforces the `@/lib/server/**` boundary without a
 * type-only exemption.
 */

export type {
  BoundaryVersionRef,
  BoundedOkResult,
  BoundedResponse,
  BudgetExceededResult,
  CoverageState,
  LandContextResult,
  OrganizationOfficeRef,
  OverlapBasis,
  ParcelKey,
  PilotState,
  PlaceOfficeTopicRelationshipRef,
  PublicContactRouteRef,
  RouteMeaning,
  SourceReleaseRef,
} from "@/lib/environmental/land-context-contract";
