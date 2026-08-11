import { describe, expect, it } from "vitest";
import {
  AIR_TEMPERATURE_VARIANTS,
  AIR_TEMPERATURE_VARIANT_IDS,
  CLIMATE_FIELD_SIGNALS,
  CLIMATE_FIELD_SIGNAL_IDS,
  climateFieldBandFor,
  climateFieldColorStops,
  climateFieldSignalDefinition,
  climateFieldSignalName,
  DEFAULT_AIR_TEMPERATURE_VARIANT,
  DEFAULT_CLIMATE_FIELD_SIGNAL,
  isClimateFieldSignal,
  type ClimateFieldSignalId,
} from "@/lib/environmental/climate-field";

/**
 * The band tables are the one thing the map, the legend and the panel all read, and every
 * defect they can carry is silent: a palette one colour short paints the top band with the
 * band below it, a non-ascending interpolate stop makes MapLibre reject the whole `fill-color`
 * expression at style build (which surfaces as an unstyled layer, not an error anyone reads),
 * and a label whose bounds do not match the break it came from legends a colour honestly for
 * the wrong interval. None of those is visible in a type check.
 */
describe("climate field band tables", () => {
  it.each(CLIMATE_FIELD_SIGNAL_IDS)("gives %s one more colour than it has breaks", (signal) => {
    const { bands, bandBreaks } = CLIMATE_FIELD_SIGNALS[signal];
    // n breaks cut the line into n+1 intervals, and every one of them must have its own
    // colour. `buildBands` falls back to the last colour when the palette is short, so a
    // missing entry does not throw -- it silently merges the top two bands.
    expect(bands).toHaveLength(bandBreaks.length + 1);
    expect(new Set(bands.map((band) => band.color)).size).toBe(bands.length);
  });

  it.each(CLIMATE_FIELD_SIGNAL_IDS)("derives strictly ascending %s stops", (signal) => {
    const stops = climateFieldColorStops(signal);
    // Flat [value, color, value, color, ...] as MapLibre's `interpolate` takes it.
    expect(stops).toHaveLength(CLIMATE_FIELD_SIGNALS[signal].bands.length * 2);

    const values = stops.filter((_entry, index) => index % 2 === 0) as number[];
    const colors = stops.filter((_entry, index) => index % 2 === 1);
    expect(values.every((value) => Number.isFinite(value))).toBe(true);
    expect(colors.every((color) => typeof color === "string")).toBe(true);
    for (let index = 1; index < values.length; index++) {
      // MapLibre requires strictly increasing input stops; equal or descending ones make the
      // expression invalid and the layer paints nothing.
      expect(values[index], `${signal} stop ${index}`).toBeGreaterThan(values[index - 1]);
    }
  });

  it.each(CLIMATE_FIELD_SIGNAL_IDS)("keeps every %s stop inside its declared domain", (signal) => {
    const { domainMinimum, domainMaximum, bands } = CLIMATE_FIELD_SIGNALS[signal];
    expect(domainMaximum).toBeGreaterThan(domainMinimum);
    // The open tails take the domain bounds rather than a half-band overshoot, which is the
    // one place this builder deviates from soil-field.ts. The bug it prevents: precipitation's
    // uneven breaks put the bottom stop at -0.35 mm/day under the soil rule.
    for (const band of bands) {
      expect(band.representativeValue, `${signal} band ${band.bandIndex}`)
        .toBeGreaterThanOrEqual(domainMinimum);
      expect(band.representativeValue, `${signal} band ${band.bandIndex}`)
        .toBeLessThanOrEqual(domainMaximum);
    }
  });

  it.each(CLIMATE_FIELD_SIGNAL_IDS)("matches %s band bounds to the breaks they came from", (signal) => {
    const { bands, bandBreaks } = CLIMATE_FIELD_SIGNALS[signal];

    // The tails are open at exactly one end each, and only at the ends.
    expect(bands[0].minimum).toBeNull();
    expect(bands[bands.length - 1].maximum).toBeNull();
    for (const band of bands.slice(1)) expect(band.minimum).not.toBeNull();
    for (const band of bands.slice(0, -1)) expect(band.maximum).not.toBeNull();

    // Every interior edge is a declared break, used once as an upper bound and once as the
    // next band's lower bound: contiguous, no gap, no overlap.
    expect(bands.slice(0, -1).map((band) => band.maximum)).toEqual([...bandBreaks]);
    expect(bands.slice(1).map((band) => band.minimum)).toEqual([...bandBreaks]);
  });

  it.each(CLIMATE_FIELD_SIGNAL_IDS)("prints %s labels from the same numbers", (signal) => {
    for (const band of CLIMATE_FIELD_SIGNALS[signal].bands) {
      // Every number the caption shows must be one of the band's own bounds -- the legend is
      // the only place a reader learns what a colour means, so a rounded caption ("1 to 3" for
      // a 1..2.5 band) misdescribes the fill it sits beside.
      const printed = band.label.match(/-?\d+(?:\.\d+)?/g) ?? [];
      const bounds = [band.minimum, band.maximum].filter(
        (bound): bound is number => bound !== null
      );
      expect(printed).toHaveLength(bounds.length);
      printed.forEach((text, index) => {
        expect(Number(text), `${signal} "${band.label}"`).toBe(bounds[index]);
      });
    }
    // Signed bands read "a to b", never "a - b": a reader cannot tell a range separator from
    // a minus sign, which is why soil-field.ts adopted the same rule.
    for (const band of CLIMATE_FIELD_SIGNALS[signal].bands) {
      if (band.minimum !== null && band.maximum !== null) {
        expect(band.label).toContain(" to ");
      }
    }
  });

  it("classifies any real number into exactly one band, tails included", () => {
    for (const signal of CLIMATE_FIELD_SIGNAL_IDS) {
      const { bands, domainMinimum, domainMaximum } = CLIMATE_FIELD_SIGNALS[signal];
      const probes = [
        domainMinimum - 1000,
        domainMinimum,
        (domainMinimum + domainMaximum) / 2,
        domainMaximum,
        domainMaximum + 1000,
        ...bands.map((band) => band.representativeValue),
      ];
      for (const value of probes) {
        const band = climateFieldBandFor(signal, value);
        expect(bands, `${signal} @ ${value}`).toContain(band);
        if (band.minimum !== null) expect(value).toBeGreaterThanOrEqual(band.minimum);
        if (band.maximum !== null) expect(value).toBeLessThan(band.maximum);
      }
    }
  });
});

describe("climate field signal vocabulary", () => {
  // N3: the id list is derived from the record rather than hand-written, so a tenth signal
  // cannot be added to the table and silently omitted from the picker and the tRPC enum.
  it("derives the wire id list from the table, in declaration order", () => {
    expect(CLIMATE_FIELD_SIGNAL_IDS).toEqual(Object.keys(CLIMATE_FIELD_SIGNALS));
    expect(CLIMATE_FIELD_SIGNAL_IDS).toHaveLength(9);
    expect(new Set(CLIMATE_FIELD_SIGNAL_IDS).size).toBe(CLIMATE_FIELD_SIGNAL_IDS.length);
    for (const signal of CLIMATE_FIELD_SIGNAL_IDS) {
      expect(isClimateFieldSignal(signal)).toBe(true);
      expect(climateFieldSignalDefinition(signal).signal).toBe(signal);
    }
    expect(isClimateFieldSignal("soil_water_content_layer_1")).toBe(false);
  });

  it("resolves exactly one warehouse signal name per selection", () => {
    const resolved = new Set<string>();
    for (const signal of CLIMATE_FIELD_SIGNAL_IDS) {
      const definition = CLIMATE_FIELD_SIGNALS[signal];
      if (definition.variants.length === 0) {
        expect(definition.signalName).not.toBeNull();
        resolved.add(climateFieldSignalName(signal));
        continue;
      }
      // `air-temperature` is the only signal whose name comes from the variant.
      expect(definition.signalName).toBeNull();
      for (const variant of AIR_TEMPERATURE_VARIANT_IDS) {
        resolved.add(climateFieldSignalName(signal, variant));
      }
    }
    // Eight signals + three air-temperature statistics, minus the one whose name is a
    // variant's: eleven warehouse signals, one per governed row in drizzle/0020.
    expect(resolved.size).toBe(11);
    expect(resolved).toContain("air_temperature_max");
    expect(resolved).toContain("soil_wetness_profile");
  });

  it("degrades a variant the signal does not publish instead of inventing a name", () => {
    // A stale store value or an IndexedDB entry replayed from an older schema must resolve to
    // a drawn field, never to a query for a signal_name that cannot exist.
    expect(climateFieldSignalName("precipitation", "max")).toBe("precipitation");
    expect(climateFieldSignalName("air-temperature", "max")).toBe("air_temperature_max");
    expect(climateFieldSignalName("air-temperature")).toBe("air_temperature_mean");
  });

  it("seeds the stores with a signal and a variant the tables publish", () => {
    expect(CLIMATE_FIELD_SIGNAL_IDS).toContain(DEFAULT_CLIMATE_FIELD_SIGNAL);
    expect(AIR_TEMPERATURE_VARIANT_IDS).toContain(DEFAULT_AIR_TEMPERATURE_VARIANT);
    expect(AIR_TEMPERATURE_VARIANTS.map((entry) => entry.variant)).toEqual([
      ...AIR_TEMPERATURE_VARIANT_IDS,
    ]);
  });

  it("captions no signal as a pilot, now that every one covers the whole lattice", () => {
    // The three soil-wetness signals carried a coverage note and a "— pilot" label until
    // 2026-08-10. Measured against production they are 4 cells only for their first 98 days
    // (2022-04-30..2022-08-05) and the full 397 for the 1,462 days since -- the same width as air
    // temperature on every one of those days. `coverageNote` is null when coverage IS the lattice,
    // and the early window needs no static sentence because `ClimateDetails` composes the counted
    // one per request from cellCount/latticeCellCount.
    const captioned = CLIMATE_FIELD_SIGNAL_IDS.filter(
      (signal) => CLIMATE_FIELD_SIGNALS[signal].coverageNote !== null
    );
    expect(captioned).toEqual([] satisfies ClimateFieldSignalId[]);
    for (const signal of CLIMATE_FIELD_SIGNAL_IDS) {
      expect(CLIMATE_FIELD_SIGNALS[signal].label.toLowerCase()).not.toContain("pilot");
    }
  });
});
