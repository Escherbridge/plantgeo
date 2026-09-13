/**
 * Phase 2 of `public_strategy_requests_20260913` (OQ-D): the two type
 * vocabularies unify onto `InterventionType`.
 *
 * The retired private path (`community.ts`'s `STRATEGY_TYPES`) carried six
 * types, five of which already existed verbatim in `InterventionType`. The
 * sixth, `water_harvesting`, is added here as a LAND-category member -- a
 * strategy request is land-category-only, and `cloud_seeding` stays the single
 * air-category type no request flow ever offers.
 */
import { describe, expect, it } from "vitest";
import {
  AIR_INTERVENTION_TYPES,
  LAND_INTERVENTION_TYPES,
  type InterventionType,
} from "@/lib/environmental/intervention";
import {
  INTERVENTION_TYPE_LABELS,
  TYPES_BY_CATEGORY,
} from "@/lib/environmental/intervention-form";

/** Exactly the vocabulary the retired `strategy_requests` submit form offered. */
const RETIRED_STRATEGY_TYPES = [
  "keyline",
  "silvopasture",
  "reforestation",
  "biochar",
  "water_harvesting",
  "cover_cropping",
] as const;

describe("unified intervention/request type vocabulary (OQ-D)", () => {
  it("carries `water_harvesting` as a land-category member", () => {
    expect(LAND_INTERVENTION_TYPES).toContain("water_harvesting");
    expect(AIR_INTERVENTION_TYPES).not.toContain("water_harvesting");
    // Type-level assertion: the union must admit it, not merely the array.
    const asType: InterventionType = "water_harvesting";
    expect(asType).toBe("water_harvesting");
  });

  it("absorbs every retired strategy-request type without losing one", () => {
    for (const strategyType of RETIRED_STRATEGY_TYPES) {
      expect(LAND_INTERVENTION_TYPES, strategyType).toContain(strategyType);
    }
  });

  it("keeps `cloud_seeding` the only air type, reachable by no request", () => {
    expect(AIR_INTERVENTION_TYPES).toEqual(["cloud_seeding"]);
    expect(LAND_INTERVENTION_TYPES).not.toContain("cloud_seeding");
  });

  it("labels and category buckets stay in step with the widened union", () => {
    expect(INTERVENTION_TYPE_LABELS.water_harvesting).toBe("Water Harvesting");
    expect(TYPES_BY_CATEGORY.land).toContain("water_harvesting");
    for (const type of [...LAND_INTERVENTION_TYPES, ...AIR_INTERVENTION_TYPES]) {
      expect(INTERVENTION_TYPE_LABELS[type], type).toBeTruthy();
    }
  });
});
