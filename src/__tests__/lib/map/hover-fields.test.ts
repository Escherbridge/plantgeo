import { describe, expect, it } from "vitest";
import { HOVERABLE_LAYER_IDS, formatHoverContent } from "@/lib/map/hover-fields";

/** Fails if any rendered string ever leaks a raw null/undefined/NaN sentinel. */
function assertNoSentinels(content: { title: string; lines: string[] } | null) {
  if (!content) return;
  for (const text of [content.title, ...content.lines]) {
    expect(text).not.toMatch(/\bnull\b/i);
    expect(text).not.toMatch(/\bundefined\b/i);
    expect(text).not.toMatch(/\bNaN\b/i);
  }
}

describe("HOVERABLE_LAYER_IDS", () => {
  it("includes every layer the shared hover manager must query", () => {
    expect(HOVERABLE_LAYER_IDS).toEqual([
      "published-fire-circles",
      "water-gauges-circle",
      "groundwater-wells-circle",
      "sensors",
      "fire-perimeters",
      "evacuation-zones",
      "interventions",
      // The interventions toggle draws two geometries from one tile: a fill and its dashed
      // outline for ingested zones, and a circle layer for the Point geometry every
      // interactively submitted site carries. A fill layer cannot hit-test a Point, so
      // without this id a submitted site was on the map and hovered as empty ground.
      "interventions-points",
      "watersheds-fill",
      "soil-survey-fill",
      // The survey's zoomed-out shape is a lattice of counted points, and a fill layer
      // cannot hit-test a Point -- the same reason "interventions-points" is listed above.
      "soil-survey-summary",
      // The weather toggle's hoverable layer is the temperature circle, not the wind
      // arrows: "weather-wind" is a text symbol whose hit area is the glyph run and whose
      // placement collides away at density.
      "weather-temperature",
      "osm-roads",
      "osm-waterways",
    ]);
  });
});

/**
 * The 2026-09-01 Parquet cutover replaced the per-detection FIRMS point with a published CELL:
 * a square of ground carrying counts and a summed radiative power over one observed day, not one
 * satellite's one reading. None of the old properties (`confidence`, `frp`, `brightness`,
 * `satellite`, `acqDate`/`acqTime`) is emitted any more -- see
 * `src/lib/environmental/parquet-fire-presentation.ts` for the shape the layer actually draws.
 */
describe("formatHoverContent: published-fire-circles", () => {
  it("formats a full published fire cell", () => {
    const content = formatHoverContent("published-fire-circles", {
      detectionCount: 1234,
      frpSum: 12.34,
      frpObservationCount: 9,
      highConfidenceDetectionCount: 87,
      observedDay: "2026-07-14",
      newestObservedAt: new Date(Date.now() - 3 * 3600_000).toISOString(),
      zoomTier: 9,
    });
    expect(content?.title).toBe("Fire detection cell");
    expect(content?.lines).toContain("Detections: 1,234");
    expect(content?.lines).toContain("High confidence: 87");
    expect(content?.lines).toContain("Total FRP: 12.3 MW");
    expect(content?.lines).toContain("Observed: 2026-07-14");
    expect(content?.lines).toContain("Aggregated at z9");
    expect(content?.lines.find((l) => l.startsWith("Newest detection"))).toMatch(/3h ago/);
    assertNoSentinels(content);
  });

  it("shows the newest detection's own timestamp, not only its relative age", () => {
    const newest = formatHoverContent("published-fire-circles", {
      detectionCount: 4,
      newestObservedAt: "2026-07-14T09:30:00Z",
    })?.lines.find((l) => l.startsWith("Newest detection"));
    expect(newest).toMatch(/2026/);
    expect(newest).toMatch(/\d:\d{2}/);
  });

  // A cell whose detections all lacked an FRP reading has `frpSum: 0` from the SUM, which is not
  // the same claim as "these fires radiated no power". The observation count is what tells the
  // two apart, so it -- and not the sum's own value -- decides whether a number is shown.
  it("captions an unobserved FRP rather than reporting the sum's zero", () => {
    const unobserved = formatHoverContent("published-fire-circles", {
      detectionCount: 6,
      frpSum: 0,
      frpObservationCount: 0,
    });
    expect(unobserved?.lines).toContain("Total FRP: Not reported");
    expect(unobserved?.lines.some((l) => l.includes("MW"))).toBe(false);

    const measured = formatHoverContent("published-fire-circles", {
      detectionCount: 6,
      frpSum: 0,
      frpObservationCount: 6,
    });
    expect(measured?.lines).toContain("Total FRP: 0.0 MW");
  });

  // The file-wide rule: a feature carrying none of the fields yields NO tooltip rather than a
  // shell of empty labels. The FRP caption is the only line with a value for an absent field, so
  // it is conditional on `detectionCount` -- a cell that exists always has one. Unconditional, it
  // turned an empty property bag into a one-line tooltip that asserted nothing.
  it("omits missing fields instead of rendering sentinels, and an empty bag yields no tooltip", () => {
    const content = formatHoverContent("published-fire-circles", {
      detectionCount: null,
      highConfidenceDetectionCount: undefined,
      frpSum: NaN,
      observedDay: "null",
      newestObservedAt: undefined,
      zoomTier: undefined,
    });
    expect(content).toBeNull();

    // A cell that DOES exist still says its FRP is unreported rather than staying silent about it.
    const counted = formatHoverContent("published-fire-circles", {
      detectionCount: 6,
      observedDay: "null",
    });
    expect(counted?.lines).toEqual(["Detections: 6", "Total FRP: Not reported"]);
    assertNoSentinels(counted);
  });

  // One formatter, shared with `FireLayer`'s click popup: the two used to render the same six
  // fields differently ("not reported" against "Not reported", `1234.6 MW` against `1,234.6 MW`).
  it("groups a large FRP sum the way the popup does", () => {
    const content = formatHoverContent("published-fire-circles", {
      detectionCount: 12,
      frpSum: 1234.56,
      frpObservationCount: 12,
    });
    expect(content?.lines).toContain("Total FRP: 1,234.6 MW");
  });
});

describe("formatHoverContent: water-gauges-circle", () => {
  it("formats a full gauge reading", () => {
    const content = formatHoverContent("water-gauges-circle", {
      siteName: "Boise River at Glenwood",
      flowCfs: 1523.456,
      condition: "below_normal",
      trend: "declining",
      percentile: 12.4,
      updatedAt: new Date(Date.now() - 45 * 60_000).toISOString(),
    });
    expect(content?.title).toBe("Boise River at Glenwood");
    expect(content?.lines).toContain("Flow: 1523.5 cfs");
    expect(content?.lines).toContain("Condition: Below normal");
    expect(content?.lines).toContain("Trend: Declining");
    expect(content?.lines).toContain("Percentile: 12%");
    expect(content?.lines.find((l) => l.startsWith("Updated"))).toMatch(/45m ago/);
    assertNoSentinels(content);
  });

  it("falls back to a generic title when siteName is missing, and omits null percentile", () => {
    const content = formatHoverContent("water-gauges-circle", {
      flowCfs: 10,
      percentile: null,
    });
    expect(content?.title).toBe("Water gauge");
    expect(content?.lines).toContain("Flow: 10.0 cfs");
    expect(content?.lines.some((l) => l.startsWith("Percentile"))).toBe(false);
    assertNoSentinels(content);
  });
});

describe("formatHoverContent: groundwater-wells-circle", () => {
  it("formats a full well reading", () => {
    const content = formatHoverContent("groundwater-wells-circle", {
      siteName: "Well 12N-1W",
      depthFt: 88.25,
      trend: "rising",
      updatedAt: new Date(Date.now() - 2 * 86400_000).toISOString(),
    });
    expect(content?.title).toBe("Well 12N-1W");
    expect(content?.lines).toContain("Depth: 88.3 ft");
    expect(content?.lines).toContain("Trend: Rising");
    expect(content?.lines.find((l) => l.startsWith("Updated"))).toMatch(/2d ago/);
    assertNoSentinels(content);
  });

  it("returns null when nothing meaningful is present", () => {
    expect(formatHoverContent("groundwater-wells-circle", {})).toBeNull();
  });
});

describe("formatHoverContent: sensors", () => {
  it("formats a full sensor station reading", () => {
    const content = formatHoverContent("sensors", {
      station_name: "Boise Air Terminal",
      network: "ASOS",
      sensor_id: "KBOI",
      observed_at: new Date(Date.now() - 20 * 60_000).toISOString(),
    });
    expect(content?.title).toBe("Boise Air Terminal");
    expect(content?.lines).toContain("Network: ASOS");
    expect(content?.lines).toContain("Station ID: KBOI");
    expect(content?.lines.find((l) => l.startsWith("Observed"))).toMatch(/20m ago/);
    assertNoSentinels(content);
  });

  it("falls back to a generic title when station_name is missing", () => {
    const content = formatHoverContent("sensors", { network: "RAWS" });
    expect(content?.title).toBe("Weather station");
    expect(content?.lines).toEqual(["Network: RAWS"]);
    assertNoSentinels(content);
  });

  it("returns null when nothing meaningful is present", () => {
    expect(formatHoverContent("sensors", {})).toBeNull();
  });
});

describe("formatHoverContent: fire-perimeters", () => {
  it("formats a full perimeter", () => {
    const content = formatHoverContent("fire-perimeters", {
      incidentName: "Elk Ridge Fire",
      gisAcres: 12345.6,
      percentContained: 45,
      severity: "critical",
      fireCause: "Lightning",
      pooState: "ID",
    });
    expect(content?.title).toBe("Elk Ridge Fire");
    expect(content?.lines).toContain("Size: 12,345.6 acres");
    expect(content?.lines).toContain("45% contained");
    expect(content?.lines).toContain("Severity: Critical");
    expect(content?.lines).toContain("Cause: Lightning");
    expect(content?.lines).toContain("State: ID");
    assertNoSentinels(content);
  });

  it("distinguishes the discovery date from the perimeter's last redraw", () => {
    const content = formatHoverContent("fire-perimeters", {
      incidentName: "Elk Ridge Fire",
      fireDiscoveryDateTime: "2026-07-14T09:30:00Z",
      polygonDateTime: new Date(Date.now() - 3 * 3600_000).toISOString(),
    });
    expect(content?.lines.find((l) => l.startsWith("Discovered"))).toMatch(/2026/);
    expect(content?.lines.find((l) => l.startsWith("Perimeter updated"))).toMatch(/3h ago/);
    assertNoSentinels(content);
  });

  it("accepts an epoch-millisecond discovery time from WFIGS", () => {
    const content = formatHoverContent("fire-perimeters", {
      fireDiscoveryDateTime: 1_753_000_000_000,
    });
    expect(content?.lines.find((l) => l.startsWith("Discovered"))).toMatch(/2025/);
    assertNoSentinels(content);
  });

  it("tolerates only severity being present (today's ingestion reality)", () => {
    const content = formatHoverContent("fire-perimeters", { severity: "high" });
    expect(content?.title).toBe("Fire perimeter");
    expect(content?.lines).toEqual(["Severity: High"]);
    assertNoSentinels(content);
  });
});

describe("formatHoverContent: evacuation-zones", () => {
  it("formats a full evacuation zone", () => {
    const content = formatHoverContent("evacuation-zones", {
      evacuation_area_name: "Riverside Estates",
      fire_name: "Elk Ridge Fire",
      county: "Deschutes",
      severity: "critical",
      evacuation_level_label: "Go Now",
      structures_within: 42,
      population_within: 118,
    });
    expect(content?.title).toBe("Riverside Estates");
    expect(content?.lines).toContain("Level: Go Now");
    expect(content?.lines).toContain("County: Deschutes");
    expect(content?.lines).toContain("Structures within: 42");
    expect(content?.lines).toContain("Population within: 118");
    assertNoSentinels(content);
  });

  it("falls back to fireName, then a generic title, when evacuationAreaName is missing", () => {
    expect(
      formatHoverContent("evacuation-zones", {
        fire_name: "Elk Ridge Fire",
        county: "Deschutes",
      })?.title
    ).toBe("Elk Ridge Fire");
    expect(formatHoverContent("evacuation-zones", { county: "Deschutes" })?.title).toBe(
      "Evacuation zone"
    );
  });

  it("returns null when nothing meaningful is present", () => {
    expect(formatHoverContent("evacuation-zones", {})).toBeNull();
  });
});

describe("formatHoverContent: interventions", () => {
  it("formats a full intervention", () => {
    const content = formatHoverContent("interventions", {
      name: "Riparian buffer restoration",
      priority: "High",
      status: "Active",
      description: "Replant native buffer along the creek.",
    });
    expect(content?.title).toBe("Riparian buffer restoration");
    expect(content?.lines).toContain("Priority: High");
    expect(content?.lines).toContain("Status: Active");
    expect(content?.lines).toContain("Replant native buffer along the creek.");
    assertNoSentinels(content);
  });

  it("falls back to a generic title when name is missing", () => {
    const content = formatHoverContent("interventions", { priority: "Low" });
    expect(content?.title).toBe("Intervention");
    expect(content?.lines).toEqual(["Priority: Low"]);
    assertNoSentinels(content);
  });
});

// Property names below are the WBDHU12 layer's own attribute names, taken from a live
// ArcGIS `f=geojson` response rather than from the title-case aliases the service catalog
// displays -- `f=geojson` emits the attribute names, so "Name"/"HUC12" never arrive.
describe("formatHoverContent: watersheds-fill", () => {
  it("formats a full HUC12 boundary", () => {
    const content = formatHoverContent("watersheds-fill", {
      name: "Kellogg Creek",
      huc12: "170900120102",
      areasqkm: 42.18,
      areaacres: 10422.32,
      tohuc: "170900120104",
      states: "OR",
    });
    expect(content?.title).toBe("Kellogg Creek");
    expect(content?.lines).toContain("HUC12: 170900120102");
    expect(content?.lines).toContain("Area: 42.2 km²");
    expect(content?.lines).toContain("States: OR");
    expect(content?.lines).toContain("Drains to: 170900120104");
    assertNoSentinels(content);
  });

  it("omits a null States rather than rendering the sentinel", () => {
    const content = formatHoverContent("watersheds-fill", {
      name: "Lake River-Frontal Columbia River",
      huc12: "170800030104",
      states: null,
    });
    expect(content?.lines.some((l) => l.startsWith("States"))).toBe(false);
    assertNoSentinels(content);
  });

  it("falls back to a generic title when name is missing", () => {
    const content = formatHoverContent("watersheds-fill", { huc12: "170900120102" });
    expect(content?.title).toBe("Watershed");
    expect(content?.lines).toEqual(["HUC12: 170900120102"]);
  });

  it("shows no tooltip for a feature carrying none of the HUC12 attributes", () => {
    expect(formatHoverContent("watersheds-fill", {})).toBeNull();
  });
});

describe("formatHoverContent: soil-survey-fill", () => {
  it("formats a full SSURGO map unit", () => {
    const content = formatHoverContent("soil-survey-fill", {
      mukey: "462571",
      muname: "Jory silty clay loam, 3 to 12 percent slopes",
      soilSeries: "Jory",
      drainageClass: "well-drained",
      hydric: false,
      landCapabilityClass: "3e",
    });
    expect(content?.title).toBe("Jory silty clay loam, 3 to 12 percent slopes");
    expect(content?.lines).toContain("Series: Jory");
    expect(content?.lines).toContain("Drainage: Well drained");
    expect(content?.lines).toContain("Land capability: 3e");
    expect(content?.lines).toContain("Hydric: No");
    expect(content?.lines).toContain("Map unit: 462571");
    assertNoSentinels(content);
  });

  it("reports a hydric map unit, and omits the line when the rating is absent", () => {
    expect(
      formatHoverContent("soil-survey-fill", { soilSeries: "Semiahmoo", hydric: true })?.lines
    ).toContain("Hydric: Yes");
    // An absent rating is not a "No": the line disappears instead of asserting one.
    const unrated = formatHoverContent("soil-survey-fill", { soilSeries: "Semiahmoo" });
    expect(unrated?.lines.some((l) => l.startsWith("Hydric"))).toBe(false);
    assertNoSentinels(unrated);
  });

  it("falls back to a generic title, and drops the empty mukey the service can emit", () => {
    const content = formatHoverContent("soil-survey-fill", {
      mukey: "",
      soilSeries: "Unknown",
      drainageClass: "unknown",
    });
    expect(content?.title).toBe("Soil map unit");
    expect(content?.lines.some((l) => l.startsWith("Map unit"))).toBe(false);
    assertNoSentinels(content);
  });

  it("returns null when nothing meaningful is present", () => {
    expect(formatHoverContent("soil-survey-fill", {})).toBeNull();
  });

  it("labels an aggregated cell as an average, not a surveyed map unit", () => {
    const content = formatHoverContent("soil-survey-fill", {
      aggregated: true,
      drainageClass: "well-drained",
      mapUnitCount: 4,
      hydricFraction: 0.25,
    });
    expect(content?.title).toBe("Soil drainage average");
    expect(content?.lines).toContain("Dominant drainage: Well drained");
    expect(content?.lines).toContain("Built from 4 real map units");
    expect(content?.lines).toContain("Hydric share: 25%");
    // Never a surveyed-unit caption: an aggregated feature carries no mukey/muname.
    expect(content?.lines.some((l) => l.startsWith("Map unit"))).toBe(false);
    assertNoSentinels(content);
  });

  it("omits the hydric-share line when no merged unit carried a rating", () => {
    const content = formatHoverContent("soil-survey-fill", {
      aggregated: true,
      drainageClass: "poorly-drained",
      mapUnitCount: 1,
      hydricFraction: null,
    });
    expect(content?.lines.some((l) => l.startsWith("Hydric share"))).toBe(false);
    assertNoSentinels(content);
  });
});

describe("formatHoverContent: osm-roads", () => {
  it("formats a full road", () => {
    const content = formatHoverContent("osm-roads", {
      name: "Main St",
      highway: "primary",
      surface: "asphalt",
      lanes: 4,
      maxspeed: "35 mph",
    });
    expect(content?.title).toBe("Main St");
    expect(content?.lines).toContain("Type: primary");
    expect(content?.lines).toContain("Surface: asphalt");
    expect(content?.lines).toContain("Lanes: 4");
    expect(content?.lines).toContain("Max speed: 35 mph");
    assertNoSentinels(content);
  });

  it("falls back to highway as the title when name is missing", () => {
    const content = formatHoverContent("osm-roads", { highway: "residential" });
    expect(content?.title).toBe("residential");
    expect(content?.lines).toEqual(["Type: residential"]);
    assertNoSentinels(content);
  });

  it("falls back to a generic title when both name and highway are missing", () => {
    const content = formatHoverContent("osm-roads", { surface: "gravel" });
    expect(content?.title).toBe("Road");
    expect(content?.lines).toEqual(["Surface: gravel"]);
    assertNoSentinels(content);
  });
});

describe("formatHoverContent: osm-waterways", () => {
  it("formats a full waterway", () => {
    const content = formatHoverContent("osm-waterways", {
      name: "Boise River",
      waterway: "river",
    });
    expect(content?.title).toBe("Boise River");
    expect(content?.lines).toEqual(["Type: river"]);
    assertNoSentinels(content);
  });

  it("falls back to waterway, then a generic title", () => {
    expect(formatHoverContent("osm-waterways", { waterway: "stream" })?.title).toBe("stream");
    expect(formatHoverContent("osm-waterways", {})).toBeNull();
  });
});

describe("formatHoverContent: weather-temperature", () => {
  it("formats a full observation in the units the feed measures in", () => {
    const content = formatHoverContent("weather-temperature", {
      temperature: 21.37,
      windSpeed: 3.42,
      windDirection: 214.6,
      humidity: 48.2,
      observedAt: new Date(Date.now() - 2 * 3600_000).toISOString(),
    });
    // No station name: the feed is a grid sample, not a named site.
    expect(content?.title).toBe("Weather observation");
    expect(content?.lines).toContain("Temperature: 21.4 °C");
    expect(content?.lines).toContain("Wind: 3.4 m/s from 215°");
    expect(content?.lines).toContain("Humidity: 48%");
    expect(content?.lines.find((l) => l.startsWith("Observed"))).toMatch(/2h ago/);
    assertNoSentinels(content);
  });

  it("captions a station that measured only temperature, without inventing a wind", () => {
    const content = formatHoverContent("weather-temperature", {
      temperature: -4.2,
      windSpeed: null,
      windDirection: null,
      humidity: null,
    });
    expect(content?.lines).toEqual(["Temperature: -4.2 °C"]);
    assertNoSentinels(content);
  });

  it("omits a direction the observation did not carry rather than the whole wind", () => {
    const content = formatHoverContent("weather-temperature", {
      temperature: 10,
      windSpeed: 2,
      windDirection: null,
    });
    expect(content?.lines).toContain("Wind: 2.0 m/s");
  });

  it("shows nothing for a feature carrying no measurement at all", () => {
    expect(formatHoverContent("weather-temperature", {})).toBeNull();
  });
});

describe("formatHoverContent: soil-survey-summary", () => {
  it("captions a lattice cell as a count over ground, never as a surveyed unit", () => {
    const content = formatHoverContent("soil-survey-summary", {
      aggregated: true,
      summary: true,
      drainageClass: "somewhat-poorly-drained",
      mapUnitCount: 1240,
      hydricFraction: 0.25,
      cellDegrees: 0.5,
    });
    expect(content?.title).toBe("Soil survey coverage");
    expect(content?.lines).toContain("1,240 map units surveyed in this cell");
    expect(content?.lines).toContain("Most common drainage: Somewhat poorly drained");
    expect(content?.lines).toContain("Hydric share: 25%");
    expect(content?.lines).toContain("Cell: 0.5° square");
    // Structurally unable to pass as a surveyed unit, exactly as the union tier is.
    expect(content?.title).not.toBe("Soil map unit");
    assertNoSentinels(content);
  });

  it("still reads an averaged polygon through the same formatter", () => {
    const content = formatHoverContent("soil-survey-summary", {
      aggregated: true,
      drainageClass: "well-drained",
      mapUnitCount: 6,
    });
    expect(content?.title).toBe("Soil drainage average");
  });
});

describe("formatHoverContent: unknown layers", () => {
  it("returns null for any layer id not in the hover contract", () => {
    expect(formatHoverContent("buildings-3d", { height: 10 })).toBeNull();
    // The wind arrows are drawn but not hoverable; the temperature dot under them is.
    expect(formatHoverContent("weather-wind", { windSpeed: 3 })).toBeNull();
    expect(formatHoverContent("", {})).toBeNull();
  });
});
