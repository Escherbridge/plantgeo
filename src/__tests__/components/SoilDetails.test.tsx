import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, screen } from "@testing-library/react";
import { renderWithProviders } from "@/test/utils";
import { useMapStore } from "@/stores/map-store";
import { useSoilStore } from "@/stores/soil-store";

/**
 * `environmental.getSoilSurvey` reports three different things through one shape:
 * a complete view, a view USDA truncated at its row ceiling, and a view USDA failed to
 * answer at all. The last two both arrive with polygons the map cannot distinguish from
 * surveyed-and-empty ground, so this dock section is the only surface that can tell them
 * apart -- these cases pin that it does. tRPC is stubbed rather than driven over a link,
 * same rationale as CommunityDetails.test.tsx.
 */
type SoilSurveyResult = {
  data:
    | (GeoJSON.FeatureCollection & {
        availability: "published" | "unavailable";
        reason: string | null;
        truncated: boolean;
        unreadableGeometries: number;
        /**
         * Optional here although `ProxiedSoilSurveyCollection` declares it required, so the
         * "no granularity" case below stays expressible: that case is what a response
         * predating the zoom-aware tiers looks like, and it must read as "detail".
         */
        granularity?: "detail" | "regional-average" | "coarse-average";
        /**
         * Optional for the same reason as `granularity`: a response predating persistence
         * carries no coverage, and must claim no gap rather than inventing one.
         */
        coverage?: { cells: number; covered: number; ingested: number };
      })
    | undefined;
  isLoading: boolean;
  isError: boolean;
  /**
   * The retention pair. These reads hold the previous answer while the next loads
   * (`keepPreviousData`, see `useViewportProxiedLayers`), which sets `status: "success"` -- so
   * `isLoading` is permanently false after the first success and a spinner keyed on it never
   * fires again. Optional so every existing case stays a settled read.
   */
  isFetching?: boolean;
  isPlaceholderData?: boolean;
};

/** Mirrors `SoilProperties` from soilgrids.ts; hand-rolled so the stub pulls in no server code. */
type SoilPropertiesResult = {
  data:
    | {
        ph: number;
        organicCarbon: number;
        nitrogen: number;
        bulkDensity: number;
        cec: number;
        ocd: number;
      }
    | undefined;
  isLoading: boolean;
  isError: boolean;
  /**
   * The retention pair. These reads hold the previous answer while the next loads
   * (`keepPreviousData`, see `useViewportProxiedLayers`), which sets `status: "success"` -- so
   * `isLoading` is permanently false after the first success and a spinner keyed on it never
   * fires again. Optional so every existing case stays a settled read.
   */
  isFetching?: boolean;
  isPlaceholderData?: boolean;
};

/**
 * Mirrors `PublishedSoilFieldCollection`, hand-rolled so the stub pulls in no server
 * code. Only the fields the panel actually reads are declared.
 */
type SoilFieldResult = {
  data:
    | (GeoJSON.FeatureCollection & {
        availability: "published" | "unavailable";
        reason: "not_published" | "stale" | "not_forecastable" | null;
        granularity: "detail" | "regional-average" | "coarse-average";
        unit: string;
        attribution: string;
        observedDay: string | null;
        requestedDay: string;
        newestAvailableDay: string | null;
        cellCount: number;
        maxObservationAgeDays: number;
        latticeDegrees: number | null;
        bands: { bandIndex: number; color: string; label: string }[];
      })
    | undefined;
  isLoading: boolean;
  isError: boolean;
  /**
   * The retention pair. These reads hold the previous answer while the next loads
   * (`keepPreviousData`, see `useViewportProxiedLayers`), which sets `status: "success"` -- so
   * `isLoading` is permanently false after the first success and a spinner keyed on it never
   * fires again. Optional so every existing case stays a settled read.
   */
  isFetching?: boolean;
  isPlaceholderData?: boolean;
};

const queries = vi.hoisted(() => ({
  getSoilProperties: vi.fn(
    (): SoilPropertiesResult => ({ data: undefined, isLoading: false, isError: false })
  ),
  getInterventionSuitability: vi.fn(() => ({
    data: undefined,
    isLoading: false,
    isError: false,
  })),
  // Declared with no parameters on purpose: both arguments still land on `mock.calls`,
  // which is where the `enabled` flag is read from.
  getSoilSurvey: vi.fn(
    (): SoilSurveyResult => ({ data: undefined, isLoading: false, isError: false })
  ),
  getSoilField: vi.fn(
    (): SoilFieldResult => ({ data: undefined, isLoading: false, isError: false })
  ),
}));

vi.mock("@/lib/trpc/client", () => ({
  trpc: {
    environmental: {
      getSoilProperties: { useQuery: queries.getSoilProperties },
      getInterventionSuitability: { useQuery: queries.getInterventionSuitability },
      getSoilSurvey: { useQuery: queries.getSoilSurvey },
      getSoilField: { useQuery: queries.getSoilField },
    },
  },
}));

import { SoilDetails } from "@/components/panels/SoilDetails";

/** A collection carrying `count` throwaway map-unit polygons. */
function soilSurveyCollection(
  count: number,
  overrides: {
    truncated?: boolean;
    availability?: "published" | "unavailable";
    reason?: string;
    unreadableGeometries?: number;
    coverage?: { cells: number; covered: number; ingested: number };
  } = {}
): NonNullable<SoilSurveyResult["data"]> {
  return {
    type: "FeatureCollection",
    features: Array.from({ length: count }, (_unused, index) => ({
      type: "Feature" as const,
      geometry: {
        type: "Polygon" as const,
        coordinates: [
          [
            [-116.35, 43.5],
            [-116.34, 43.5],
            [-116.34, 43.51],
            [-116.35, 43.51],
            [-116.35, 43.5],
          ],
        ],
      },
      properties: { mukey: String(index) },
    })),
    availability: overrides.availability ?? "published",
    reason: overrides.reason ?? null,
    truncated: overrides.truncated ?? false,
    unreadableGeometries: overrides.unreadableGeometries ?? 0,
    ...(overrides.coverage === undefined ? {} : { coverage: overrides.coverage }),
  };
}

/**
 * The `enabled` flag the panel passed for the viewport-proxied survey query. Takes the
 * mock loosely typed, the way LayerManager.test.tsx does: the stubs above declare no
 * parameters, so their recorded `calls` are an empty tuple to the compiler.
 */
function enabledFlagOf(query: { mock: { calls: unknown[][] } }): boolean | undefined {
  const lastCall = query.mock.calls.at(-1);
  return (lastCall?.[1] as { enabled?: boolean } | undefined)?.enabled;
}

/**
 * Both soil fields go through one procedure, so a call has to be matched on `measure`
 * rather than by taking the last one.
 */
function soilFieldCallFor(measure: "moisture" | "temperature"): unknown[] | undefined {
  return (queries.getSoilField.mock.calls as unknown as unknown[][])
    .filter((call) => (call[0] as { measure?: string } | undefined)?.measure === measure)
    .at(-1);
}

function soilFieldInputOf(measure: "moisture" | "temperature"): Record<string, unknown> {
  return (soilFieldCallFor(measure)?.[0] ?? {}) as Record<string, unknown>;
}

function soilFieldEnabledFlagOf(measure: "moisture" | "temperature"): boolean | undefined {
  return (soilFieldCallFor(measure)?.[1] as { enabled?: boolean } | undefined)?.enabled;
}

const INITIAL_MAP_STATE = useMapStore.getState();
const INITIAL_SOIL_STATE = useSoilStore.getState();

beforeEach(() => {
  useMapStore.setState(INITIAL_MAP_STATE, true);
  useSoilStore.setState(INITIAL_SOIL_STATE, true);
  // The survey layer is switched on: the panel only describes coverage for a layer the
  // map is actually drawing.
  useMapStore.setState({ activeLayers: ["soil-survey"] });
  queries.getSoilProperties.mockReturnValue({
    data: undefined,
    isLoading: false,
    isError: false,
  });
  queries.getInterventionSuitability.mockReturnValue({
    data: undefined,
    isLoading: false,
    isError: false,
  });
  queries.getSoilSurvey.mockReturnValue({
    data: undefined,
    isLoading: false,
    isError: false,
  });
  queries.getSoilField.mockReturnValue({
    data: undefined,
    isLoading: false,
    isError: false,
  });
});

afterEach(() => {
  vi.clearAllMocks();
  useMapStore.setState(INITIAL_MAP_STATE, true);
  useSoilStore.setState(INITIAL_SOIL_STATE, true);
});

/** The viewport the dock hands down; without one the survey query stays disabled. */
const VIEWPORT_BBOX = "-116.35,43.5,-116.25,43.6";

function renderPanel() {
  return renderWithProviders(
    <SoilDetails bbox={VIEWPORT_BBOX} />
  );
}

describe("SoilDetails SSURGO coverage", () => {
  it("reports a truncated view as partial rather than as the map units in view", () => {
    // Reachable at the sanctioned zoom: at MAX_SOIL_BBOX_SQUARE_DEGREES over Corn Belt
    // farmland SDA holds more than MAX_SOIL_POLYGONS map units, serves the first 1000,
    // and the ground under the rest paints blank -- identical to unsurveyed ground.
    queries.getSoilSurvey.mockReturnValue({
      data: soilSurveyCollection(1000, { truncated: true }),
      isLoading: false,
      isError: false,
    });

    renderPanel();

    expect(screen.getByText(/More map units are stored for this view than it draws/)).toBeTruthy();
    expect(screen.getByText(/subset/)).toBeTruthy();
    // The count must never be presented as the whole view.
    expect(screen.queryByText(/1,?000 SSURGO map units drawn/)).toBeNull();
  });

  it("distinguishes an upstream fault from a view USDA surveyed and found nothing in", () => {
    queries.getSoilSurvey.mockReturnValue({
      data: soilSurveyCollection(0, {
        availability: "unavailable",
        reason: "soil_survey_upstream_returned_no_table",
      }),
      isLoading: false,
      isError: false,
    });

    renderPanel();

    expect(screen.getByText(/did not return a map-unit table/)).toBeTruthy();
    // The empty-coverage claim belongs only to a published answer: USDA never said
    // there is no soil here.
    expect(screen.queryByText(/no surveyed SSURGO map units/)).toBeNull();
  });

  it("reports an honestly empty view as surveyed with nothing in it", () => {
    queries.getSoilSurvey.mockReturnValue({
      data: soilSurveyCollection(0),
      isLoading: false,
      isError: false,
    });

    renderPanel();

    expect(screen.getByText(/no surveyed SSURGO map units/)).toBeTruthy();
    expect(screen.queryByText(/did not return a map-unit table/)).toBeNull();
  });

  it("states the count for a complete view without a partial-coverage warning", () => {
    queries.getSoilSurvey.mockReturnValue({
      data: soilSurveyCollection(3),
      isLoading: false,
      isError: false,
    });

    renderPanel();

    expect(screen.getByText(/3 SSURGO map units drawn/)).toBeTruthy();
    expect(screen.queryByText(/subset/)).toBeNull();
  });

  it("distinguishes map units it could not read from ground with no soil on it", () => {
    // SDA served rows; none of their WKT parsed. `features: []` with truncated:false and
    // availability:"published" is byte-identical to open ocean, so only the dropped-row
    // count can keep the panel from asserting a coverage claim USDA never made.
    queries.getSoilSurvey.mockReturnValue({
      data: soilSurveyCollection(0, { unreadableGeometries: 3 }),
      isLoading: false,
      isError: false,
    });

    renderPanel();

    expect(screen.getByText(/3 map units this reader could not store/)).toBeTruthy();
    expect(screen.queryByText(/no surveyed SSURGO map units/)).toBeNull();
  });

  it("still reports the drawn map units when only some of them were unreadable", () => {
    queries.getSoilSurvey.mockReturnValue({
      data: soilSurveyCollection(2, { unreadableGeometries: 1 }),
      isLoading: false,
      isError: false,
    });

    renderPanel();

    expect(screen.getByText(/2 SSURGO map units drawn/)).toBeTruthy();
    expect(screen.getByText(/1 map unit this reader could not store/)).toBeTruthy();
  });

  it("names ground nobody has fetched as missing coverage, not as an absence of soil", () => {
    // The dishonest-empty case persistence introduced. Without this the response below --
    // no features, nothing truncated, nothing unreadable, availability "published" -- is
    // byte-identical to a viewport USDA surveyed and found nothing in.
    queries.getSoilSurvey.mockReturnValue({
      data: soilSurveyCollection(0, { coverage: { cells: 4, covered: 1, ingested: 0 } }),
      isLoading: false,
      isError: false,
    });

    renderPanel();

    expect(screen.getByText(/3 of 4 grid cells in this view have not been loaded/)).toBeTruthy();
  });

  it("claims no coverage gap for a fully covered view", () => {
    queries.getSoilSurvey.mockReturnValue({
      data: soilSurveyCollection(2, { coverage: { cells: 2, covered: 2, ingested: 1 } }),
      isLoading: false,
      isError: false,
    });

    renderPanel();

    expect(screen.queryByText(/have not been loaded from USDA/)).toBeNull();
  });

  it("claims no coverage gap for a response that predates persistence", () => {
    // `coverage` absent is a fixture or an older client cache entry, not a gap.
    queries.getSoilSurvey.mockReturnValue({
      data: soilSurveyCollection(2),
      isLoading: false,
      isError: false,
    });

    renderPanel();

    expect(screen.queryByText(/have not been loaded from USDA/)).toBeNull();
  });

  it("makes no unreadable-geometry claim when every map unit parsed", () => {
    queries.getSoilSurvey.mockReturnValue({
      data: soilSurveyCollection(3),
      isLoading: false,
      isError: false,
    });

    renderPanel();

    expect(screen.queryByText(/could not be read/)).toBeNull();
  });

  it("neither queries nor describes coverage while the survey toggle is off", () => {
    useMapStore.setState({ activeLayers: [] });

    renderPanel();

    // The map is not asking either, so the panel must not add an upstream request of
    // its own -- it reads the same react-query entry LayerManager fills.
    expect(enabledFlagOf(queries.getSoilSurvey)).toBe(false);
    expect(screen.queryByText(/SSURGO map units/)).toBeNull();
  });

  it("asks for the viewport it was handed, on the shared feed's own staleTime", () => {
    renderPanel();

    const [input, options] = queries.getSoilSurvey.mock.calls.at(-1) as unknown as [
      { bbox: string },
      { enabled: boolean; staleTime: number; retry: number },
    ];
    // The bbox is the dock's, not a third derivation of the map store.
    expect(input.bbox).toBe(VIEWPORT_BBOX);
    expect(options.enabled).toBe(true);
    expect(options.staleTime).toBe(24 * 60 * 60 * 1000);
    expect(options.retry).toBe(1);
  });

  it("does not query without a viewport to ask about", () => {
    renderWithProviders(<SoilDetails />);

    expect(enabledFlagOf(queries.getSoilSurvey)).toBe(false);
  });

  it("passes zoom through to the shared survey query, so the map and panel never split", () => {
    renderWithProviders(
      <SoilDetails bbox={VIEWPORT_BBOX} zoom={9} />
    );

    const [input] = queries.getSoilSurvey.mock.calls.at(-1) as unknown as [
      { bbox: string; zoom?: number },
    ];
    expect(input.zoom).toBe(9);
  });

  it("labels a zoomed-out average as an average, not a surveyed map unit", () => {
    queries.getSoilSurvey.mockReturnValue({
      data: {
        ...soilSurveyCollection(1),
        granularity: "regional-average",
        features: [
          {
            type: "Feature" as const,
            geometry: { type: "Polygon" as const, coordinates: [] },
            properties: { aggregated: true, drainageClass: "well-drained", mapUnitCount: 7 },
          },
        ],
      },
      isLoading: false,
      isError: false,
    });

    renderPanel();

    expect(screen.getByText(/showing drainage-class averages built from 7 real/)).toBeTruthy();
    expect(screen.getByText(/averaged drainage region/)).toBeTruthy();
    expect(screen.queryByText(/SSURGO map units drawn/)).toBeNull();
  });

  it("keeps the original detail-tier wording when the response carries no granularity", () => {
    // Every fixture above omits `granularity` -- the field environmental.ts's owner has
    // not yet added to ProxiedFeatureCollection. Undefined must read as "detail", not
    // trigger the averaged copy.
    queries.getSoilSurvey.mockReturnValue({
      data: soilSurveyCollection(3),
      isLoading: false,
      isError: false,
    });

    renderPanel();

    expect(screen.getByText(/3 SSURGO map units drawn/)).toBeTruthy();
    expect(screen.queryByText(/drainage-class averages/)).toBeNull();
  });
});

/**
 * A retained answer must not be stated as the current one, and the loading line must still fire.
 *
 * Both reads here hold the previous answer while the next loads (`keepPreviousData`), which sets
 * `status: "success"`: `isLoading` is therefore permanently false after the first success, so the
 * two "Loading … for this view…" lines -- keyed on it until 2026-08-16 -- never appeared again
 * for any later viewport. Meanwhile every figure below them is measured on the response in hand,
 * which during that window describes the PREVIOUS view. Pan Boise to Portland and the section
 * read "412 SSURGO map units drawn for this view" for the length of a USDA round trip, with no
 * loading line: the same defect the map lane refused to introduce into the watershed LIST.
 */
describe("SoilDetails retained answers", () => {
  it("shows the survey loading line on a refetch, not only on the very first load", () => {
    queries.getSoilSurvey.mockReturnValue({
      data: soilSurveyCollection(3),
      // The shape after a first success: a fetch is open, and `isLoading` is false because
      // `status` already reads "success".
      isLoading: false,
      isError: false,
      isFetching: true,
      isPlaceholderData: false,
    });

    renderPanel();

    expect(screen.getByText(/Loading the USDA soil survey for this view/)).toBeTruthy();
  });

  it("says the survey figures describe the previous view while a retained answer stands", () => {
    queries.getSoilSurvey.mockReturnValue({
      data: soilSurveyCollection(412),
      isLoading: false,
      isError: false,
      isFetching: true,
      isPlaceholderData: true,
    });

    renderPanel();

    expect(
      screen.getByText(/survey figures below describe the previous view/)
    ).toBeTruthy();
    // The count is still shown rather than blanked -- blanking is the defect retention fixed --
    // but it is no longer the only thing said about it.
    expect(screen.getByText(/412 SSURGO map units drawn for this view/)).toBeTruthy();
  });

  it("does not claim a retained view while nothing is retained", () => {
    queries.getSoilSurvey.mockReturnValue({
      data: soilSurveyCollection(412),
      isLoading: false,
      isError: false,
      isFetching: false,
      isPlaceholderData: false,
    });

    renderPanel();

    expect(screen.queryByText(/describe the previous view/)).toBeNull();
    expect(screen.queryByText(/Loading the USDA soil survey/)).toBeNull();
  });
});

describe("SoilDetails property selector", () => {
  const QUERY_POINT = { lat: 43.55, lon: -116.3 };

  function renderWithPoint() {
    return renderWithProviders(
      <SoilDetails bbox={VIEWPORT_BBOX} queryPoint={QUERY_POINT} />
    );
  }

  beforeEach(() => {
    useSoilStore.setState({ property: "soc" });
    queries.getSoilProperties.mockReturnValue({
      data: {
        ph: 6.4,
        organicCarbon: 12.3,
        nitrogen: 1.1,
        bulkDensity: 1.35,
        cec: 18.2,
        ocd: 4.6,
      },
      isLoading: false,
      isError: false,
    });
  });

  it("highlights the queried-point row matching the selected property", () => {
    useSoilStore.setState({ property: "phh2o" });

    renderWithPoint();

    // pH is selected: its row carries the emphasized styling the others do not.
    const phValue = screen.getByText("6.4");
    expect(phValue.className).toContain("text-[hsl(var(--primary))]");
    const nitrogenValue = screen.getByText("1.10 g/kg");
    expect(nitrogenValue.className).not.toContain("text-[hsl(var(--primary))]");
  });

  it("moves the highlight when a different property is selected", () => {
    useSoilStore.setState({ property: "cec" });

    renderWithPoint();

    expect(screen.getByText("18.2 cmol/kg").className).toContain(
      "text-[hsl(var(--primary))]"
    );
    expect(screen.getByText("6.4").className).not.toContain("text-[hsl(var(--primary))]");
  });

  it("still shows every point-query field regardless of selection -- nothing is hidden", () => {
    useSoilStore.setState({ property: "ocd" });

    renderWithPoint();

    expect(screen.getByText("6.4")).toBeTruthy();
    expect(screen.getByText("12.3 g/kg")).toBeTruthy();
    expect(screen.getByText("1.10 g/kg")).toBeTruthy();
    expect(screen.getByText("1.35 g/cm³")).toBeTruthy();
    expect(screen.getByText("18.2 cmol/kg")).toBeTruthy();
    expect(screen.getByText("4.6 kg/m³")).toBeTruthy();
  });
});

/**
 * Both ERA5-Land soil fields publish three things the map cannot say on its own: that a
 * zoomed-out view is a smoothed average rather than measured cells, that the day drawn is
 * not always the day asked for (the archive ends before the live edge), and that an empty
 * view is missing coverage rather than a low reading.
 *
 * The cases below drive the MOISTURE field because it is the one with a complete backfill;
 * they exercise the shared `SoilFieldSection`, so temperature inherits every one. The
 * temperature-specific block after them pins what actually differs.
 */
describe("SoilDetails soil-moisture field", () => {
  /** A published collection carrying only what the panel reads. */
  function moistureCollection(
    overrides: Partial<NonNullable<SoilFieldResult["data"]>> = {}
  ): NonNullable<SoilFieldResult["data"]> {
    return {
      type: "FeatureCollection",
      features: [],
      availability: "published",
      reason: null,
      granularity: "detail",
      unit: "m^3/m^3",
      attribution: "ERA5-Land (Copernicus/ECMWF) via Open-Meteo, CC-BY 4.0",
      observedDay: "2026-04-30",
      requestedDay: "2026-04-30",
      newestAvailableDay: null,
      cellCount: 12,
      maxObservationAgeDays: 30,
      latticeDegrees: null,
      bands: [{ bandIndex: 0, color: "#8c510a", label: "< 0.05" }],
      ...overrides,
    };
  }

  function renderWithMoistureOn(
    data: NonNullable<SoilFieldResult["data"]> | undefined,
    zoom = 10
  ) {
    useMapStore.setState({ activeLayers: ["soil-moisture"] });
    queries.getSoilField.mockReturnValue({ data, isLoading: false, isError: false });
    return renderWithProviders(
      <SoilDetails bbox={VIEWPORT_BBOX} zoom={zoom} />
    );
  }

  it("neither queries nor describes the field while the toggle is off", () => {
    useMapStore.setState({ activeLayers: [] });
    renderWithProviders(<SoilDetails bbox={VIEWPORT_BBOX} zoom={10} />);

    expect(soilFieldEnabledFlagOf("moisture")).toBe(false);
    expect(screen.queryByText(/Volumetric soil water/)).toBeNull();
  });

  it("keys on the same zoom and depth the map drew with", () => {
    useSoilStore.setState({
      fieldDepth: { moisture: "deep", temperature: "surface", vpd: "surface" },
    });
    renderWithMoistureOn(moistureCollection(), 6);

    const input = soilFieldInputOf("moisture");
    expect(input.bbox).toBe(VIEWPORT_BBOX);
    expect(input.zoom).toBe(6);
    expect(input.depth).toBe("deep");
    expect(input.measure).toBe("moisture");
  });

  it("labels a zoomed-out view as a smoothed average over a coarser lattice", () => {
    renderWithMoistureOn(
      moistureCollection({ granularity: "coarse-average", latticeDegrees: 1, cellCount: 1568 }),
      5
    );

    expect(screen.getByText(/smoothed contours over a/)).toBeTruthy();
    expect(screen.getByText(/not individual/)).toBeTruthy();
  });

  it("makes no average claim at the detail tier, where the cells are the measurements", () => {
    renderWithMoistureOn(moistureCollection({ granularity: "detail" }));

    expect(screen.queryByText(/smoothed contours over a/)).toBeNull();
  });

  it("names both days when the newest reading predates the day asked for", () => {
    renderWithMoistureOn(
      moistureCollection({ observedDay: "2026-04-30", requestedDay: "2026-05-20" })
    );

    expect(screen.getByText(/Drawn for 2026-04-30/)).toBeTruthy();
    expect(screen.getByText(/at or before 2026-05-20/)).toBeTruthy();
  });

  it("says which day to scrub to rather than drawing a field that is too old", () => {
    renderWithMoistureOn(
      moistureCollection({
        availability: "unavailable",
        reason: "stale",
        observedDay: null,
        requestedDay: "2026-08-06",
        newestAvailableDay: "2026-04-30",
      })
    );

    expect(screen.getByText(/Scrub the time slider to 2026-04-30/)).toBeTruthy();
  });

  it("calls an uncovered view missing coverage, not dry soil", () => {
    renderWithMoistureOn(
      moistureCollection({ availability: "unavailable", reason: "not_published", observedDay: null })
    );

    expect(screen.getByText(/not dry soil/)).toBeTruthy();
  });

  it("refuses a future day instead of drawing the newest reading under it", () => {
    renderWithMoistureOn(
      moistureCollection({
        availability: "unavailable",
        reason: "not_forecastable",
        observedDay: null,
        requestedDay: "2027-01-01",
      })
    );

    expect(screen.getByText(/is in the future/)).toBeTruthy();
    expect(screen.getByText(/nothing may be invented/)).toBeTruthy();
  });

  it("publishes the licence attribution wherever the values are drawn", () => {
    renderWithMoistureOn(moistureCollection());

    expect(screen.getByText(/CC-BY 4.0/)).toBeTruthy();
  });

  it("offers a depth selector and writes the chosen depth to the store", () => {
    renderWithMoistureOn(moistureCollection());

    act(() => screen.getByText("Root zone (7-28 cm)").click());
    expect(useSoilStore.getState().fieldDepth.moisture).toBe("root-zone");
  });
});

/**
 * Soil temperature rides the same section, so the honesty notes above already cover it.
 * What is specific to it: a fourth ECMWF depth the moisture lane does not fetch, its own
 * unit and legend heading, its own store slot, and a blank-ground caption that says
 * something true about a temperature field rather than about a dry one.
 */
describe("SoilDetails soil-temperature field", () => {
  function temperatureCollection(
    overrides: Partial<NonNullable<SoilFieldResult["data"]>> = {}
  ): NonNullable<SoilFieldResult["data"]> {
    return {
      type: "FeatureCollection",
      features: [],
      availability: "published",
      reason: null,
      granularity: "detail",
      unit: "C",
      attribution: "ERA5-Land (Copernicus/ECMWF) via Open-Meteo, CC-BY 4.0",
      observedDay: "2026-04-30",
      requestedDay: "2026-04-30",
      newestAvailableDay: null,
      cellCount: 9,
      maxObservationAgeDays: 30,
      latticeDegrees: null,
      bands: [{ bandIndex: 0, color: "#4575b4", label: "< -5" }],
      ...overrides,
    };
  }

  function renderWithTemperatureOn(
    data: NonNullable<SoilFieldResult["data"]> | undefined,
    zoom = 10
  ) {
    useMapStore.setState({ activeLayers: ["soil-temperature"] });
    queries.getSoilField.mockReturnValue({ data, isLoading: false, isError: false });
    return renderWithProviders(
      <SoilDetails bbox={VIEWPORT_BBOX} zoom={zoom} />
    );
  }

  it("asks for the temperature measure, not moisture, on its own toggle", () => {
    renderWithTemperatureOn(temperatureCollection());

    expect(soilFieldEnabledFlagOf("temperature")).toBe(true);
    expect(soilFieldEnabledFlagOf("moisture")).toBe(false);
    expect(soilFieldInputOf("temperature").measure).toBe("temperature");
  });

  it("offers the fourth ECMWF depth the moisture lane does not fetch", () => {
    renderWithTemperatureOn(temperatureCollection());

    act(() => screen.getByText("Substratum (100-255 cm)").click());
    expect(useSoilStore.getState().fieldDepth.temperature).toBe("substratum");
    // The moisture slot is untouched: the depths are per measure, not one shared selection.
    expect(useSoilStore.getState().fieldDepth.moisture).toBe("surface");
  });

  it("legends the field in its own unit", () => {
    renderWithTemperatureOn(temperatureCollection());

    expect(screen.getByText(/Soil temperature/)).toBeTruthy();
    expect(screen.queryByText(/Volumetric soil water/)).toBeNull();
  });

  // The temperature backfill is still filling cells, so an uncovered view is the COMMON
  // case here, not an edge one. Captioning it with moisture's wording would tell the reader
  // that measured ground is dry.
  it("calls an uncovered view missing coverage, not cold soil", () => {
    renderWithTemperatureOn(
      temperatureCollection({
        availability: "unavailable",
        reason: "not_published",
        observedDay: null,
      })
    );

    expect(screen.getByText(/not cold soil/)).toBeTruthy();
    expect(screen.queryByText(/not dry soil/)).toBeNull();
  });
});

/**
 * `queryPoint` drove the point query since the panel was written and nothing ever passed
 * one. These pin the reachable half: the instruction is stated when there is no point, and
 * the pin is clearable from the panel when there is.
 */
describe("SoilDetails queried point", () => {
  it("tells the user how to pick a point, and how to clear it", () => {
    renderWithProviders(<SoilDetails bbox={VIEWPORT_BBOX} />);

    expect(screen.getByText(/Click anywhere on the map to query soil properties/)).toBeTruthy();
    expect(screen.getByText(/press Escape, to clear it/)).toBeTruthy();
    expect(screen.queryByText("Clear queried point")).toBeNull();
  });

  it("clears the pin through the handler the dock's Soil section supplied", () => {
    const onClearQueryPoint = vi.fn();
    renderWithProviders(
      <SoilDetails
        bbox={VIEWPORT_BBOX}
        queryPoint={{ lat: 43.6, lon: -116.2 }}
        onClearQueryPoint={onClearQueryPoint}
      />
    );

    act(() => screen.getByText("Clear queried point").click());
    expect(onClearQueryPoint).toHaveBeenCalledTimes(1);
  });
});
