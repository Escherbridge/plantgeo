import { describe, expect, it } from "vitest";
import { decodeForecastResponse, forecastQuerySchema } from "./contract";
import { fixtureQuery, makeForecastFixture } from "./test-fixture";

describe("forecast identity and science boundary", () => {
  it("preserves numeric zero precipitation and future valid hours", () => {
    const result = decodeForecastResponse(makeForecastFixture(), fixtureQuery);
    expect(result.hourly[0].values.precipitation).toBe(0);
    expect(result.hourly.at(-1)?.valid_time).toBe("2026-09-14T23:00:00.000Z");
  });
  it("refuses another run, place and retained prior-day frame", () => {
    const otherRun = makeForecastFixture();
    otherRun.run!.run_id = "other-run";
    expect(() => decodeForecastResponse(otherRun, fixtureQuery)).toThrow(/run/i);
    const otherPlace = makeForecastFixture();
    otherPlace.support!.latitude = 40.1;
    expect(() => decodeForecastResponse(otherPlace, fixtureQuery)).toThrow(/location/i);
    const priorDay = makeForecastFixture();
    priorDay.hourly[0].valid_time = "2026-09-12T00:00:00Z";
    expect(() => decodeForecastResponse(priorDay, fixtureQuery)).toThrow(/time/i);
  });
  it("refuses silent gaps, unit drift and unexplained nulls", () => {
    const gap = makeForecastFixture();
    gap.hourly.pop();
    expect(() => decodeForecastResponse(gap, fixtureQuery)).toThrow(/cover/);
    const units = makeForecastFixture();
    units.variables.wind_speed_10m.unit = "km/h";
    expect(() => decodeForecastResponse(units, fixtureQuery)).toThrow(/units/);
    const missing = makeForecastFixture();
    missing.hourly[0].values.precipitation = null;
    expect(() => decodeForecastResponse(missing, fixtureQuery)).toThrow(/reason/);
  });
  it("preserves typed refusal and rejects refusal with values", () => {
    const outside = { ...makeForecastFixture(), status: "outside-domain", hourly: [], daily: [], support: null };
    expect(decodeForecastResponse(outside, fixtureQuery).status).toBe("outside-domain");
    expect(() => decodeForecastResponse({ ...outside, hourly: makeForecastFixture().hourly }, fixtureQuery)).toThrow(/contains values/);
  });
  it("refuses partial daily coverage labelled complete and query over cap", () => {
    const query = { ...fixtureQuery, end: "2026-09-13T12:00:00Z" };
    const partial = makeForecastFixture(query);
    partial.daily[0].complete = true;
    expect(() => decodeForecastResponse(partial, query)).toThrow(/completeness/);
    expect(forecastQuerySchema.safeParse({ ...fixtureQuery, end: "2026-09-16T00:00:00Z" }).success).toBe(false);
  });
  it("preserves a calm daily vector explanation without implying missing source hours", () => {
    const calm = makeForecastFixture();
    calm.daily[0].wind_direction = null;
    calm.daily[0].missingness.wind_direction = "calm-vector-direction-undefined";
    const decoded = decodeForecastResponse(calm, fixtureQuery);
    expect(decoded.daily[0].complete).toBe(true);
    expect(decoded.daily[0].missingness.wind_direction).toBe("calm-vector-direction-undefined");
  });
});
