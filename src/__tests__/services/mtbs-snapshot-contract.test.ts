import { describe, expect, it } from "vitest";
import { mtbsSnapshotWireSchema, normalizeMtbsSnapshot } from "@/lib/server/services/mtbs-snapshot-contract";
import { snapshotMetadata, snapshotWire } from "./mtbs-snapshot-fixture";

describe("governed MTBS snapshot descriptor", () => {
  it("preserves capture provenance separately from availability and partial-year mapping", () => {
    expect(normalizeMtbsSnapshot(mtbsSnapshotWireSchema.parse(snapshotWire))).toEqual(snapshotMetadata);
  });

  it.each([
    { capture_complete: false }, { mode: "delta" }, { schema: "unknown" }, { manifest_sha256: "unverified" },
    { available_day: "2026-09-10" }, { available_day: "2026-09-12" },
    { captured_through: "2026-09-10T19:10:01Z" }, { available_day: "2026-02-30" },
    { captured_from: "2026-09-10T20:00:00Z" }, { captured_through: "2026-09-10T19:05:00" },
    { covered_years: { from: 2026, to: 2018 } }, { bbox: [-111, 42, -125, 49] },
    { partial_fire_years: [2023, 2023] }, { partial_fire_years: [2027] },
    { partial_fire_years: [2026, 2023] }, { source_row_count: -1 }, { source_row_count: 2001 },
    { source_url: "https://user:secret@example.test/source" }, { ungoverned: true },
  ])("rejects invalid authority/scope metadata %j", (change) => {
    expect(mtbsSnapshotWireSchema.safeParse({ ...snapshotWire, ...change }).success).toBe(false);
  });

  it("uses the actual UTC capture instant when an explicit offset crosses the day boundary", () => {
    expect(mtbsSnapshotWireSchema.safeParse({ ...snapshotWire,
      captured_from: "2026-09-10T23:00:00-02:00", captured_through: "2026-09-10T23:05:00-02:00",
    }).success).toBe(false);
  });
});
