import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import { renderWithProviders } from "@/test/utils";
import { useMapStore } from "@/stores/map-store";
import { useClimateStore } from "@/stores/climate-store";
import {
  CLIMATE_FIELD_SIGNALS,
  type ClimateFieldSignalId,
  type ClimateRenderForm,
} from "@/lib/environmental/climate-field";

/**
 * The Climate section of the map dock is the only surface that can say what the drawn field
 * actually IS: which day answered, which rung served it, how much of the view carries a
 * measurement and at what cell size. The map itself can show a nearly blank canvas for four
 * different reasons -- a stale request, an unpublished lane, a truncated read, and genuine
 * blank ground -- and every one of them looks the same on the map. These cases pin that the
 * panel tells them apart.
 *
 * tRPC is stubbed rather than driven over a link, the same rationale as `SoilDetails.test.tsx`.
 */
type ClimateFieldResult = {
  data:
    | (GeoJSON.FeatureCollection & {
        availability: "published" | "unavailable";
        reason: "not_published" | "stale" | "not_forecastable" | null;
        signal: ClimateFieldSignalId;
        variant: string;
        unit: string;
        attribution: string;
        observedDay: string | null;
        requestedDay: string;
        newestAvailableDay: string | null;
        cellCount: number;
        latticeCellCount: number;
        maxObservationAgeDays: number;
        renderForm: ClimateRenderForm;
        truncated: boolean;
        bands: { bandIndex: number; color: string; label: string }[];
        /** The one rung that answered; the caption names it rather than inferring it. */
        zoomTier: 0 | 5 | 9 | 13;
        /** Only the field the caption reads is declared, as in `SoilDetails.test.tsx`. */
        support: { cellWidthDegrees?: number };
      })
    | undefined;
  isFetching?: boolean;
  isPlaceholderData?: boolean;
  isError?: boolean;
};

const queries = vi.hoisted(() => ({
  // Declared with no parameters, the way the sibling panel suites do it: both arguments still
  // land on `mock.calls`, which is where the query input is read from.
  getClimateField: vi.fn((): ClimateFieldResult => ({ data: undefined })),
}));

vi.mock("@/lib/trpc/client", () => ({
  trpc: {
    environmental: { getClimateField: { useQuery: queries.getClimateField } },
  },
}));

import { ClimateDetails } from "@/components/panels/ClimateDetails";

const BBOX = "-125,42,-116,49";
const AIR_TEMPERATURE = CLIMATE_FIELD_SIGNALS["air-temperature"];

/** A landed collection for one signal, with only the fields the panel reads spelled out. */
function collection(
  overrides: Partial<NonNullable<ClimateFieldResult["data"]>> = {}
): NonNullable<ClimateFieldResult["data"]> {
  return {
    type: "FeatureCollection",
    features: [],
    availability: "published",
    reason: null,
    signal: "air-temperature",
    variant: "mean",
    unit: AIR_TEMPERATURE.unit,
    attribution: "NASA POWER (NASA LaRC)",
    observedDay: "2026-08-30",
    requestedDay: "2026-08-30",
    newestAvailableDay: null,
    cellCount: 41,
    latticeCellCount: 60,
    maxObservationAgeDays: 0,
    renderForm: "field",
    truncated: false,
    bands: [{ bandIndex: 0, color: "#123456", label: "10 – 15 °C" }],
    zoomTier: 13,
    support: { cellWidthDegrees: 1 },
    ...overrides,
  };
}

const INITIAL_MAP_STATE = useMapStore.getState();
const INITIAL_CLIMATE_STATE = useClimateStore.getState();

beforeEach(() => {
  useMapStore.setState(INITIAL_MAP_STATE, true);
  useClimateStore.setState(INITIAL_CLIMATE_STATE, true);
  // The panel describes only the rows the map is drawing; with every row off it renders the
  // "switch one on" line and nothing else.
  useMapStore.setState({ activeLayers: [AIR_TEMPERATURE.toggleId] });
  queries.getClimateField.mockReturnValue({ data: undefined });
});

afterEach(() => {
  vi.clearAllMocks();
  useMapStore.setState(INITIAL_MAP_STATE, true);
  useClimateStore.setState(INITIAL_CLIMATE_STATE, true);
});

function renderPanel(zoom = 13) {
  return renderWithProviders(<ClimateDetails bbox={BBOX} zoom={zoom} />);
}

/**
 * The input the panel last asked the climate procedure for.
 *
 * Takes the mock loosely typed, the way `SoilDetails.test.tsx` and `LayerManager.test.tsx` do:
 * the stub above declares no parameters, so its recorded `calls` are an empty tuple to the
 * compiler even though both arguments are really there at runtime.
 */
function climateFieldInput(): { renderForm?: string; signal?: string } | undefined {
  const calls = queries.getClimateField.mock.calls as unknown as unknown[][];
  return calls.at(-1)?.[0] as { renderForm?: string; signal?: string } | undefined;
}

describe("ClimateDetails coverage caption", () => {
  it("names the served rung and the cell size from the response that drew the cells", () => {
    // Both numbers and the pitch come from the SAME response. A denominator or a cell size that
    // is not measured beside its numerator will always eventually lie -- this caption's
    // predecessor hard-coded "the lane's 397 cells" and contradicted the live count on screen.
    queries.getClimateField.mockReturnValue({ data: collection({ zoomTier: 9, cellCount: 12 }) });

    renderPanel(9);

    expect(screen.getByText(/12 of the 60/)).toBeTruthy();
    // One degree at z9 and five at z0 are the same sentence with a hundredfold difference in
    // what it claims about the ground, so the size is named rather than left to be inferred.
    expect(screen.getByText(/1° cells in this view/)).toBeTruthy();
    expect(
      screen.getByText(/Served from the z9 rung, which averages every measurement/)
    ).toBeTruthy();
  });

  it("says nothing about a rung when the base rung answered", () => {
    // The base rung aggregates nothing, so the sentence that explains averaging would be false.
    queries.getClimateField.mockReturnValue({ data: collection({ zoomTier: 13 }) });

    renderPanel(13);

    expect(screen.getByText(/41 of the 60/)).toBeTruthy();
    expect(screen.queryByText(/Served from the z/)).toBeNull();
  });
});

describe("ClimateDetails notices", () => {
  it("distinguishes a stale lane from blank ground and names the day to scrub to", () => {
    queries.getClimateField.mockReturnValue({
      data: collection({
        availability: "unavailable",
        reason: "stale",
        observedDay: null,
        requestedDay: "2026-08-30",
        newestAvailableDay: "2026-06-01",
        cellCount: 0,
      }),
    });

    renderPanel();

    // The actionable half is what makes this different from an outage: the reader is told which
    // day the archive does answer for, not merely that this one is empty.
    expect(screen.getByText(/Nothing is drawn for 2026-08-30/)).toBeTruthy();
    expect(screen.getByText(/2026-06-01/)).toBeTruthy();
    // ...and a stale answer carries no band legend, because nothing was drawn to legend.
    expect(screen.queryByText(/cells in this view carry a measurement/)).toBeNull();
  });

  it("says an unpublished view is missing coverage on our side, not the thing itself", () => {
    // Blank ground on a climate field is the misreading the coverage note exists to prevent: a
    // reader must not take an unfilled lane for a measured absence of the quantity.
    queries.getClimateField.mockReturnValue({
      data: collection({
        availability: "unavailable",
        reason: "not_published",
        observedDay: null,
        cellCount: 0,
      }),
    });

    renderPanel();

    expect(screen.getByText(/does not cover this view/)).toBeTruthy();
    expect(
      screen.getByText(new RegExp(AIR_TEMPERATURE.blankGroundMisreading))
    ).toBeTruthy();
  });

  it("reports a truncated read as part of the view rather than as the view", () => {
    queries.getClimateField.mockReturnValue({
      data: collection({ truncated: true, cellCount: 500 }),
    });

    renderPanel();

    expect(screen.getByText(/Showing 500 of the cells in this view/)).toBeTruthy();
  });

  it("names both days when the archive's newest day is not the day asked for", () => {
    queries.getClimateField.mockReturnValue({
      data: collection({ observedDay: "2026-08-25", requestedDay: "2026-08-30" }),
    });

    renderPanel();

    // A field silently drawn from days ago while the slider reads today is a lie the map cannot
    // tell on its own, so the panel names both dates.
    expect(
      screen.getByText(/Drawn for 2026-08-25, the newest reading at or before 2026-08-30/)
    ).toBeTruthy();
  });

  it("says the served form outranked the requested one rather than letting the picker read broken", () => {
    // The picker still shows the requested chip selected, so without this line the control looks
    // like it failed. `dew-point` is the signal that can ask for a contour and be refused one.
    useMapStore.setState({ activeLayers: [CLIMATE_FIELD_SIGNALS["dew-point"].toggleId] });
    queries.getClimateField.mockReturnValue({
      data: collection({ signal: "dew-point", renderForm: "field" }),
    });

    renderPanel(13);

    expect(screen.getByText(/Drawn as filled at this zoom/)).toBeTruthy();
    expect(screen.getByText(/zoom out for the requested contours form/)).toBeTruthy();
  });
});

describe("ClimateDetails form picker", () => {
  it("offers a signal both of the forms it declares, and marks the one in force", () => {
    useMapStore.setState({ activeLayers: [CLIMATE_FIELD_SIGNALS["dew-point"].toggleId] });

    renderPanel();

    // Dew point declares `isoline` first and `field` second, so both chips are offered and the
    // head of its own list is what the row opens on.
    expect(screen.getByRole("button", { name: "Contours" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Filled" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Points" })).toBeNull();
  });

  it("draws no picker at all for a signal that declares one form", () => {
    // Precipitation offered `symbol` and `field` and DEFAULTED to points until 2026-09-02; the
    // frozen render contract permits no point form for a continuous field, so `symbol` was
    // withdrawn from every signal and this row has nothing left to pick between. A one-entry
    // list must render no picker rather than a control with a single, un-unselectable chip.
    useMapStore.setState({
      activeLayers: [CLIMATE_FIELD_SIGNALS.precipitation.toggleId],
    });

    renderPanel();

    expect(screen.queryByRole("button", { name: "Filled" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Points" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Contours" })).toBeNull();
  });

  it("asks for the filled field when the store still names the retired points form", () => {
    // A persisted store written before 2026-09-02 names `symbol` for precipitation, and every
    // replayed cache entry does too. `field` is its honest successor -- one mark per measured
    // cell either way -- so the request must carry that rather than a form no signal offers.
    useMapStore.setState({
      activeLayers: [CLIMATE_FIELD_SIGNALS.precipitation.toggleId],
    });
    useClimateStore.setState({ renderForms: { precipitation: "symbol" } });

    renderPanel();

    const input = climateFieldInput();
    expect(input?.signal).toBe("precipitation");
    expect(input?.renderForm).toBe("field");
  });
});

describe("ClimateDetails with nothing switched on", () => {
  it("describes no legend for a layer nobody is drawing", () => {
    useMapStore.setState({ activeLayers: [] });

    renderPanel();

    expect(screen.getByText(/Switch on a climate layer above/)).toBeTruthy();
  });
});
