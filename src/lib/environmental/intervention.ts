import { z } from "zod";

export interface CarbonPotential {
  currentOC: number;
  potentialGain: number;
  yearsToSaturation: number;
  confidenceClass: "high" | "medium" | "low";
}

export type InterventionType =
  | "reforestation"
  | "silvopasture"
  | "cover_cropping"
  | "biochar"
  | "keyline"
  /**
   * Land-category since 2026-09-13: the retired `strategy_requests` vocabulary
   * (`community.ts`'s `STRATEGY_TYPES`) carried this type and `InterventionType`
   * did not, so unifying the two enums (track
   * `public_strategy_requests_20260913`, OQ-D) is one additive union member
   * rather than two lists a form and a label map keep in sync by hand.
   */
  | "water_harvesting"
  | "cloud_seeding";

/** Whether an intervention acts on land parcels or on airborne/atmospheric targets. */
export const InterventionCategorySchema = z.enum(["land", "air"]);
export type InterventionCategory = z.infer<typeof InterventionCategorySchema>;

/** Every `InterventionType` defaults to `"land"` except the air-category types below. */
export const LAND_INTERVENTION_TYPES: InterventionType[] = [
  "reforestation",
  "silvopasture",
  "cover_cropping",
  "biochar",
  "keyline",
  "water_harvesting",
];

export const AIR_INTERVENTION_TYPES: InterventionType[] = ["cloud_seeding"];
