/** SoilGrids is withheld until a governed source-direct Parquet lookup is admitted. */

export interface SoilProperties {
  ph: number;
  organicCarbon: number;
  nitrogen: number;
  bulkDensity: number;
  cec: number;
  ocd: number;
}

export const SOIL_EVIDENCE_UNAVAILABLE_CODE = "VALIDATED_SOIL_RELEASE_NOT_PUBLISHED" as const;

/** A validated Parquet soil release is not available for the requested location. */
export class SoilEvidenceUnavailableError extends Error {
  readonly code = SOIL_EVIDENCE_UNAVAILABLE_CODE;

  constructor(message = "Soil properties are unavailable until the source-direct Parquet lane is published") {
    super(message);
    this.name = "SoilEvidenceUnavailableError";
  }
}

/** A source-direct soil lookup is temporarily unavailable and may be retried after publication. */
export class SoilUpstreamUnavailableError extends Error {
  readonly code = "SOIL_UPSTREAM_UNAVAILABLE" as const;

  constructor(message = "The source-direct soil Parquet lane is temporarily unavailable") {
    super(message);
    this.name = "SoilUpstreamUnavailableError";
  }
}

/** Refuse the retired HTTP and PostgreSQL cache path; callers must use the Parquet lane. */
export async function getSoilProperties(_lat: number, _lon: number): Promise<SoilProperties> {
  throw new SoilEvidenceUnavailableError();
}
