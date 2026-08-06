/**
 * The soil-moisture value vocabulary, shared by the server that builds the isobands, the
 * map that paints them and the panel that legends them -- so a colour on the map and a
 * colour in the legend cannot drift apart. See `src/lib/environmental/AGENTS.md`
 * §soil-moisture.
 */

/** The ECMWF layer a reading describes, as the warehouse signal names them. */
export type SoilMoistureDepth = "surface" | "root-zone" | "deep";

/** One depth, its ERA5-Land signal, and how to caption it. */
export interface SoilMoistureDepthDefinition {
  depth: SoilMoistureDepth;
  /** `agri.signal_observation.signal_name`. */
  signalName: string;
  label: string;
  /** Centimetres below the surface, for the caption only. */
  topCentimetres: number;
  bottomCentimetres: number;
}

/**
 * The three ERA5-Land volumetric soil-water layers the lane publishes. ECMWF also defines a
 * fourth (100-289 cm); it is not ingested, so it is not offered here rather than being
 * offered and answering empty.
 */
export const SOIL_MOISTURE_DEPTHS: readonly SoilMoistureDepthDefinition[] = [
  {
    depth: "surface",
    signalName: "soil_water_content_layer_1",
    label: "Surface (0-7 cm)",
    topCentimetres: 0,
    bottomCentimetres: 7,
  },
  {
    depth: "root-zone",
    signalName: "soil_water_content_layer_2",
    label: "Root zone (7-28 cm)",
    topCentimetres: 7,
    bottomCentimetres: 28,
  },
  {
    depth: "deep",
    signalName: "soil_water_content_layer_3",
    label: "Deep (28-100 cm)",
    topCentimetres: 28,
    bottomCentimetres: 100,
  },
];

export const DEFAULT_SOIL_MOISTURE_DEPTH: SoilMoistureDepth = "surface";

/** The `support_key` every reading in this lane carries; the grid the values are valid on. */
export const SOIL_MOISTURE_SUPPORT_KEY = "era5-land-0.1deg";

/** The only unit this layer serves. A reading in anything else is not comparable. */
export const SOIL_MOISTURE_UNIT = "m^3/m^3";

/** The lane's warehouse source key, for provenance the licence obliges us to publish. */
export const SOIL_MOISTURE_SOURCE_KEY = "open-meteo-era5-land-archive";

/** Attribution the CC-BY licence on this source requires wherever the values are drawn. */
export const SOIL_MOISTURE_ATTRIBUTION =
  "ERA5-Land (Copernicus/ECMWF) via Open-Meteo, CC-BY 4.0";

export function soilMoistureDepthDefinition(
  depth: SoilMoistureDepth
): SoilMoistureDepthDefinition {
  const found = SOIL_MOISTURE_DEPTHS.find((candidate) => candidate.depth === depth);
  return found ?? SOIL_MOISTURE_DEPTHS[0];
}

/**
 * Band edges in m^3/m^3.
 *
 * Chosen against the measured spread of the lane rather than a textbook scale: on
 * production 2026-08-05 the three layers span 0.023-0.430, mean ~0.21. Even 0.05 steps put
 * the arid Snake River Plain (0.03-0.07) and the Coast Range (0.32-0.43) in different bands
 * without collapsing the middle of the distribution into one.
 */
export const SOIL_MOISTURE_BAND_BREAKS: readonly number[] = [
  0.05, 0.1, 0.15, 0.2, 0.25, 0.3, 0.35,
];

/** One drawn band: its interval, the colour that paints it, and the caption for the legend. */
export interface SoilMoistureBand {
  bandIndex: number;
  /** Inclusive lower bound; null for the open tail below the first break. */
  minimum: number | null;
  /** Inclusive upper bound; null for the open tail above the last break. */
  maximum: number | null;
  /**
   * The value the fill expression interpolates at for this band. Every served feature
   * carries one so the map has a single paint expression for both zoom tiers.
   */
  representativeValue: number;
  color: string;
  label: string;
}

/**
 * ColorBrewer BrBG, 8 classes: brown is dry, teal is wet. A diverging ramp rather than a
 * sequential one because the useful reading of soil moisture is "drier or wetter than
 * typical", and its midpoint sits at the lane's own mean.
 */
const SOIL_MOISTURE_BAND_COLORS: readonly string[] = [
  "#8c510a",
  "#bf812d",
  "#dfc27d",
  "#f6e8c3",
  "#c7eae5",
  "#80cdc1",
  "#35978f",
  "#01665e",
];

/** Half a band's width, used to place the open tails at a representative value. */
const TAIL_HALF_WIDTH = 0.025;

function formatBandLabel(minimum: number | null, maximum: number | null): string {
  if (minimum === null && maximum !== null) return `< ${maximum.toFixed(2)}`;
  if (maximum === null && minimum !== null) return `>= ${minimum.toFixed(2)}`;
  return `${(minimum ?? 0).toFixed(2)} - ${(maximum ?? 0).toFixed(2)}`;
}

/** The full band table, derived from the breaks so a break edit cannot desync the legend. */
export const SOIL_MOISTURE_BANDS: readonly SoilMoistureBand[] = Array.from(
  { length: SOIL_MOISTURE_BAND_BREAKS.length + 1 },
  (_unused, bandIndex): SoilMoistureBand => {
    const minimum = bandIndex === 0 ? null : SOIL_MOISTURE_BAND_BREAKS[bandIndex - 1];
    const maximum =
      bandIndex === SOIL_MOISTURE_BAND_BREAKS.length ? null : SOIL_MOISTURE_BAND_BREAKS[bandIndex];
    const representativeValue =
      minimum === null
        ? (maximum as number) - TAIL_HALF_WIDTH
        : maximum === null
          ? minimum + TAIL_HALF_WIDTH
          : (minimum + maximum) / 2;
    return {
      bandIndex,
      minimum,
      maximum,
      representativeValue,
      color: SOIL_MOISTURE_BAND_COLORS[bandIndex] ?? SOIL_MOISTURE_BAND_COLORS.at(-1)!,
      label: formatBandLabel(minimum, maximum),
    };
  }
);

/** The band a measured value falls in. Total over the reals: the tails are open. */
export function soilMoistureBandFor(value: number): SoilMoistureBand {
  for (const band of SOIL_MOISTURE_BANDS) {
    if (band.maximum === null || value < band.maximum) return band;
  }
  return SOIL_MOISTURE_BANDS[SOIL_MOISTURE_BANDS.length - 1];
}

/**
 * Colour stops for a MapLibre `interpolate` fill expression, flat as
 * `[value, color, value, color, ...]`. Derived from the same band table the legend renders,
 * which is what stops the two from disagreeing.
 */
export function soilMoistureColorStops(): (number | string)[] {
  return SOIL_MOISTURE_BANDS.flatMap((band) => [band.representativeValue, band.color]);
}
