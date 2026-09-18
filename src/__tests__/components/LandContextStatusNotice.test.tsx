import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import {
  LandContextStatusNotice,
  deriveLandContextNotices,
  type LandContextNoticeInput,
} from "@/components/map/land-context/LandContextStatusNotice";
import {
  useLandContextStore,
  type LandContextFeature,
  type LandContextGroupId,
} from "@/stores/land-context-store";

/**
 * The toggles must not lie. A lit switch beside an empty canvas has to say WHICH of three
 * things is true, per family: nothing has been clicked yet, no source is admitted for reads
 * yet (in the reader's own words), or there simply is no feature at the clicked point.
 */
const GAP = "no Parquet lane wired in yet; reference plane not yet admitted for reads";

const ALL_OFF: Record<LandContextGroupId, boolean> = {
  "parcels-land-use": false,
  "electric-utility-territories": false,
  "blm-lands": false,
  "state-managed-lands": false,
};

const POINT = { mode: "point" as const, point: [-116.2, 43.6] as [number, number] };

function blmFeature(): LandContextFeature {
  return {
    id: "blm:SMA-1:0",
    group: "blm-lands",
    title: "SMA-1",
    geometry: { type: "GeometryCollection", geometries: [] },
  };
}

function input(overrides: Partial<LandContextNoticeInput>): LandContextNoticeInput {
  return {
    enabledGroups: { ...ALL_OFF },
    selection: null,
    results: [],
    resultMeta: null,
    queryStatus: "idle",
    ...overrides,
  };
}

describe("deriveLandContextNotices", () => {
  it("says nothing while every group is off", () => {
    expect(deriveLandContextNotices(input({ selection: POINT, queryStatus: "settled" }))).toEqual([]);
  });

  it("tells the user selection is click-driven while a group is on and nothing is selected", () => {
    const notices = deriveLandContextNotices(
      input({ enabledGroups: { ...ALL_OFF, "blm-lands": true, "parcels-land-use": true } })
    );
    expect(notices).toHaveLength(1);
    expect(notices[0].layerId).toBe("land-context-select-point");
    expect(notices[0].tone).toBe("notice");
    expect(notices[0].message).toContain("Parcels & land use, BLM lands");
    expect(notices[0].message).toContain("click a point on the map");
    expect(notices[0].message).toContain("never the current view");
    expect(notices[0].message).not.toContain("Electric utility");
  });

  it("stays silent mid-flight so an empty result is never called an absence early", () => {
    const enabledGroups = { ...ALL_OFF, "blm-lands": true };
    expect(
      deriveLandContextNotices(input({ enabledGroups, selection: POINT, queryStatus: "loading" }))
    ).toEqual([]);
    expect(
      deriveLandContextNotices(input({ enabledGroups, selection: POINT, queryStatus: "idle" }))
    ).toEqual([]);
  });

  it("quotes the reader's own gap string when no admitted source answered for a family", () => {
    const notices = deriveLandContextNotices(
      input({
        enabledGroups: { ...ALL_OFF, "blm-lands": true },
        selection: POINT,
        queryStatus: "settled",
        resultMeta: {
          totalCount: 0,
          returnedCount: 0,
          hasMore: false,
          coverageNotices: [{ coverageState: "unknown_coverage", gaps: [GAP] }],
        },
      })
    );
    expect(notices).toHaveLength(1);
    expect(notices[0].layerId).toBe("land-context-coverage-unknown_coverage");
    expect(notices[0].message).toContain("BLM lands");
    expect(notices[0].message).toContain("no admitted source answered");
    expect(notices[0].message).toContain(`Stated gap: ${GAP}.`);
  });

  it("renders an unbound source as a governed absence, never as partial coverage or a fault", () => {
    const notices = deriveLandContextNotices(
      input({
        enabledGroups: { ...ALL_OFF, "blm-lands": true },
        selection: POINT,
        queryStatus: "settled",
        resultMeta: {
          totalCount: 0,
          returnedCount: 0,
          hasMore: false,
          coverageNotices: [{ coverageState: "source_unbound_for_region", gaps: [GAP] }],
        },
      })
    );
    expect(notices).toHaveLength(1);
    expect(notices[0].layerId).toBe("land-context-coverage-source_unbound_for_region");
    // Amber caption tone, not the fault pill: nothing broke, nothing is bound here.
    expect(notices[0].tone).toBe("notice");
    expect(notices[0].message).toContain("not available in this region");
    expect(notices[0].message).toContain(`Stated gap: ${GAP}.`);
  });

  it("names only the families that came back empty, never one that matched", () => {
    const notices = deriveLandContextNotices(
      input({
        enabledGroups: { ...ALL_OFF, "blm-lands": true, "parcels-land-use": true },
        selection: POINT,
        queryStatus: "settled",
        results: [blmFeature()],
        resultMeta: { totalCount: 1, returnedCount: 1, hasMore: false, coverageNotices: [] },
      })
    );
    expect(notices).toHaveLength(1);
    expect(notices[0].layerId).toBe("land-context-no-features");
    expect(notices[0].message).toContain("No Parcels & land use features were returned at the selected point");
    expect(notices[0].message).toContain("coverage for them was not stated");
    expect(notices[0].message).not.toContain("BLM");
  });

  it("says nothing at all once every enabled family has a feature", () => {
    expect(
      deriveLandContextNotices(
        input({
          enabledGroups: { ...ALL_OFF, "blm-lands": true },
          selection: POINT,
          queryStatus: "settled",
          results: [blmFeature()],
          resultMeta: { totalCount: 1, returnedCount: 1, hasMore: false, coverageNotices: [] },
        })
      )
    ).toEqual([]);
  });

  it("distinguishes a proven absence from unknown coverage", () => {
    const notices = deriveLandContextNotices(
      input({
        enabledGroups: { ...ALL_OFF, "state-managed-lands": true },
        selection: { mode: "area", areaPolygon: [[0, 0], [1, 0], [1, 1], [0, 0]] },
        queryStatus: "settled",
        resultMeta: {
          totalCount: 0,
          returnedCount: 0,
          hasMore: false,
          coverageNotices: [{ coverageState: "no_match_in_proven_coverage", gaps: [] }],
        },
      })
    );
    expect(notices[0].layerId).toBe("land-context-coverage-no_match_in_proven_coverage");
    expect(notices[0].message).toContain("No State-managed lands features at the selected area");
    expect(notices[0].message).toContain("real absence");
    expect(notices[0].message).not.toContain("Stated gap");
  });

  it("merges identical coverage states into one line with de-duplicated gaps", () => {
    const notices = deriveLandContextNotices(
      input({
        enabledGroups: { ...ALL_OFF, "blm-lands": true },
        selection: POINT,
        queryStatus: "settled",
        resultMeta: {
          totalCount: 0,
          returnedCount: 0,
          hasMore: false,
          coverageNotices: [
            { coverageState: "unknown_coverage", gaps: [GAP] },
            { coverageState: "unknown_coverage", gaps: [GAP, "second gap"] },
          ],
        },
      })
    );
    expect(notices).toHaveLength(1);
    expect(notices[0].message).toContain(`Stated gap: ${GAP}; second gap.`);
  });

  it("reports a failed lookup as a fault with no fallback", () => {
    const notices = deriveLandContextNotices(
      input({ enabledGroups: { ...ALL_OFF, "blm-lands": true }, selection: POINT, queryStatus: "error" })
    );
    expect(notices).toEqual([
      expect.objectContaining({
        layerId: "land-context-request-failed",
        tone: "fault",
        message: expect.stringContaining("failed before returning a state. No fallback is shown."),
      }),
    ]);
  });

  it("reports a budget refusal as the reason nothing is drawn", () => {
    const notices = deriveLandContextNotices(
      input({
        enabledGroups: { ...ALL_OFF, "electric-utility-territories": true },
        selection: POINT,
        queryStatus: "settled",
        resultMeta: {
          totalCount: 0,
          returnedCount: 0,
          hasMore: false,
          budgetExceeded: { reason: "outside_pilot_states", limit: 3, requested: null },
          coverageNotices: [],
        },
      })
    );
    expect(notices).toHaveLength(1);
    expect(notices[0].layerId).toBe("land-context-budget-exceeded");
    expect(notices[0].message).toContain("Electric utility territories were not looked up");
    expect(notices[0].message).toContain("outside the pilot states");
  });
});

describe("<LandContextStatusNotice />", () => {
  beforeEach(() => {
    useLandContextStore.setState({
      enabledGroups: { ...ALL_OFF },
      selection: null,
      results: [],
      resultMeta: null,
      queryStatus: "idle",
    });
  });

  afterEach(() => {
    cleanup();
  });

  it("renders nothing while every group is off", () => {
    render(<LandContextStatusNotice />);
    expect(screen.queryByTestId("land-context-notices")).toBeNull();
  });

  it("renders the click-driven hint as a polite status once a group is on", () => {
    useLandContextStore.setState({ enabledGroups: { ...ALL_OFF, "blm-lands": true } });
    render(<LandContextStatusNotice />);
    const notice = screen.getByTestId("land-context-notice-land-context-select-point");
    expect(notice.getAttribute("role")).toBe("status");
    expect(notice.textContent).toContain("BLM lands: click a point on the map");
    // Each pill is its own live region; a live container would double-announce.
    expect(screen.getByTestId("land-context-notices").hasAttribute("aria-live")).toBe(false);
  });

  it("renders the reader's gap string per family after a settled empty answer", () => {
    useLandContextStore.setState({
      enabledGroups: { ...ALL_OFF, "parcels-land-use": true },
      selection: POINT,
      queryStatus: "settled",
      resultMeta: {
        totalCount: 0,
        returnedCount: 0,
        hasMore: false,
        coverageNotices: [{ coverageState: "unknown_coverage", gaps: [GAP] }],
      },
    });
    render(<LandContextStatusNotice />);
    const notice = screen.getByTestId("land-context-notice-land-context-coverage-unknown_coverage");
    expect(notice.textContent).toContain("Parcels & land use");
    expect(notice.textContent).toContain(GAP);
  });

  it("renders a failed lookup as an alert", () => {
    useLandContextStore.setState({
      enabledGroups: { ...ALL_OFF, "blm-lands": true },
      selection: POINT,
      queryStatus: "error",
    });
    render(<LandContextStatusNotice />);
    expect(
      screen.getByTestId("land-context-notice-land-context-request-failed").getAttribute("role")
    ).toBe("alert");
  });
});
