import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  DATA_INTERVENTION_LANES,
  DATA_PROVENANCE_LABELS,
  DataInterventionDetailsSchema,
  isDataInterventionType,
  readDataInterventionDetails,
  validateDataInterventionDetails,
} from "@/lib/environmental/data-intervention";

const collection = { lane: "water-gauges", collectionMethod: "Read a fixed staff gauge" };

describe("community data intervention details", () => {
  beforeEach(() => { vi.useFakeTimers(); vi.setSystemTime(new Date("2026-09-20T12:00:00Z")); });
  afterEach(() => { vi.useRealTimers(); });

  it.each(DATA_INTERVENTION_LANES)("accepts a collection plan for $id without inventing observation evidence", ({ id }) => {
    expect(validateDataInterventionDetails("data_collection", { ...collection, lane: id })).toBeNull();
  });

  it("requires real evidence for submission, independently of a collection plan", () => {
    expect(validateDataInterventionDetails("data_submission", collection)).toBe("Provide the observation date");
    expect(validateDataInterventionDetails("data_submission", { ...collection, observedOn: "2026-09-19" })).toBe("Provide a dataset or evidence link");
    expect(validateDataInterventionDetails("data_submission", { ...collection, observedOn: "2026-09-19", dataUrl: "https://example.org/observations.csv" })).toBeNull();
  });

  it.each(["calendar", "signal", "unknown-lane", ""])("refuses an internal or undeclared lane: %s", (lane) => {
    expect(validateDataInterventionDetails("data_collection", { ...collection, lane })).toBeTruthy();
  });

  it.each(["0000-01-01", "2025-02-29", "2026-02-30", "2026-13-01", "2026-00-01", "2026-09-31", "2026-9-20", "2026-09-21", "garbage"])("refuses a malformed or future observed day: %s", (observedOn) => {
    expect(DataInterventionDetailsSchema.safeParse({ ...collection, observedOn }).success).toBe(false);
  });

  it.each(["2024-02-29", "2026-09-20"])("accepts a real past or present day: %s", (observedOn) => {
    expect(DataInterventionDetailsSchema.safeParse({ ...collection, observedOn }).success).toBe(true);
  });

  it.each(["not a url", "", "javascript:alert(1)", "data:text/plain,hello", "ftp://example.org/readings", "/readings.csv"])("refuses unsafe or malformed links without throwing: %s", (dataUrl) => {
    expect(() => DataInterventionDetailsSchema.safeParse({ ...collection, dataUrl })).not.toThrow();
    expect(DataInterventionDetailsSchema.safeParse({ ...collection, dataUrl }).success).toBe(false);
  });

  it("rejects missing details, empty methods, oversized fields and claimed provenance", () => {
    for (const details of [undefined, {}, { ...collection, collectionMethod: "  " }, { ...collection, collectionMethod: "x".repeat(2001) }, { ...collection, dataOrigin: "verified_source" }, { ...collection, provenance: { verified: true } }]) {
      expect(validateDataInterventionDetails("data_collection", details)).toBeTruthy();
    }
    expect(validateDataInterventionDetails("biochar", collection)).toBeTruthy();
    expect(validateDataInterventionDetails("biochar", undefined)).toBeNull();
  });

  it("reads valid properties without relabeling malformed or absent evidence", () => {
    expect(readDataInterventionDetails({ ...collection, collectionMethod: "  Read a gauge  " })).toEqual({ ...collection, collectionMethod: "Read a gauge" });
    expect(readDataInterventionDetails({ ...collection, dataOrigin: "community" })).toBeNull();
    expect(readDataInterventionDetails(null)).toBeNull();
    expect(readDataInterventionDetails(undefined)).toBeNull();
  });

  it("keeps origin separate from publication status and intervention type", () => {
    expect(isDataInterventionType("data_collection")).toBe(true);
    expect(isDataInterventionType("data_submission")).toBe(true);
    expect(isDataInterventionType("published")).toBe(false);
    expect(DATA_PROVENANCE_LABELS.community).toBe("Community collected data");
    expect(DATA_PROVENANCE_LABELS.verified_source).toBe("Verified source data");
  });
});
