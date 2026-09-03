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

/**
 * The registry toggle each signal owns, derived from the signal id so the two cannot drift.
 *
 * Nine toggles since 2026-08-10, where there was one until then. The old single `climate-field`
 * toggle carried a signal picker inside the Climate panel, and the cost was not cosmetic: the
 * slider capability for the whole lane was computed over ALL eleven `signal_name`s unioned, so
 * the four-cell soil-wetness pilot was offered the same axis, the same `latestObservedDate` and
 * the same gap list as the 397-cell air-temperature field. A row per signal is what gives each
 * signal its own honest axis -- see `SLIDER_STREAM_LAYER_NAMES` in src/types/time-slider.ts.
 */
export type ClimateFieldToggleId = `climate-${ClimateFieldSignalId}`;

/**
 * How one signal is painted. The reason nine climate rows can be on at once and still compose.
 *
 * Nine filled fields over the same 397 cells is one visible field and eight invisible ones: the
 * topmost opaque fill wins, and the category counter reads "3 of 9" while the map shows one. So
 * a row picks a FORM as well as a day, and the forms are chosen to layer over each other -- a
 * filled base with contours across it -- the way a printed weather chart composes a temperature
 * wash and its isotherms.
 *
 * `symbol` IS STILL IN THIS UNION AND IS OFFERED BY NO SIGNAL. It was the third layer of that
 * composite, one dot per measured cell, and `LAYER_RENDER_CONTRACT` permits no point form for a
 * `continuous_field` at any band: a dot's radius says nothing about the ground the value
 * describes, which is the fictitious footprint the contract exists to forbid. Withdrawing it from
 * every `renderForms` list is what stops it being CHOSEN; keeping it in the union is what lets a
 * stale store entry, a replayed cache and a legacy server answer still be NAMED, so
 * `resolveClimateRenderForm` can map them onto `field` instead of failing to type-check against a
 * value that demonstrably exists in the wild.
 *
 * Not every form is honest for every signal either, which is why the per-signal `renderForms`
 * list exists rather than this union being offered everywhere. See the note on that field.
 */
export type ClimateRenderForm = "field" | "isoline" | "symbol";

/** Every form, for the wire schema and the exhaustiveness checks that must accept all of them. */
export const CLIMATE_RENDER_FORMS: readonly ClimateRenderForm[] = [
  "field",
  "isoline",
  "symbol",
];

/** The reader-facing caption for each form, so a row's control and its legend agree. */
export const CLIMATE_RENDER_FORM_LABELS: Readonly<Record<ClimateRenderForm, string>> = {
  field: "Filled",
  isoline: "Contours",
  symbol: "Points",
};

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
  /** The registry toggle that switches this signal, derived rather than typed. */
  toggleId: ClimateFieldToggleId;
  /** The slider stream whose axis this signal scrubs; its own, never the lane's. */
  streamName: string;
  /**
   * The forms this signal may be drawn in, most appropriate FIRST -- the head is the default
   * the row opens on.
   *
   * A list rather than the bare `ClimateRenderForm` union because two forms are affirmatively
   * dishonest on some signals and are withheld there rather than offered and captioned:
   *
   *   - `isoline` is absent from `precipitation`. A contour asserts the field varies smoothly
   *     between the samples it is drawn through, and daily rainfall does not: a convective cell
   *     wets one 55 km square and leaves its neighbour dry, so an isopleth across the pair
   *     draws a gradient where the record holds a discontinuity.
   *   - `isoline` is absent from all three soil-wetness signals for the harder version of the
   *     same problem. They are a PILOT on part of the lattice, and contouring scattered samples
   *     interpolates a continuous field across ground the lane never measured -- the exact
   *     fabrication `blankGroundMisreading` and the coverage note exist to prevent.
   *   - `symbol` is absent from ALL NINE since 2026-09-02, and for a different kind of reason:
   *     not that it is dishonest about this signal, but that the frozen render contract permits
   *     no point form for a continuous field at any band. See `ClimateRenderForm`.
   *
   * `field` is honest everywhere: it draws one cell per measured sample and puts nothing at all
   * on a cell the lane has not filled. Six of the nine therefore offer exactly one form and draw
   * no picker at all.
   */
  renderForms: readonly ClimateRenderForm[];
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
   *
   * Carries NO COUNTS, deliberately, and a count must never be added back. Until 2026-08-10 it
   * read "Pilot coverage: 4 of the lane's 397 cells carry this signal" -- a figure measured
   * against production on 2026-08-08 and frozen into the bundle. The pilot then grew, and the
   * panel rendered the frozen sentence directly above the live one, so a reader saw "4 of the
   * lane's 397 cells" and "267 measured 0.5 degree cells drawn for 2026-08-06" in the same
   * card. A constant cannot track a growing lane; the numbers belong to the served collection,
   * which measures them per request. This states the KIND of coverage, and
   * `ClimateDetails` composes the counted sentence from `cellCount` and `latticeCellCount`.
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

/* The three soil-wetness signals carried a `SOIL_WETNESS_COVERAGE_NOTE` and a "— pilot" label
   suffix until 2026-08-10. The pilot is over: measured against production, the lane is 4 cells
   for its first 98 days (2022-04-30..2022-08-05) and the full 397 for the 1,462 days since
   (2022-08-06..2026-08-06) -- the same width as air temperature on every one of those days. So
   the caption described 6% of the record and mis-described the rest, which is the failure the
   `coverageNote` contract calls out: it is `null` when coverage IS the whole lattice. The early
   98 days still need no static sentence, because `ClimateDetails` composes the counted one per
   request from `cellCount`/`latticeCellCount` -- a reader sitting on 2022-05-01 is told "4 of
   397" by the served collection itself, which is exactly what that mechanism exists for. */

/**
 * One signal's entry as it is WRITTEN, without the two fields that are derived from its id.
 *
 * `toggleId` and `streamName` are omitted here and added by the builder below, for the reason
 * `CLIMATE_FIELD_SIGNAL_IDS` is derived rather than hand-listed: a hand-typed
 * `toggleId: "climate-dew-point"` beside `signal: "dew-point"` is a place the two can disagree,
 * and a toggle id that does not match its signal resolves to a row that renders nothing while
 * every type still checks.
 */
type ClimateFieldSignalTableEntry = Omit<
  ClimateFieldSignalDefinition,
  "toggleId" | "streamName"
>;

/** The nine quantities this lane publishes, in the order the dock renders their rows. */
const CLIMATE_FIELD_SIGNAL_TABLE: Readonly<
  Record<ClimateFieldSignalId, ClimateFieldSignalTableEntry>
> = {
  "air-temperature": {
    signal: "air-temperature",
    // The lane's natural base wash: the one signal a reader almost always wants painted under
    // whatever else is on, and the only one that defaults to `field` for that reason.
    renderForms: ["field", "isoline"],
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
    renderForms: ["isoline", "field"],
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
    // No `isoline`, for the reason on `renderForms` in the interface above. `symbol` was this
    // signal's DEFAULT until 2026-09-02 -- a graduated drop over another signal's wash is the
    // classic composite -- and it went with the rest of the point forms when the frozen render
    // contract turned out to permit none for a continuous field. Nothing is left to pick between,
    // so the row draws no form picker at all.
    renderForms: ["field"],
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
    renderForms: ["isoline", "field"],
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
    renderForms: ["isoline", "field"],
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
    // No barb and no arrow, and none can be added from this lane: the NASA POWER backfill
    // requests eight parameters -- ALLSKY_SFC_SW_DWN, PRECTOTCORR, RH2M, T2M, T2MDEW, T2M_MAX,
    // T2M_MIN, WS2M -- and WD2M is not among them, so the warehouse holds a daily mean SPEED
    // with no bearing anywhere. A barb drawn without a direction would point somewhere, and
    // wherever it pointed would be invented. A speed is a scalar here, and it is drawn as one.
    renderForms: ["isoline", "field"],
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
    // No `isoline` on any of the three: see `renderForms` on the interface. Contouring a pilot
    // interpolates a continuous field across ground the lane has never measured.
    renderForms: ["field"],
    label: "Soil wetness (surface)",
    quantityLabel: "Surface soil wetness",
    fieldLabel: "surface soil-wetness",
    signalName: "soil_wetness_surface",
    variants: [],
    unit: "fraction_of_saturation",
    unitLabel: "fraction of saturation",
    blankGroundMisreading: "dry soil",
    coverageNote: null,
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
    renderForms: ["field"],
    label: "Soil wetness (root zone)",
    quantityLabel: "Root-zone soil wetness",
    fieldLabel: "root-zone soil-wetness",
    signalName: "soil_wetness_root_zone",
    variants: [],
    unit: "fraction_of_saturation",
    unitLabel: "fraction of saturation",
    blankGroundMisreading: "dry soil",
    coverageNote: null,
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
    renderForms: ["field"],
    label: "Soil wetness (profile)",
    quantityLabel: "Profile soil wetness",
    fieldLabel: "profile soil-wetness",
    signalName: "soil_wetness_profile",
    variants: [],
    unit: "fraction_of_saturation",
    unitLabel: "fraction of saturation",
    blankGroundMisreading: "dry soil",
    coverageNote: null,
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

/** The registry toggle that switches one signal. The only place this string is formed. */
export function climateFieldToggleId(signal: ClimateFieldSignalId): ClimateFieldToggleId {
  return `climate-${signal}`;
}

/**
 * The slider stream one signal scrubs. The only place this string is formed.
 *
 * Prefixed `climate-field-` rather than reusing the toggle id, following the precedent the
 * ERA5-Land lane set (`soil-moisture` toggles the `soil-field-moisture` stream): a capability
 * payload keyed by `layerName` and a store keyed by `LayerToggleId` are two different lookups,
 * and giving them the same strings makes a wrong-map bug invisible in every log line that
 * prints one. Distinct from every `geo.layers.name` for the reason `SLIDER_STREAM_LAYER_NAMES`
 * gives -- and structurally so, because no `geo.layers` row is named `climate-field-*`.
 */
export function climateFieldStreamName(signal: ClimateFieldSignalId): string {
  return `climate-field-${signal}`;
}

/**
 * The nine definitions as everything outside this module reads them: the written table, plus
 * the two ids derived from each signal's own key.
 *
 * `Object.entries` over a record that is exhaustive by its own type cannot under-report, so
 * this cannot omit a signal the table defines, and the derivation means a tenth signal needs
 * one table entry and no other edit in this file.
 */
export const CLIMATE_FIELD_SIGNALS: Readonly<
  Record<ClimateFieldSignalId, ClimateFieldSignalDefinition>
> = Object.fromEntries(
  Object.entries(CLIMATE_FIELD_SIGNAL_TABLE).map(([signal, entry]) => [
    signal,
    {
      ...entry,
      toggleId: climateFieldToggleId(signal as ClimateFieldSignalId),
      streamName: climateFieldStreamName(signal as ClimateFieldSignalId),
    },
  ])
) as Readonly<Record<ClimateFieldSignalId, ClimateFieldSignalDefinition>>;

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
  CLIMATE_FIELD_SIGNAL_TABLE
) as ClimateFieldSignalId[];

/** Every climate toggle, in the order the dock renders the Climate group's rows. */
export const CLIMATE_FIELD_TOGGLE_IDS: readonly ClimateFieldToggleId[] =
  CLIMATE_FIELD_SIGNAL_IDS.map(climateFieldToggleId);

/**
 * The signal a climate toggle switches, or null for a toggle that is not one of the nine.
 *
 * Parses rather than looks up, so it stays total over `string` -- callers hold a
 * `LayerToggleId` from a store that may carry a user-uploaded id, and a lookup table would
 * need a widening cast at every one of those call sites.
 */
export function climateFieldSignalForToggle(
  toggleId: string
): ClimateFieldSignalId | null {
  if (!toggleId.startsWith("climate-")) return null;
  const candidate = toggleId.slice("climate-".length);
  return isClimateFieldSignal(candidate) ? candidate : null;
}

export function climateFieldSignalDefinition(
  signal: ClimateFieldSignalId
): ClimateFieldSignalDefinition {
  return CLIMATE_FIELD_SIGNALS[signal];
}

/** True when the string names a signal this lane publishes. */
export function isClimateFieldSignal(value: string): value is ClimateFieldSignalId {
  return Object.prototype.hasOwnProperty.call(CLIMATE_FIELD_SIGNAL_TABLE, value);
}

/** The form a signal's row opens on: the head of its own list, never a shared default. */
export function defaultClimateRenderForm(signal: ClimateFieldSignalId): ClimateRenderForm {
  return CLIMATE_FIELD_SIGNAL_TABLE[signal].renderForms[0];
}

/**
 * The form to draw a signal in, given whatever the store holds for it.
 *
 * Resolves rather than rejects, and that is the whole point: a persisted store entry, a
 * replayed cache and a signal whose `renderForms` list has since been narrowed all arrive here
 * naming a form the signal no longer offers. Falling back to the signal's own default degrades
 * to a drawn layer; honouring the stale value would draw the one form this signal withholds
 * because it is not honest -- a contour across a pilot, or across daily rainfall.
 *
 * A stored `symbol` is mapped onto `field` BY NAME rather than left to the default, and the
 * difference is visible: `dew-point` opens on `isoline`, so a reader who had explicitly picked
 * points would have been handed contours by the generic fallback. `field` is the honest successor
 * to `symbol` for every signal -- both draw one mark per measured cell and neither fills ground
 * the lane never sampled -- so it is the same choice, drawn at the support it actually describes.
 * Precipitation is the case this was written for: `symbol` was its default until 2026-09-02, so
 * every persisted store from before then names it.
 */
export function resolveClimateRenderForm(
  signal: ClimateFieldSignalId,
  requested: ClimateRenderForm | undefined
): ClimateRenderForm {
  const { renderForms } = CLIMATE_FIELD_SIGNAL_TABLE[signal];
  if (requested !== undefined && renderForms.includes(requested)) return requested;
  if (requested === "symbol" && renderForms.includes("field")) return "field";
  return renderForms[0];
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
