import type { MtbsSnapshotMetadata } from "@/lib/environmental/mtbs-snapshot";

export const snapshotMetadata: MtbsSnapshotMetadata = {
  schema: "mtbs-current-snapshot/v1", manifestSha256: "a".repeat(64), mode: "full_replacement",
  availableDay: "2026-09-11", capturedFrom: "2026-09-10T19:00:00Z", capturedThrough: "2026-09-10T19:05:00Z",
  coveredYears: { from: 2018, to: 2026 }, bbox: [-125, 42, -111, 49], crs: "EPSG:4326",
  captureComplete: true, partialFireYears: [2023, 2024, 2025, 2026], sourceRowCount: 2,
  sourceUrl: "https://apps.fs.usda.gov/arcx/rest/services/EDW/EDW_MTBS_01/MapServer/63/query",
};

export const snapshotWire = {
  schema: snapshotMetadata.schema, manifest_sha256: snapshotMetadata.manifestSha256, mode: snapshotMetadata.mode,
  available_day: snapshotMetadata.availableDay, captured_from: snapshotMetadata.capturedFrom,
  captured_through: snapshotMetadata.capturedThrough, covered_years: snapshotMetadata.coveredYears,
  bbox: snapshotMetadata.bbox, crs: snapshotMetadata.crs, capture_complete: snapshotMetadata.captureComplete,
  partial_fire_years: snapshotMetadata.partialFireYears, source_row_count: snapshotMetadata.sourceRowCount,
  source_url: snapshotMetadata.sourceUrl,
};
