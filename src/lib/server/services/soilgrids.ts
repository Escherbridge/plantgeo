export interface SoilProperties {
  ph: number;
  organicCarbon: number;
  nitrogen: number;
  bulkDensity: number;
  cec: number;
  ocd: number;
}

export const SOIL_EVIDENCE_UNAVAILABLE_CODE =
  "VALIDATED_SOIL_RELEASE_NOT_PUBLISHED" as const;

/** Marks the warehouse publication gate separately from an upstream failure. */
export class SoilEvidenceUnavailableError extends Error {
  readonly code = SOIL_EVIDENCE_UNAVAILABLE_CODE;

  constructor() {
    super(
      "Soil properties are unavailable until a validated warehouse release is published"
    );
    this.name = "SoilEvidenceUnavailableError";
  }
}

/** Refuses request-time upstream soil facts outside the warehouse contract. */
export async function getSoilProperties(
  lat: number,
  lon: number
): Promise<SoilProperties> {
  if (!Number.isFinite(lat) || lat < -90 || lat > 90) {
    throw new RangeError("Latitude must be between -90 and 90");
  }
  if (!Number.isFinite(lon) || lon < -180 || lon > 180) {
    throw new RangeError("Longitude must be between -180 and 180");
  }
  throw new SoilEvidenceUnavailableError();
}
