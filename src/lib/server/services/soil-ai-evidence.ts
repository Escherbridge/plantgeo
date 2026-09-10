import type { SoilProperties } from "./soilgrids";

/** Pair normalized SoilGrids values with units; see AGENTS.md §soil-ai-evidence. */
export function soilAiEvidence(soil: SoilProperties | null) {
  if (soil === null) return null;
  return {
    depth: "0–5 cm",
    method: "SoilGrids model predictions; not a local soil sample",
    ph: { value: soil.ph, unit: "pH (water), dimensionless" },
    organicCarbon: { value: soil.organicCarbon, unit: "g/kg" },
    nitrogen: { value: soil.nitrogen, unit: "g/kg" },
    bulkDensity: { value: soil.bulkDensity, unit: "g/cm³" },
    cec: { value: soil.cec, unit: "cmol(c)/kg" },
    ocd: { value: soil.ocd, unit: "kg/m³" },
  };
}
