import { describe, expect, it } from "vitest";
// A plain .mjs deploy script. The builder is exported from it precisely so the properties bag it
// writes to production can be asserted here rather than only proven by running the script.
import {
  REQUEST_KIND,
  REQUEST_STATUS,
  REQUEST_TYPE_BY_STRATEGY,
  buildRequestFeatureProperties,
} from "../../../scripts/backfill-strategy-requests.mjs";

/**
 * Phase 3 of `public_strategy_requests_20260913`: the one-time backfill's row shape.
 *
 * The script itself talks to production and nobody runs it from a test, so the part that can be
 * wrong silently -- the properties bag it writes -- is factored out as a pure function and pinned
 * here. The bag must match what `interventions.submitRequest` writes, because the map layer, the
 * detail modal and the visibility rule all read that one shape; a migrated request that differs by
 * a field is a request that renders differently from a freshly submitted one.
 */

const LEGACY_ROW = {
  id: "11111111-1111-4111-8111-111111111111",
  user_id: "22222222-2222-4222-8222-222222222222",
  strategy_type: "water_harvesting",
  title: "Swales above the north pasture",
  description: "Runoff cuts the hillside every spring.",
  lat: 43.6150,
  lon: -116.2023,
  created_at: new Date("2026-07-04T12:30:00Z"),
};

describe("buildRequestFeatureProperties", () => {
  it("writes submitRequest's discriminators: request kind, land category, published status", () => {
    const properties = buildRequestFeatureProperties(LEGACY_ROW);

    expect(properties.kind).toBe("request");
    expect(REQUEST_KIND).toBe("request");
    expect(properties.category).toBe("land");
    // Direct-to-published, no review queue (OQ-A sub-decision (a)).
    expect(REQUEST_STATUS).toBe("published");
  });

  it("maps title to name, matching submitIntervention's field so one bag describes both kinds", () => {
    const properties = buildRequestFeatureProperties(LEGACY_ROW);

    expect(properties.name).toBe("Swales above the north pasture");
    expect(properties).not.toHaveProperty("title");
  });

  it("builds a GeoJSON Point in [longitude, latitude] order, not the column order", () => {
    const properties = buildRequestFeatureProperties(LEGACY_ROW);

    expect(properties.geometry).toEqual({
      type: "Point",
      coordinates: [-116.2023, 43.615],
    });
  });

  it("carries the submitter forward and never re-creates the team-private boundary", () => {
    const properties = buildRequestFeatureProperties(LEGACY_ROW);

    expect(properties.submittedByUserId).toBe(LEGACY_ROW.user_id);
    // A `submittedByTeamId` here would re-gate through isFeatureVisibleTo's workspace clause --
    // the exact boundary this track removes.
    expect(properties.submittedByTeamId).toBeNull();
    expect(properties.publicationConsent).toBe(true);
  });

  it("stamps provenance so the copy is idempotent and the original date survives", () => {
    const properties = buildRequestFeatureProperties(LEGACY_ROW);

    expect(properties.migratedFromStrategyRequestId).toBe(LEGACY_ROW.id);
    expect(properties.originalCreatedAt).toBe("2026-07-04T12:30:00.000Z");
  });

  it("preserves a null description rather than inventing prose", () => {
    const properties = buildRequestFeatureProperties({
      ...LEGACY_ROW,
      description: null,
    });

    expect(properties.description).toBeNull();
  });

  it("maps all six legacy strategy types onto land InterventionType members", () => {
    expect(Object.keys(REQUEST_TYPE_BY_STRATEGY).sort()).toEqual([
      "biochar",
      "cover_cropping",
      "keyline",
      "reforestation",
      "silvopasture",
      "water_harvesting",
    ]);
    for (const [legacy, unified] of Object.entries(REQUEST_TYPE_BY_STRATEGY)) {
      expect(unified).toBe(legacy);
    }
  });

  it("refuses to guess at an unmapped strategy type", () => {
    expect(() =>
      buildRequestFeatureProperties({ ...LEGACY_ROW, strategy_type: "cloud_seeding" })
    ).toThrow(/unmapped strategy_type/);
  });

  it("refuses a row whose coordinates are not finite", () => {
    expect(() =>
      buildRequestFeatureProperties({ ...LEGACY_ROW, lat: null })
    ).toThrow(/finite coordinate pair/);
  });
});
