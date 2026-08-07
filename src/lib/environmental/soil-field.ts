/**
 * The ERA5-Land soil-field value vocabulary -- volumetric soil water AND soil temperature --
 * shared by the server that builds the isobands, the map that paints them and the panel that
 * legends them, so a colour on the map and a colour in the legend cannot drift apart. See
 * `src/lib/environmental/AGENTS.md` §soil-field.
 */

/** Which physical quantity a soil field draws. */
export type SoilFieldMeasure = "moisture" | "temperature";

/**
 * An ECMWF soil layer. ERA5-Land uses the SAME four layer boundaries for temperature as for
 * volumetric water, so one depth vocabulary serves both measures rather than two that would
 * have to be kept in agreement by hand.
 */
export type SoilFieldDepth = "surface" | "root-zone" | "deep" | "substratum";

/** One depth, its ERA5-Land signal, and how to caption it. */
export interface SoilFieldDepthDefinition {
  depth: SoilFieldDepth;
  /** `agri.signal_observation.signal_name`. */
  signalName: string;
  label: string;
  /** Centimetres below the surface, for the caption only. */
  topCentimetres: number;
  bottomCentimetres: number;
}

/** One drawn band: its interval, the colour that paints it, and the caption for the legend. */
export interface SoilFieldBand {
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

/** Everything that differs between one soil measure and the other. */
export interface SoilFieldMeasureDefinition {
  measure: SoilFieldMeasure;
  /** The `LayerToggleId` this measure renders through. */
  toggleId: "soil-moisture" | "soil-temperature";
  /** The panel switch's label. */
  layerLabel: string;
  /** The legend heading, without the unit. */
  quantityLabel: string;
  /** The field named in running prose ("Loading the <fieldLabel> field..."). */
  fieldLabel: string;
  /**
   * What blank ground on THIS field would otherwise be misread as. Blank means the lane has
   * not filled that cell; captioning it needs the measure's own low-value reading, because
   * "not dry soil" says nothing about a temperature field.
   */
  blankGroundMisreading: string;
  /** `agri.signal_observation.normalized_unit`, exactly as stored. */
  unit: string;
  /** The same unit written for a human; the legend heading uses this one. */
  unitLabel: string;
  depths: readonly SoilFieldDepthDefinition[];
  defaultDepth: SoilFieldDepth;
  /** Interior band edges; the tails below the first and above the last are open. */
  bandBreaks: readonly number[];
  bands: readonly SoilFieldBand[];
}

/** The `support_key` every reading in this lane carries; the grid the values are valid on. */
export const SOIL_FIELD_SUPPORT_KEY = "era5-land-0.1deg";

/** The lane's warehouse source key, for provenance the licence obliges us to publish. */
export const SOIL_FIELD_SOURCE_KEY = "open-meteo-era5-land-archive";

/** Attribution the CC-BY licence on this source requires wherever the values are drawn. */
export const SOIL_FIELD_ATTRIBUTION = "ERA5-Land (Copernicus/ECMWF) via Open-Meteo, CC-BY 4.0";

/**
 * Half a band's width, used to place the open tails at a representative value. Expressed as
 * a fraction of the (uniform) interior band width so one rule covers both measures.
 */
const TAIL_HALF_WIDTH_FRACTION = 0.5;

/**
 * "to" rather than an en dash or a hyphen, because temperature bands are signed: "-5 - 0"
 * asks the reader to tell a range separator from a minus sign, and they cannot.
 */
function formatBandLabel(
  minimum: number | null,
  maximum: number | null,
  formatBound: (value: number) => string
): string {
  if (minimum === null && maximum !== null) return `< ${formatBound(maximum)}`;
  if (maximum === null && minimum !== null) return `>= ${formatBound(minimum)}`;
  return `${formatBound(minimum ?? 0)} to ${formatBound(maximum ?? 0)}`;
}

/** Derives the band table from the breaks, so a break edit cannot desync the legend. */
function buildBands(
  breaks: readonly number[],
  colors: readonly string[],
  formatBound: (value: number) => string
): readonly SoilFieldBand[] {
  const interiorWidth = breaks.length > 1 ? breaks[1] - breaks[0] : 1;
  const tailHalfWidth = interiorWidth * TAIL_HALF_WIDTH_FRACTION;
  return Array.from({ length: breaks.length + 1 }, (_unused, bandIndex): SoilFieldBand => {
    const minimum = bandIndex === 0 ? null : breaks[bandIndex - 1];
    const maximum = bandIndex === breaks.length ? null : breaks[bandIndex];
    const representativeValue =
      minimum === null
        ? (maximum as number) - tailHalfWidth
        : maximum === null
          ? minimum + tailHalfWidth
          : (minimum + maximum) / 2;
    return {
      bandIndex,
      minimum,
      maximum,
      representativeValue,
      color: colors[bandIndex] ?? colors[colors.length - 1],
      label: formatBandLabel(minimum, maximum, formatBound),
    };
  });
}

/**
 * ColorBrewer BrBG, 8 classes: brown is dry, teal is wet. Diverging rather than sequential
 * because the useful reading of soil moisture is "drier or wetter than typical", and its
 * midpoint sits at the lane's own mean.
 */
const MOISTURE_BAND_COLORS: readonly string[] = [
  "#8c510a",
  "#bf812d",
  "#dfc27d",
  "#f6e8c3",
  "#c7eae5",
  "#80cdc1",
  "#35978f",
  "#01665e",
];

/**
 * ColorBrewer RdYlBu, 8 classes, reversed: deep blue is frozen, deep red is hot. Diverging
 * for the same reason moisture's ramp is, and reversed so cold reads cold.
 */
const TEMPERATURE_BAND_COLORS: readonly string[] = [
  "#4575b4",
  "#74add1",
  "#abd9e9",
  "#e0f3f8",
  "#fee090",
  "#fdae61",
  "#f46d43",
  "#d73027",
];

/**
 * Band edges in m^3/m^3.
 *
 * Chosen against the measured spread of the lane rather than a textbook scale: on
 * production 2026-08-05 the three layers span 0.023-0.430, mean ~0.21. Even 0.05 steps put
 * the arid Snake River Plain (0.03-0.07) and the Coast Range (0.32-0.43) in different bands
 * without collapsing the middle of the distribution into one.
 */
const MOISTURE_BAND_BREAKS: readonly number[] = [0.05, 0.1, 0.15, 0.2, 0.25, 0.3, 0.35];

/**
 * Band edges in degrees Celsius.
 *
 * NOT measured against this lane: at the time these were chosen the soil-temperature
 * backfill had written zero rows, so calling them data-driven would be a fabrication. They
 * are anchored physically instead -- 0 C is the freeze boundary, and 5 C steps span the
 * -15..+35 C range daily-mean ERA5-Land soil temperature covers over the PNW across a year.
 * Re-derive them against the realised distribution once the lane is complete, the way
 * moisture's were.
 */
const TEMPERATURE_BAND_BREAKS: readonly number[] = [-5, 0, 5, 10, 15, 20, 25];

/**
 * The two measures this lane publishes.
 *
 * ECMWF also defines a fourth volumetric-water layer (100-289 cm); the Open-Meteo archive
 * lane does not fetch it, so it is not offered here rather than being offered and answering
 * empty. Temperature's fourth layer IS fetched, which is why the two measures declare
 * different depth lists off one shared depth vocabulary.
 */
export const SOIL_FIELD_MEASURES: Readonly<Record<SoilFieldMeasure, SoilFieldMeasureDefinition>> =
  {
    moisture: {
      measure: "moisture",
      toggleId: "soil-moisture",
      layerLabel: "Soil Moisture (ERA5-Land)",
      quantityLabel: "Volumetric soil water",
      fieldLabel: "soil-moisture",
      blankGroundMisreading: "dry soil",
      unit: "m^3/m^3",
      unitLabel: "m³/m³",
      depths: [
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
      ],
      defaultDepth: "surface",
      bandBreaks: MOISTURE_BAND_BREAKS,
      bands: buildBands(MOISTURE_BAND_BREAKS, MOISTURE_BAND_COLORS, (value) => value.toFixed(2)),
    },
    temperature: {
      measure: "temperature",
      toggleId: "soil-temperature",
      layerLabel: "Soil Temperature (ERA5-Land)",
      quantityLabel: "Soil temperature",
      fieldLabel: "soil-temperature",
      blankGroundMisreading: "cold soil",
      unit: "C",
      unitLabel: "°C",
      depths: [
        {
          depth: "surface",
          signalName: "soil_temperature_level_1",
          label: "Surface (0-7 cm)",
          topCentimetres: 0,
          bottomCentimetres: 7,
        },
        {
          depth: "root-zone",
          signalName: "soil_temperature_level_2",
          label: "Root zone (7-28 cm)",
          topCentimetres: 7,
          bottomCentimetres: 28,
        },
        {
          depth: "deep",
          signalName: "soil_temperature_level_3",
          label: "Deep (28-100 cm)",
          topCentimetres: 28,
          bottomCentimetres: 100,
        },
        {
          depth: "substratum",
          signalName: "soil_temperature_level_4",
          label: "Substratum (100-255 cm)",
          topCentimetres: 100,
          bottomCentimetres: 255,
        },
      ],
      defaultDepth: "surface",
      bandBreaks: TEMPERATURE_BAND_BREAKS,
      bands: buildBands(TEMPERATURE_BAND_BREAKS, TEMPERATURE_BAND_COLORS, (value) =>
        value.toFixed(0)
      ),
    },
  };

/** Both measures, in declaration order; what the panel and the layer manager iterate. */
export const SOIL_FIELD_MEASURE_IDS: readonly SoilFieldMeasure[] = ["moisture", "temperature"];

/** The default depth for every measure, as the store seeds itself. */
export const DEFAULT_SOIL_FIELD_DEPTHS: Readonly<Record<SoilFieldMeasure, SoilFieldDepth>> = {
  moisture: SOIL_FIELD_MEASURES.moisture.defaultDepth,
  temperature: SOIL_FIELD_MEASURES.temperature.defaultDepth,
};

/** Every depth key any measure offers, for the wire schema that must accept all of them. */
export const SOIL_FIELD_DEPTHS: readonly SoilFieldDepth[] = [
  "surface",
  "root-zone",
  "deep",
  "substratum",
];

export function soilFieldMeasureDefinition(
  measure: SoilFieldMeasure
): SoilFieldMeasureDefinition {
  return SOIL_FIELD_MEASURES[measure];
}

/** True when the string names a measure this lane publishes. */
export function isSoilFieldMeasure(value: string): value is SoilFieldMeasure {
  return Object.prototype.hasOwnProperty.call(SOIL_FIELD_MEASURES, value);
}

/**
 * The named depth for a measure, falling back to its first. A depth one measure offers and
 * the other does not (`substratum` on moisture) resolves to the fallback rather than
 * throwing, so a stale store value or a replayed cache entry degrades to a drawn field.
 */
export function soilFieldDepthDefinition(
  measure: SoilFieldMeasure,
  depth: SoilFieldDepth
): SoilFieldDepthDefinition {
  const { depths } = SOIL_FIELD_MEASURES[measure];
  return depths.find((candidate) => candidate.depth === depth) ?? depths[0];
}

/** The band a measured value falls in. Total over the reals: the tails are open. */
export function soilFieldBandFor(measure: SoilFieldMeasure, value: number): SoilFieldBand {
  const { bands } = SOIL_FIELD_MEASURES[measure];
  for (const band of bands) {
    if (band.maximum === null || value < band.maximum) return band;
  }
  return bands[bands.length - 1];
}

/**
 * Colour stops for a MapLibre `interpolate` fill expression, flat as
 * `[value, color, value, color, ...]`. Derived from the same band table the legend renders,
 * which is what stops the two from disagreeing.
 */
export function soilFieldColorStops(measure: SoilFieldMeasure): (number | string)[] {
  return SOIL_FIELD_MEASURES[measure].bands.flatMap((band) => [band.representativeValue, band.color]);
}
