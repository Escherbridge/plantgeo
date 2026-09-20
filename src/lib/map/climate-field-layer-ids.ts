import {
  CLIMATE_FIELD_SIGNAL_IDS,
  type ClimateFieldSignalId,
} from "@/lib/environmental/climate-field";

export interface ClimateFieldLayerIds {
  sourceId: string;
  fillId: string;
  outlineId: string;
  isobandFillId: string;
  isolineId: string;
  pointId: string;
  labelId: string;
}

/** Canonical MapLibre ids for one climate signal. */
export function climateFieldLayerIdsFor(signal: ClimateFieldSignalId): ClimateFieldLayerIds {
  const sourceId = `climate-field-${signal}`;
  return {
    sourceId,
    fillId: `${sourceId}-fill`,
    outlineId: `${sourceId}-outline`,
    isobandFillId: `${sourceId}-isoband-fill`,
    isolineId: `${sourceId}-isoline`,
    pointId: `${sourceId}-point`,
    labelId: `${sourceId}-value-labels`,
  };
}

const CLIMATE_FIELD_GEOMETRY_ENTRIES = CLIMATE_FIELD_SIGNAL_IDS.flatMap((signal) => {
  const ids = climateFieldLayerIdsFor(signal);
  return [
    [ids.fillId, signal],
    [ids.outlineId, signal],
    [ids.isobandFillId, signal],
    [ids.isolineId, signal],
    [ids.pointId, signal],
  ] as const;
});

const CLIMATE_FIELD_SIGNAL_BY_GEOMETRY_LAYER = new Map<string, ClimateFieldSignalId>(
  CLIMATE_FIELD_GEOMETRY_ENTRIES
);

/** Every climate geometry layer the shared hover/tap surface may inspect. */
export const CLIMATE_FIELD_GEOMETRY_LAYER_IDS: readonly string[] =
  CLIMATE_FIELD_GEOMETRY_ENTRIES.map(([layerId]) => layerId);

/** Signal drawn by a climate geometry layer, excluding labels and unknown ids. */
export function climateFieldSignalForGeometryLayerId(
  layerId: string
): ClimateFieldSignalId | null {
  return CLIMATE_FIELD_SIGNAL_BY_GEOMETRY_LAYER.get(layerId) ?? null;
}
