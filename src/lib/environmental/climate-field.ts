/**
 * The NASA POWER climate-field value vocabulary -- daily meteorology plus the pilot
 * soil-wetness signals -- shared by the server that reads the cells, the map that paints
 * them and the panel that legends them, so a colour on the map and a colour in the legend
 * cannot drift apart. See `src/lib/environmental/AGENTS.md` §climate-field.
 *
 * The wire never carries a raw `signal_name`: the client sends a `ClimateFieldSignalId` and
 * the server resolves it here, which is what keeps the warehouse's naming out of the browser
 * and out of the tRPC schema.
 */

/** Which quantity a climate field draws. One is selected at a time. */
export type ClimateFieldSignalId =
  | "air-temperature"
  | "dew-point"
  | "precipitation"
  | "relative-humidity"
  | "shortwave-radiation"
  | "wind-speed"
  | "soil-wetness-surface"
  | "soil-wetness-root-zone"
  | "soil-wetness-profile";

/**
 * Which daily statistic the air-temperature field draws.
 *
 * The one signal NASA POWER publishes three ways over the same cells and days. A VARIANT of
 * one quantity rather than three signals, because "mean, max or min" is the same question
 * asked three ways and a reader wants one answer at a time -- three ids would put three
 * near-identical rows in the picker and three entries in the legend.
 */
export type AirTemperatureVariant = "mean" | "max" | "min";

/** One drawn band: its interval, the colour that paints it, and the caption for the legend. */
export interface ClimateFieldBand {
  bandIndex: number;
  /** Inclusive lower bound; null for the open tail below the first break. */
  minimum: number | null;
  /** Exclusive upper bound; null for the open tail above the last break. */
  maximum: number | null;
  /**
   * The value the fill expression interpolates at for this band. The open tails take the
   * signal's own domain bound rather than a half-band overshoot, so a bounded quantity --
   * precipitation, relative humidity, a saturation fraction -- never gets a stop outside
   * the values it can hold.
   */
  representativeValue: number;
  color: string;
  label: string;
}

/** One air-temperature statistic and the warehouse signal that carries it. */
export interface AirTemperatureVariantDefinition {
  variant: AirTemperatureVariant;
  /** `agri.signal_observation.signal_name`. */
  signalName: string;
  label: string;
}

/** Everything that differs between one climate signal and the next. */
export interface ClimateFieldSignalDefinition {
  signal: ClimateFieldSignalId;
  /** The picker's caption. */
  label: string;
  /** The legend heading, without the unit. */
  quantityLabel: string;
  /** The field named in running prose ("Loading the <fieldLabel> field..."). */
  fieldLabel: string;
  /**
   * `agri.signal_observation.signal_name`, or null for the one signal whose variants name it.
   * Resolve through `climateFieldSignalName` rather than reading this directly.
   */
  signalName: string | null;
  /** Empty except on `air-temperature`; the statistic picker renders from this. */
  variants: readonly AirTemperatureVariantDefinition[];
  /** `agri.signal_observation.normalized_unit`, exactly as stored. */
  unit: string;
  /** The same unit written for a human; the legend heading uses this one. */
  unitLabel: string;
  /**
   * What blank ground on THIS field would otherwise be misread as. Blank means the lane has
   * not filled that cell, and captioning it needs the signal's own low-value reading.
   */
  blankGroundMisreading: string;
  /**
   * Why this signal's coverage is narrower than the lattice, or null when it is the whole
   * lattice. Published so a near-empty map reads as a pilot rather than as an outage.
   */
  coverageNote: string | null;
  /** Lowest value the ramp's bottom tail is drawn at. */
  domainMinimum: number;
  /** Highest value the ramp's top tail is drawn at. */
  domainMaximum: number;
  /** Interior band edges; the tails below the first and above the last are open. */
  bandBreaks: readonly number[];
  bands: readonly ClimateFieldBand[];
}

/** The `support_key` every reading in this lane carries. */
export const CLIMATE_FIELD_SUPPORT_KEY = "surface";

/** `agri.spatial_cell.grid_name` for the 397 half-degree cells this lane is valid on. */
export const CLIMATE_FIELD_GRID_NAME = "nasa-power-0.5-degree";

/** The lane's warehouse source key, for provenance the licence obliges us to publish. */
export const CLIMATE_FIELD_SOURCE_KEY = "nasa-power-daily";

/** Attribution published wherever these values are drawn. */
export const CLIMATE_FIELD_ATTRIBUTION = "NASA POWER (NASA LaRC)";

/** The signal the store seeds itself with. */
export const DEFAULT_CLIMATE_FIELD_SIGNAL: ClimateFieldSignalId = "air-temperature";

/** The air-temperature statistic the store seeds itself with. */
export const DEFAULT_AIR_TEMPERATURE_VARIANT: AirTemperatureVariant = "mean";

/**
 * "to" rather than an en dash or a hyphen, because temperature bands are signed: "-5 - 0"
 * asks the reader to tell a range separator from a minus sign, and they cannot. The same
 * rule `soil-field.ts` follows, for the same reason.
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

/** What `buildBands` needs that the band table itself does not carry. */
interface BandTableInput {
  breaks: readonly number[];
  colors: readonly string[];
  domainMinimum: number;
  domainMaximum: number;
  formatBound: (value: number) => string;
}

/**
 * Derives the band table from the breaks, so a break edit cannot desync the legend.
 *
 * Diverges from `soil-field.ts`'s builder in one place, and deliberately: the tails take the
 * signal's DOMAIN bound instead of a half-interior-width overshoot. Every soil measure has
 * uniform interior bands, so the overshoot is well defined there; precipitation's are
 * 0.9/1.5/2.5/5/15 wide, and a uniform overshoot off the first pair would place the bottom
 * stop at -0.35 mm/day -- a negative rainfall the interpolation would then blend towards on
 * every dry cell.
 */
function buildBands({
  breaks,
  colors,
  domainMinimum,
  domainMaximum,
  formatBound,
}: BandTableInput): readonly ClimateFieldBand[] {
  return Array.from({ length: breaks.length + 1 }, (_unused, bandIndex): ClimateFieldBand => {
    const minimum = bandIndex === 0 ? null : breaks[bandIndex - 1];
    const maximum = bandIndex === breaks.length ? null : breaks[bandIndex];
    const representativeValue =
      minimum === null
        ? domainMinimum
        : maximum === null
          ? domainMaximum
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
 * ColorBrewer RdBu, 11 classes, reversed: deep blue is freezing, deep red is a heat event,
 * near-white is the lane's own middle. Diverging because the useful reading of an air
 * temperature is "warmer or colder than temperate", and the -20..40 C domain's midpoint (10-15
 * C) is where the white class lands.
 */
const AIR_TEMPERATURE_BAND_COLORS: readonly string[] = [
  "#053061",
  "#2166ac",
  "#4393c3",
  "#92c5de",
  "#d1e5f0",
  "#f7f7f7",
  "#fddbc7",
  "#f4a582",
  "#d6604d",
  "#b2182b",
  "#67001f",
];

/**
 * Band edges in degrees Celsius, 5 C apart across the measured body of the lane.
 *
 * The -20..40 C domain is the measured p02-p98 of `air_temperature_mean` over the lane's
 * 397 cells; the open tails carry the rest. Five-degree steps keep a coastal winter (2-8 C)
 * and a Columbia Basin July afternoon (30-38 C) many bands apart without collapsing the
 * temperate middle where most cell-days sit.
 */
const AIR_TEMPERATURE_BAND_BREAKS: readonly number[] = [
  -10, -5, 0, 5, 10, 15, 20, 25, 30, 35,
];

/**
 * ColorBrewer PuBu, 9 classes: near-white is desiccated air, deep blue is saturated. Cool and
 * SEQUENTIAL where air temperature is diverging -- a dew point has no "typical" midpoint to
 * diverge around; the reading is monotone, "how much water is the air holding".
 */
const DEW_POINT_BAND_COLORS: readonly string[] = [
  "#fff7fb",
  "#ece7f2",
  "#d0d1e6",
  "#a6bddb",
  "#74a9cf",
  "#3690c0",
  "#0570b0",
  "#045a8d",
  "#023858",
];

/** Band edges in degrees Celsius; the -20..25 C domain is the lane's measured p02-p98. */
const DEW_POINT_BAND_BREAKS: readonly number[] = [-15, -10, -5, 0, 5, 10, 15, 20];

/**
 * ColorBrewer Blues, 7 classes, with an almost-white first: a dry day must not read as a
 * light shower. Sequential because precipitation has a true zero and no midpoint.
 */
const PRECIPITATION_BAND_COLORS: readonly string[] = [
  "#f7fbff",
  "#deebf7",
  "#c6dbef",
  "#9ecae1",
  "#6baed6",
  "#3182bd",
  "#08519c",
];

/**
 * Band edges in mm/day, SKEWED rather than uniform.
 *
 * Daily precipitation over this lattice is dominated by zeros and trace amounts -- the
 * measured p98 is ~25 mm/day while the median is under 1 -- so uniform steps would put almost
 * every cell in one band and leave six empty. The first break at 0.1 separates "no rain" from
 * "trace", and the widths double from there so a 30 mm frontal day is still distinguishable
 * from a 6 mm drizzle.
 */
const PRECIPITATION_BAND_BREAKS: readonly number[] = [0.1, 1, 2.5, 5, 10, 25];

/**
 * ColorBrewer PuBuGn, 9 classes: pale is arid air, deep teal is saturated. Sequential over a
 * bounded percentage, which has neither a true midpoint nor tails worth an open class.
 */
const RELATIVE_HUMIDITY_BAND_COLORS: readonly string[] = [
  "#fff7fb",
  "#ece2f0",
  "#d0d1e6",
  "#a6bddb",
  "#67a9cf",
  "#3690c0",
  "#02818a",
  "#016c59",
  "#014636",
];

/** Band edges in percent. The quantity is bounded 0-100, so the domain is the bound. */
const RELATIVE_HUMIDITY_BAND_BREAKS: readonly number[] = [20, 30, 40, 50, 60, 70, 80, 90];

/**
 * ColorBrewer YlOrBr, 8 classes: pale yellow is an overcast winter day, deep brown is a
 * cloudless midsummer one. Sequential -- insolation has a true zero and no typical middle.
 */
const SHORTWAVE_RADIATION_BAND_COLORS: readonly string[] = [
  "#ffffe5",
  "#fff7bc",
  "#fee391",
  "#fec44f",
  "#fe9929",
  "#ec7014",
  "#cc4c02",
  "#8c2d04",
];

/**
 * Band edges in MJ/m^2/day. The 0-32 domain is the lane's measured p02-p98 and is close to
 * the physical ceiling at this latitude, so 4 MJ steps span the whole seasonal swing.
 */
const SHORTWAVE_RADIATION_BAND_BREAKS: readonly number[] = [4, 8, 12, 16, 20, 24, 28];

/**
 * ColorBrewer BuPu, 9 classes: pale is calm, deep purple is a wind event. Sequential, and
 * deliberately NOT the warm ramp `WeatherLayer` uses for station wind -- that one classifies
 * gusts at a point, this one is a daily mean over a 0.5-degree cell, and giving them the same
 * palette would invite reading one as the other.
 */
const WIND_SPEED_BAND_COLORS: readonly string[] = [
  "#f7fcfd",
  "#e0ecf4",
  "#bfd3e6",
  "#9ebcda",
  "#8c96c6",
  "#8c6bb1",
  "#88419d",
  "#810f7c",
  "#4d004b",
];

/** Band edges in m/s; the 0-10 domain is the lane's measured p02-p98 for a daily mean. */
const WIND_SPEED_BAND_BREAKS: readonly number[] = [1, 2, 3, 4, 5, 6, 7, 8];

/**
 * ColorBrewer BrBG, 9 classes: brown is dry, teal is saturated. The same palette family the
 * ERA5-Land moisture field uses, on purpose -- the two answer the same physical question on
 * two lattices, and giving them different ramps would make a reader compare colours that
 * mean the same thing.
 */
const SOIL_WETNESS_BAND_COLORS: readonly string[] = [
  "#8c510a",
  "#bf812d",
  "#dfc27d",
  "#f6e8c3",
  "#f5f5f5",
  "#c7eae5",
  "#80cdc1",
  "#35978f",
  "#01665e",
];

/** Band edges as a fraction of saturation. The quantity is bounded 0-1 by construction. */
const SOIL_WETNESS_BAND_BREAKS: readonly number[] = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8];

/** The three air-temperature statistics, in the order the picker renders them. */
export const AIR_TEMPERATURE_VARIANTS: readonly AirTemperatureVariantDefinition[] = [
  { variant: "mean", signalName: "air_temperature_mean", label: "Daily mean" },
  { variant: "max", signalName: "air_temperature_max", label: "Daily max" },
  { variant: "min", signalName: "air_temperature_min", label: "Daily min" },
];

/** Every air-temperature statistic, for the wire schema that must accept all of them. */
export const AIR_TEMPERATURE_VARIANT_IDS: readonly AirTemperatureVariant[] = [
  "mean",
  "max",
  "min",
];

/**
 * The coverage caveat the three soil-wetness signals carry.
 *
 * Measured on production 2026-08-08: 4 cells against the lane's 397, ~5.8 k rows each against
 * ~586 k. They are offered anyway, because a reader who cannot select a signal cannot be told
 * it is a pilot -- and four honestly-captioned cells are a better answer than a hidden lane.
 */
const SOIL_WETNESS_COVERAGE_NOTE =
  "Pilot coverage: 4 of the lane's 397 cells carry this signal, so most of the map is " +
  "correctly blank.";

/** The nine quantities this lane publishes, in the order the picker renders them. */
export const CLIMATE_FIELD_SIGNALS: Readonly<
  Record<ClimateFieldSignalId, ClimateFieldSignalDefinition>
> = {
  "air-temperature": {
    signal: "air-temperature",
    label: "Air temperature",
    quantityLabel: "Air temperature",
    fieldLabel: "air-temperature",
    // Null: the variant names the signal. The only entry where that is true.
    signalName: null,
    variants: AIR_TEMPERATURE_VARIANTS,
    unit: "C",
    unitLabel: "°C",
    blankGroundMisreading: "cold air",
    coverageNote: null,
    domainMinimum: -20,
    domainMaximum: 40,
    bandBreaks: AIR_TEMPERATURE_BAND_BREAKS,
    bands: buildBands({
      breaks: AIR_TEMPERATURE_BAND_BREAKS,
      colors: AIR_TEMPERATURE_BAND_COLORS,
      domainMinimum: -20,
      domainMaximum: 40,
      formatBound: (value) => value.toFixed(0),
    }),
  },
  "dew-point": {
    signal: "dew-point",
    label: "Dew point",
    quantityLabel: "Dew point temperature",
    fieldLabel: "dew-point",
    signalName: "dew_point_temperature",
    variants: [],
    unit: "C",
    unitLabel: "°C",
    blankGroundMisreading: "dry air",
    coverageNote: null,
    domainMinimum: -20,
    domainMaximum: 25,
    bandBreaks: DEW_POINT_BAND_BREAKS,
    bands: buildBands({
      breaks: DEW_POINT_BAND_BREAKS,
      colors: DEW_POINT_BAND_COLORS,
      domainMinimum: -20,
      domainMaximum: 25,
      formatBound: (value) => value.toFixed(0),
    }),
  },
  precipitation: {
    signal: "precipitation",
    label: "Precipitation",
    quantityLabel: "Precipitation",
    fieldLabel: "precipitation",
    signalName: "precipitation",
    variants: [],
    unit: "mm/day",
    unitLabel: "mm/day",
    blankGroundMisreading: "a dry day",
    coverageNote: null,
    domainMinimum: 0,
    domainMaximum: 40,
    bandBreaks: PRECIPITATION_BAND_BREAKS,
    bands: buildBands({
      breaks: PRECIPITATION_BAND_BREAKS,
      colors: PRECIPITATION_BAND_COLORS,
      domainMinimum: 0,
      domainMaximum: 40,
      // The break's own decimal form, not a fixed precision: these edges are skewed, so
      // `toFixed(0)` would caption 2.5 as "3" and `toFixed(1)` would caption 25 as "25.0".
      formatBound: (value) => String(value),
    }),
  },
  "relative-humidity": {
    signal: "relative-humidity",
    label: "Relative humidity",
    quantityLabel: "Relative humidity",
    fieldLabel: "relative-humidity",
    signalName: "relative_humidity",
    variants: [],
    unit: "%",
    unitLabel: "%",
    blankGroundMisreading: "dry air",
    coverageNote: null,
    domainMinimum: 0,
    domainMaximum: 100,
    bandBreaks: RELATIVE_HUMIDITY_BAND_BREAKS,
    bands: buildBands({
      breaks: RELATIVE_HUMIDITY_BAND_BREAKS,
      colors: RELATIVE_HUMIDITY_BAND_COLORS,
      domainMinimum: 0,
      domainMaximum: 100,
      formatBound: (value) => value.toFixed(0),
    }),
  },
  "shortwave-radiation": {
    signal: "shortwave-radiation",
    label: "Solar radiation",
    quantityLabel: "Surface shortwave radiation",
    fieldLabel: "solar-radiation",
    signalName: "surface_shortwave_radiation",
    variants: [],
    unit: "MJ/m^2/day",
    unitLabel: "MJ/m²/day",
    blankGroundMisreading: "an overcast day",
    coverageNote: null,
    domainMinimum: 0,
    domainMaximum: 32,
    bandBreaks: SHORTWAVE_RADIATION_BAND_BREAKS,
    bands: buildBands({
      breaks: SHORTWAVE_RADIATION_BAND_BREAKS,
      colors: SHORTWAVE_RADIATION_BAND_COLORS,
      domainMinimum: 0,
      domainMaximum: 32,
      formatBound: (value) => value.toFixed(0),
    }),
  },
  "wind-speed": {
    signal: "wind-speed",
    label: "Wind speed",
    quantityLabel: "Wind speed",
    fieldLabel: "wind-speed",
    signalName: "wind_speed",
    variants: [],
    unit: "m/s",
    unitLabel: "m/s",
    blankGroundMisreading: "still air",
    coverageNote: null,
    domainMinimum: 0,
    domainMaximum: 10,
    bandBreaks: WIND_SPEED_BAND_BREAKS,
    bands: buildBands({
      breaks: WIND_SPEED_BAND_BREAKS,
      colors: WIND_SPEED_BAND_COLORS,
      domainMinimum: 0,
      domainMaximum: 10,
      formatBound: (value) => value.toFixed(0),
    }),
  },
  "soil-wetness-surface": {
    signal: "soil-wetness-surface",
    label: "Soil wetness (surface) — pilot",
    quantityLabel: "Surface soil wetness",
    fieldLabel: "surface soil-wetness",
    signalName: "soil_wetness_surface",
    variants: [],
    unit: "fraction_of_saturation",
    unitLabel: "fraction of saturation",
    blankGroundMisreading: "dry soil",
    coverageNote: SOIL_WETNESS_COVERAGE_NOTE,
    domainMinimum: 0,
    domainMaximum: 1,
    bandBreaks: SOIL_WETNESS_BAND_BREAKS,
    bands: buildBands({
      breaks: SOIL_WETNESS_BAND_BREAKS,
      colors: SOIL_WETNESS_BAND_COLORS,
      domainMinimum: 0,
      domainMaximum: 1,
      formatBound: (value) => value.toFixed(2),
    }),
  },
  "soil-wetness-root-zone": {
    signal: "soil-wetness-root-zone",
    label: "Soil wetness (root zone) — pilot",
    quantityLabel: "Root-zone soil wetness",
    fieldLabel: "root-zone soil-wetness",
    signalName: "soil_wetness_root_zone",
    variants: [],
    unit: "fraction_of_saturation",
    unitLabel: "fraction of saturation",
    blankGroundMisreading: "dry soil",
    coverageNote: SOIL_WETNESS_COVERAGE_NOTE,
    domainMinimum: 0,
    domainMaximum: 1,
    bandBreaks: SOIL_WETNESS_BAND_BREAKS,
    bands: buildBands({
      breaks: SOIL_WETNESS_BAND_BREAKS,
      colors: SOIL_WETNESS_BAND_COLORS,
      domainMinimum: 0,
      domainMaximum: 1,
      formatBound: (value) => value.toFixed(2),
    }),
  },
  "soil-wetness-profile": {
    signal: "soil-wetness-profile",
    label: "Soil wetness (profile) — pilot",
    quantityLabel: "Profile soil wetness",
    fieldLabel: "profile soil-wetness",
    signalName: "soil_wetness_profile",
    variants: [],
    unit: "fraction_of_saturation",
    unitLabel: "fraction of saturation",
    blankGroundMisreading: "dry soil",
    coverageNote: SOIL_WETNESS_COVERAGE_NOTE,
    domainMinimum: 0,
    domainMaximum: 1,
    bandBreaks: SOIL_WETNESS_BAND_BREAKS,
    bands: buildBands({
      breaks: SOIL_WETNESS_BAND_BREAKS,
      colors: SOIL_WETNESS_BAND_COLORS,
      domainMinimum: 0,
      domainMaximum: 1,
      formatBound: (value) => value.toFixed(2),
    }),
  },
};

/**
 * Every signal, in declaration order; what the panel and the wire schema iterate.
 *
 * DERIVED from the table above rather than hand-listed. A third copy of the nine ids -- after
 * the union and the record -- is a place a tenth signal can be silently omitted from the
 * picker and from the tRPC enum while the record that defines it looks complete. The record is
 * exhaustive over the union by its own type, so `Object.keys` cannot under-report it, and
 * JavaScript preserves the literal's insertion order for string keys.
 */
export const CLIMATE_FIELD_SIGNAL_IDS: readonly ClimateFieldSignalId[] = Object.keys(
  CLIMATE_FIELD_SIGNALS
) as ClimateFieldSignalId[];

export function climateFieldSignalDefinition(
  signal: ClimateFieldSignalId
): ClimateFieldSignalDefinition {
  return CLIMATE_FIELD_SIGNALS[signal];
}

/** True when the string names a signal this lane publishes. */
export function isClimateFieldSignal(value: string): value is ClimateFieldSignalId {
  return Object.prototype.hasOwnProperty.call(CLIMATE_FIELD_SIGNALS, value);
}

/**
 * The warehouse `signal_name` behind a selected signal.
 *
 * A variant that the signal does not offer -- anything but `air-temperature` today -- is
 * ignored rather than rejected, so a stale store value or a replayed cache entry degrades to
 * a drawn field instead of a query for a signal that cannot exist.
 */
export function climateFieldSignalName(
  signal: ClimateFieldSignalId,
  variant: AirTemperatureVariant = DEFAULT_AIR_TEMPERATURE_VARIANT
): string {
  const definition = CLIMATE_FIELD_SIGNALS[signal];
  if (definition.signalName !== null) return definition.signalName;
  const selected = definition.variants.find((candidate) => candidate.variant === variant);
  return (selected ?? definition.variants[0]).signalName;
}

/** The band a measured value falls in. Total over the reals: the tails are open. */
export function climateFieldBandFor(
  signal: ClimateFieldSignalId,
  value: number
): ClimateFieldBand {
  const { bands } = CLIMATE_FIELD_SIGNALS[signal];
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
export function climateFieldColorStops(signal: ClimateFieldSignalId): (number | string)[] {
  return CLIMATE_FIELD_SIGNALS[signal].bands.flatMap((band) => [
    band.representativeValue,
    band.color,
  ]);
}
