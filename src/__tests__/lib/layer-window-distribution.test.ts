import { describe, expect, it } from "vitest";
import {
  acceptedDistributionSignalNames,
  distributionSignalName,
  windowedToggleForStyleLayer,
} from "@/lib/layer-window-distribution";
import { DEFAULT_SOIL_FIELD_DEPTHS } from "@/lib/environmental/soil-field";

/**
 * Pure mapping tests for the soil half of the window-distribution signal picker: a painted depth
 * must resolve to exactly the warehouse signal agri reads for that depth, and the server's
 * allow-list (`acceptedDistributionSignalNames`, which `layer-window.ts` trusts) must accept
 * exactly what this module can send and nothing a sibling measure's depth would send.
 */
describe("windowedToggleForStyleLayer for a soil field", () => {
  it.each([
    ["soil-moisture-field-fill", "soil-moisture"],
    ["soil-moisture-field-outline", "soil-moisture"],
    ["soil-temperature-field-fill", "soil-temperature"],
    ["soil-temperature-field-outline", "soil-temperature"],
  ])("resolves %s to %s", (styleLayerId, toggleId) => {
    expect(windowedToggleForStyleLayer(styleLayerId)).toBe(toggleId);
  });

  it("does not resolve the value-label layer, which is not hoverable", () => {
    expect(windowedToggleForStyleLayer("soil-moisture-field-value-labels")).toBeNull();
  });
});

describe("distributionSignalName for a soil field", () => {
  it.each([
    ["surface", "soil_water_content_layer_1"],
    ["root-zone", "soil_water_content_layer_2"],
    ["deep", "soil_water_content_layer_3"],
  ] as const)("sends %s moisture as %s, not the other depths' lanes", (depth, signalName) => {
    const depths = { ...DEFAULT_SOIL_FIELD_DEPTHS, moisture: depth };
    expect(distributionSignalName("soil-moisture-field-fill", "mean", depths)).toBe(signalName);
  });

  it.each([
    ["surface", "soil_temperature_level_1"],
    ["root-zone", "soil_temperature_level_2"],
    ["deep", "soil_temperature_level_3"],
    ["substratum", "soil_temperature_level_4"],
  ] as const)("sends %s temperature as %s, not the other depths' lanes", (depth, signalName) => {
    const depths = { ...DEFAULT_SOIL_FIELD_DEPTHS, temperature: depth };
    expect(distributionSignalName("soil-temperature-field-fill", "mean", depths)).toBe(signalName);
  });

  it("defaults to the surface depth when no depths are supplied, like the store seeds itself", () => {
    expect(distributionSignalName("soil-moisture-field-fill", "mean")).toBe("soil_water_content_layer_1");
  });

  it("reads each measure's own depth independently off one fieldDepth map", () => {
    const depths = { ...DEFAULT_SOIL_FIELD_DEPTHS, moisture: "deep" as const, temperature: "substratum" as const };
    expect(distributionSignalName("soil-moisture-field-fill", "mean", depths)).toBe("soil_water_content_layer_3");
    expect(distributionSignalName("soil-temperature-field-fill", "mean", depths)).toBe("soil_temperature_level_4");
  });
});

describe("acceptedDistributionSignalNames for the two soil toggles", () => {
  it("accepts exactly the moisture lane's three depths", () => {
    expect(acceptedDistributionSignalNames("soil-moisture")).toEqual([
      "soil_water_content_layer_1",
      "soil_water_content_layer_2",
      "soil_water_content_layer_3",
    ]);
  });

  it("accepts exactly the temperature lane's four depths", () => {
    expect(acceptedDistributionSignalNames("soil-temperature")).toEqual([
      "soil_temperature_level_1",
      "soil_temperature_level_2",
      "soil_temperature_level_3",
      "soil_temperature_level_4",
    ]);
  });

  it("never accepts the other measure's signal names -- a temperature lane for moisture, or back", () => {
    const moistureAccepted = acceptedDistributionSignalNames("soil-moisture");
    const temperatureAccepted = acceptedDistributionSignalNames("soil-temperature");
    expect(moistureAccepted.some((name) => temperatureAccepted.includes(name))).toBe(false);
    expect(moistureAccepted).not.toContain("soil_temperature_level_1");
    expect(temperatureAccepted).not.toContain("soil_water_content_layer_1");
  });

  it("names nothing for soil-vpd, which has one depth and needs no disambiguation", () => {
    expect(acceptedDistributionSignalNames("soil-vpd")).toEqual([]);
  });
});
