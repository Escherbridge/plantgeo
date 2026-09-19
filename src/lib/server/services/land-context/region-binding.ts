/**
 * Whether THIS deployment's region binds a land-context source, for every server surface that reads
 * the plane. Rationale: `src/lib/server/services/land-context/AGENTS.md` §region-binding.
 */

import { z } from "zod";
import {
  LAND_CONTEXT_REGION_LAYER_SLUG,
  layerBindingInRegion,
} from "@/lib/map/layer-region-binding";
import { getRegion, subdivisionCodesForRegion } from "@/lib/region/region";
import type { PilotState } from "@/lib/environmental/land-context-contract";

/**
 * True when this region binds a source for `land-context`; asked fresh on every call.
 *
 * Routed through the ONE binding rule with a `null` payload, which takes its manifest arm: a server
 * process holds no slider capability payload, and the compiled manifest is the whole evidence it
 * needs. Answering this question a second way here is how the two verdicts drift apart, which is
 * what `layerBindingInRegion` was consolidated to prevent (STYLE-REVIEW-W5 B1).
 */
export function isLandContextBoundInRegion(): boolean {
  return layerBindingInRegion(null, LAND_CONTEXT_REGION_LAYER_SLUG) !== "unbound";
}

/**
 * The subdivision codes this deployment's region admits, read from the SELECTED manifest per call.
 *
 * The request vocabulary, as opposed to the pilot's compile-time storage tuple (`PILOT_STATES`):
 * every wire surface that accepts a "state" validates against this, so a second region can never be
 * offered WA/OR/ID (STYLE-REVIEW-W8 B1).
 */
export function admittedSubdivisionCodes(): readonly string[] {
  return subdivisionCodesForRegion(getRegion());
}

/** True when the region admits this subdivision code; the per-request membership test. */
export function isAdmittedSubdivisionCode(code: string): boolean {
  return admittedSubdivisionCodes().includes(code);
}

/** Longest subdivision suffix an ISO 3166-2 admin code carries; bounds the field before it is read. */
const MAX_SUBDIVISION_CODE_LENGTH = 3;

/**
 * The ONE validator every wire surface uses for a caller-supplied subdivision code.
 *
 * `.refine` runs per parse, so the admitted set is the SELECTED manifest's and never a module-scope
 * snapshot — a `z.enum(PILOT_STATES)` at module scope is exactly what offered WA/OR/ID to a region
 * that binds no land-context source at all (STYLE-REVIEW-W8 B1).
 *
 * The `PilotState` narrowing at the end is a cast with a checked reason: every caller passes the
 * unbound gate first, the pilot is the only manifest that binds `land-context`, and
 * `src/__tests__/region/land-context-second-region.test.ts` fails the day that stops being true.
 */
export const admittedSubdivisionCodeSchema = z
  .string()
  .trim()
  .min(1)
  .max(MAX_SUBDIVISION_CODE_LENGTH)
  .refine(isAdmittedSubdivisionCode, {
    message: "not a subdivision code this deployment's region admits",
  })
  .transform((code) => code as PilotState);

/** How a land-context surface names this deployment's region in a refusal it hands a reader. */
export function regionDisplayName(): string {
  return getRegion().displayName;
}

/** The selected region's slug, for a refusal payload an operator has to correlate with a deploy. */
export function regionSlug(): string {
  return getRegion().slug;
}

/**
 * The one sentence every land-context surface says when the region binds no source for the layer.
 *
 * A governed absence and never an outage, a budget, or a gap in a record (`federation.md` §2): the
 * platform holds nothing for this layer ANYWHERE in this region, so there is no area, parcel or
 * subject that would answer. Worded so a reader — or a model relaying it — cannot restate it as
 * "nothing found here", which is a claim about the ground rather than about the deployment.
 */
export function landContextUnboundDetail(): string {
  return (
    `Land context is not available in this region: this deployment covers ${regionDisplayName()} ` +
    `and its region manifest binds no land-context data source, so no parcel, boundary or office ` +
    `record exists for it anywhere here. This is not a gap in the record and not a failed lookup.`
  );
}
