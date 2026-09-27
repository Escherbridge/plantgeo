import type { SoilProperties } from "./soilgrids";

/** Pair SoilGrids model estimates with units and their basis label; see soil/AGENTS.md §soil-ai-evidence. */
export function soilAiEvidence(soil: SoilProperties | null) {
  if (soil === null) return null;
  return {
    depth: "0–5 cm",
    method: "SoilGrids model predictions; not a local soil sample",
    basis: "model_estimate" as const,
    label: soil.label ?? "SoilGrids v2.0 250 m model estimate, 0-5 cm",
    ...(soil.distanceM !== undefined ? { cellCentreDistanceM: soil.distanceM } : {}),
    ...(soil.releaseId !== undefined ? { releaseId: soil.releaseId } : {}),
    ph: { value: soil.ph, unit: "pH (water), dimensionless" },
    organicCarbon: { value: soil.organicCarbon, unit: "g/kg" },
    nitrogen: { value: soil.nitrogen, unit: "g/kg" },
    bulkDensity: { value: soil.bulkDensity, unit: "g/cm³" },
    cec: { value: soil.cec, unit: "cmol(c)/kg" },
    ocd: { value: soil.ocd, unit: "kg/m³" },
    ...(soil.topsoil !== undefined ? {
      topsoil0to30cm: {
        label: soil.topsoil.label,
        texture: soil.topsoil.texture_class,
        reaction: soil.topsoil.reaction_class,
        organicCarbonBand: soil.topsoil.soc_band,
        ph: { value: soil.topsoil.ph, unit: "pH (water), dimensionless" },
        organicCarbon: { value: soil.topsoil.soc_pct, unit: "% (mass)" },
        clay: { value: soil.topsoil.clay_pct, unit: "% (mass)" },
        sand: { value: soil.topsoil.sand_pct, unit: "% (mass)" },
        silt: { value: soil.topsoil.silt_pct, unit: "% (mass)" },
      },
    } : {}),
  };
}
