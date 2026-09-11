import type { ClimateFieldSignalId } from "@/lib/environmental/climate-field";

/** Shared source and shape identifiers for climate rendering and hit testing. */
export function climateFieldLayerIds(signal: ClimateFieldSignalId) {
  const sourceId = `climate-field-${signal}`;
  return {
    sourceId,
    fillId: `${sourceId}-fill`,
    outlineId: `${sourceId}-outline`,
    isobandFillId: `${sourceId}-isoband-fill`,
    isolineId: `${sourceId}-isoline`,
    pointId: `${sourceId}-point`,
  };
}
