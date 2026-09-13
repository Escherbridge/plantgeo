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
];

export const AIR_INTERVENTION_TYPES: InterventionType[] = ["cloud_seeding"];
