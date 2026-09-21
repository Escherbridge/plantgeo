/**
 * The land-context SERVER surfaces under the second manifest, and the one literal that may stay.
 *
 * `second-region-catalogue.test.tsx` proves the map-toggle half of `federation.md` §2. This file
 * proves the half W8 found unproven: the tRPC router, the bounded readers and the agent tools were
 * region-independent, so a `kenya-highlands` deployment advertised `state: "WA"`, described itself
 * as covering "WA/OR/ID" and answered a Nairobi AOI `outside_pilot_states` -- a BUDGET refusal
 * standing in for a governed absence (STYLE-REVIEW-W8 B1).
 *
 * The last test is the checkable half of the reason `REGION_SUBDIVISION_CODES` stays the pilot's
 * compile-time tuple: it fails the day any registered manifest binds a land-context source, which
 * is the day the storage vocabulary has to move behind the selected manifest with it.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { KENYA_HIGHLANDS } from "@/lib/region/kenya_highlands";
import { PNW } from "@/lib/region/pnw";
import { getRegion } from "@/lib/region/region";
import { landContextTools, callLandContextTool } from "@/lib/server/services/land-context-tools";
import {
  readBoundaryByParcelKey,
  readBoundedAoiIntersection,
  readContactsForSubject,
  readCoverageForRegion,
  readPointContainment,
} from "@/lib/server/services/land-context";

const SECOND_REGION_SLUG = "kenya-highlands";

/** Nairobi-ish: inside the second region's envelope, nowhere near the pilot's. */
const NAIROBI = { lon: 36.82, lat: -1.29 };
const NAIROBI_BBOX = { west: 36.7, south: -1.4, east: 36.9, north: -1.2 };

beforeEach(() => {
  vi.stubEnv("NEXT_PUBLIC_PLANTGEO_REGION", SECOND_REGION_SLUG);
});

afterEach(() => {
  vi.unstubAllEnvs();
});

describe("the bounded readers under a region that binds no land-context source", () => {
  it("answers the governed absence rather than a budget refusal, for every reader", async () => {
    const point = await readPointContainment(NAIROBI.lon, NAIROBI.lat);
    const area = await readBoundedAoiIntersection(NAIROBI_BBOX);
    const contacts = await readContactsForSubject("some-subject", null);
    const parcel = await readBoundaryByParcelKey({
      sourceNamespace: "ke-nairobi",
      originalId: "1",
      // A code no manifest admits: the region gate must answer BEFORE any state check, or the
      // caller is told its input was wrong when the layer is simply not here.
      state: "WA",
    });
    const coverage = await readCoverageForRegion("WA", null);

    expect(point.status).toBe("ok");
    expect(area.status).toBe("ok");
    expect(contacts.status).toBe("ok");
    expect(parcel.status).toBe("ok");
    expect(coverage.status).toBe("ok");
    if (point.status !== "ok" || area.status !== "ok") throw new Error("unreachable");
    if (contacts.status !== "ok" || parcel.status !== "ok" || coverage.status !== "ok") {
      throw new Error("unreachable");
    }
    expect(point.data[0].coverageState).toBe("source_unbound_for_region");
    expect(area.data[0].coverageState).toBe("source_unbound_for_region");
    expect(contacts.data[0].coverageState).toBe("source_unbound_for_region");
    expect(parcel.data.coverageState).toBe("source_unbound_for_region");
    expect(coverage.data.coverageState).toBe("source_unbound_for_region");
  });

  it("names the region and never calls the absence a gap in the record", async () => {
    const point = await readPointContainment(NAIROBI.lon, NAIROBI.lat);
    if (point.status !== "ok") throw new Error("unreachable");
    const [sentence] = point.data[0].unresolvedGaps;
    expect(sentence).toContain("Kenya Highlands");
    expect(sentence).toContain("not available in this region");
    expect(sentence).not.toContain("budget");
  });
});

describe("the agent tools under a region that binds no land-context source", () => {
  it("stays registered, so the agent never claims not to know the surface", () => {
    expect(landContextTools().map((tool) => tool.name)).toEqual([
      "resolve_land_boundary_at_point",
      "resolve_land_boundary_in_area",
      "resolve_land_boundary_by_parcel_key",
      "lookup_land_contacts_for_subject",
      "land_context_coverage_status",
      "draft_land_inquiry_text",
      "lookup_land_contacts_at_point",
      "lookup_land_contacts_in_area",
      "read_crop_cover_in_area",
    ]);
  });

  it("describes this region rather than the pilot, and offers no pilot state", () => {
    const admittedCodes = getRegion().adminCodes.map((code) => code.slice(code.indexOf("-") + 1));
    for (const tool of landContextTools()) {
      expect(tool.description, tool.name).not.toContain("WA/OR/ID");
      expect(tool.description, tool.name).toContain("Kenya Highlands");
      const properties = (tool.input_schema as { properties?: Record<string, unknown> }).properties ?? {};
      const state = properties.state as { enum?: string[] } | undefined;
      if (state?.enum !== undefined) expect(state.enum, tool.name).toEqual(admittedCodes);
    }
  });

  it("refuses with a governed absence rather than an empty success", async () => {
    const answer = JSON.parse(
      await callLandContextTool("land_context_coverage_status", { state: "WA" })
    ) as Record<string, unknown>;
    expect(answer.error).toBe("not_available_in_region");
    expect(answer.unbound_layers).toEqual(["land-context"]);
    expect(answer.region_display_name).toBe("Kenya Highlands");
    expect(String(answer.note)).toContain("not a gap in the record");
  });
});

describe("the reason REGION_SUBDIVISION_CODES may stay the pilot's tuple", () => {
  it("allows only the pilot to bind the current physical land-context schema", () => {
    // The day this fails, the Drizzle `pgEnum` and the Parquet row schema stop describing the only
    // region that has a land-context plane, and both must move behind the selected manifest.
    const registeredManifests: { slug: string; enabledLayers: readonly { layerSlug: string }[] }[] = [
      PNW,
      KENYA_HIGHLANDS,
    ];
    for (const manifest of registeredManifests) {
      expect(
        manifest.enabledLayers.some((binding) => binding.layerSlug === "land-context"),
        manifest.slug
      ).toBe(manifest.slug === PNW.slug);
    }
  });
});
