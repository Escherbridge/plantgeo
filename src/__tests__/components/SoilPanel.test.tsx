import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import { renderWithProviders } from "@/test/utils";
import { useMapStore } from "@/stores/map-store";
import { useSoilStore } from "@/stores/soil-store";

/**
 * `environmental.getSoilSurvey` reports three different things through one shape:
 * a complete view, a view USDA truncated at its row ceiling, and a view USDA failed to
 * answer at all. The last two both arrive with polygons the map cannot distinguish from
 * surveyed-and-empty ground, so the panel is the only surface that can tell them apart --
 * these cases pin that it does. tRPC is stubbed rather than driven over a link, same
 * rationale as CommunityPanel.test.tsx.
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
      })
    | undefined;
  isLoading: boolean;
  isError: boolean;
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
}));

vi.mock("@/lib/trpc/client", () => ({
  trpc: {
    environmental: {
      getSoilProperties: { useQuery: queries.getSoilProperties },
      getInterventionSuitability: { useQuery: queries.getInterventionSuitability },
      getSoilSurvey: { useQuery: queries.getSoilSurvey },
    },
  },
}));

import { SoilPanel } from "@/components/panels/SoilPanel";

/** A collection carrying `count` throwaway map-unit polygons. */
function soilSurveyCollection(
  count: number,
  overrides: {
    truncated?: boolean;
    availability?: "published" | "unavailable";
    reason?: string;
    unreadableGeometries?: number;
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
});

afterEach(() => {
  vi.clearAllMocks();
  useMapStore.setState(INITIAL_MAP_STATE, true);
  useSoilStore.setState(INITIAL_SOIL_STATE, true);
});

/** The viewport PanelManager hands down; without one the survey query stays disabled. */
const VIEWPORT_BBOX = "-116.35,43.5,-116.25,43.6";

function renderPanel() {
  return renderWithProviders(
    <SoilPanel open onOpenChange={() => {}} bbox={VIEWPORT_BBOX} />
  );
}

describe("SoilPanel SSURGO coverage", () => {
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

    expect(screen.getByText(/surveyed more map units than this view draws/)).toBeTruthy();
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

    expect(screen.getByText(/3 map units whose boundary could not be read/)).toBeTruthy();
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
    expect(screen.getByText(/1 map unit whose boundary could not be read/)).toBeTruthy();
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
    // The bbox is PanelManager's, not a third derivation of the map store.
    expect(input.bbox).toBe(VIEWPORT_BBOX);
    expect(options.enabled).toBe(true);
    expect(options.staleTime).toBe(24 * 60 * 60 * 1000);
    expect(options.retry).toBe(1);
  });

  it("does not query without a viewport to ask about", () => {
    renderWithProviders(<SoilPanel open onOpenChange={() => {}} />);

    expect(enabledFlagOf(queries.getSoilSurvey)).toBe(false);
  });

  it("passes zoom through to the shared survey query, so the map and panel never split", () => {
    renderWithProviders(
      <SoilPanel open onOpenChange={() => {}} bbox={VIEWPORT_BBOX} zoom={9} />
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

describe("SoilPanel property selector", () => {
  const QUERY_POINT = { lat: 43.55, lon: -116.3 };

  function renderWithPoint() {
    return renderWithProviders(
      <SoilPanel open onOpenChange={() => {}} bbox={VIEWPORT_BBOX} queryPoint={QUERY_POINT} />
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
