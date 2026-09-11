/** Evidence for one complete query capture of a still-partial MTBS product. */
export interface MtbsSnapshotMetadata {
  schema: "mtbs-current-snapshot/v1";
  manifestSha256: string;
  mode: "full_replacement";
  availableDay: string;
  capturedFrom: string;
  capturedThrough: string;
  coveredYears: { from: number; to: number };
  bbox: [number, number, number, number];
  crs: "EPSG:4326";
  captureComplete: true;
  partialFireYears: number[];
  sourceRowCount: number;
  sourceUrl: string;
}
