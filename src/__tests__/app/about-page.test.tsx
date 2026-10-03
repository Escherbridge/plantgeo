import { describe, expect, it } from "vitest";
import { render } from "@testing-library/react";

import AboutPage from "@/app/about/page";

/**
 * One attribution row of the About page, read back from the rendered definition list.
 * Keyed by the upstream's name so an assertion is about one source's public claim.
 */
interface AttributionRow {
  description: string;
  note: string | null;
}

/**
 * Reads §06 "Where the data comes from" as a term -> row map.
 *
 * The page keeps its rows in a module-private array (a Next.js page may only export the
 * route contract), so the rendered `<dl>` is the one place the claims can be read from.
 */
function renderAttributionRows(): Map<string, AttributionRow> {
  const { container } = render(<AboutPage />);
  const section = container.querySelector("#attribution");
  if (!(section instanceof HTMLElement)) {
    throw new Error("About page renders no #attribution section");
  }
  const rows = new Map<string, AttributionRow>();
  for (const dt of Array.from(section.querySelectorAll("dl dt"))) {
    const row = dt.parentElement;
    const dd = row?.querySelector("dd");
    const note = row?.querySelector("dd + div");
    rows.set(dt.textContent?.trim() ?? "", {
      description: dd?.textContent?.trim() ?? "",
      note: note?.textContent?.trim() ?? null,
    });
  }
  return rows;
}

describe("About page — data attribution claims", () => {
  // Every claim in §06 must have a user-reachable path behind it. The three rows below were
  // audited against the tRPC router, the layer registry and the lane schedule on 2026-09-15;
  // these assertions pin what that audit found so the list cannot quietly promise more again.

  it("does not name LANDFIRE anywhere: nothing on the platform reads it", () => {
    const { container } = render(<AboutPage />);
    expect(container.textContent).not.toMatch(/landfire/i);
  });

  it("reports SSURGO as a static, admitted release rather than queried on request", () => {
    const row = renderAttributionRows().get("USDA NRCS SSURGO");
    expect(row).toBeDefined();
    // `environmental.getSoilSurvey` reads a captured, pinned SSURGO release
    // (`agri-data-service`'s soil-survey route) -- no code path calls USDA Soil Data Access
    // live on a request, so "on request" is not a true note, and the wording must stay honest
    // in both states: before ANY release is admitted (unavailable, every viewport) and after.
    expect(row?.note).toBe("Static release");
    // NOT asserted: `.not.toMatch(/soil data access/i)`. The honest copy names the real USDA
    // product the release is captured from -- "a captured, admitted USDA Soil Data Access
    // release" -- so the forbidden-phrase check was self-contradicting with the very assertions
    // below it (S4 review finding 3): the phrase is the plan-mandated wording, not a live-query
    // claim, and "nothing is queried"/"admitted"/"unavailable" are what actually rule that out.
    expect(row?.description).toMatch(/nothing is queried/i);
    expect(row?.description).toMatch(/admitted/i);
    expect(row?.description).toMatch(/unavailable/i);
  });

  it("scopes NHDPlus HR to the HUC12 boundaries that are actually read", () => {
    const row = renderAttributionRows().get("USGS NHDPlus HR");
    expect(row).toBeDefined();
    // hydrosheds.ts queries NHDPlus_HR MapServer layer 12 (WBDHU12) and nothing else; the
    // watersheds Parquet lane walks the same layer. No flowline or waterbody layer is drawn.
    expect(row?.description).toMatch(/HUC12 watershed boundaries/);
    expect(row?.description).toMatch(/flowlines and waterbodies are not drawn/i);
    // The map's basins come from the daily-checked lane; only the Water panel's list is live.
    expect(row?.note).toBe("Daily check, list on request");
  });

  it("describes the Open-Meteo point reading as a published sample near the view centre", () => {
    const row = renderAttributionRows().get("Open-Meteo");
    // wildfire.getWeatherForPoint reads the nearest PUBLISHED warehouse observation and
    // FireDetails keys it on the viewport centre (DockDetails.tsx FireDetailsBody); no code
    // path fetches Open-Meteo for a click.
    expect(row?.description).not.toMatch(/map click/i);
    expect(row?.description).toMatch(/nearest published sample to the centre of the view/);
  });

  it("quotes the Sentinel-2 lane's real cadence: hourly, not daily at 05:00", () => {
    const row = renderAttributionRows().get("Sentinel-2 L2A");
    // job_executor_service.py VEGETATION_DIRECT_LANE_ID schedule="5 * * * *".
    expect(row?.note).toBe("Hourly poll, one product-day per run");
  });

  it("does not flatly claim 'On request' for any row: each live query names what it asks for", () => {
    for (const [term, row] of renderAttributionRows()) {
      expect(row.note, `${term} carries a bare "On request" note`).not.toBe("On request");
    }
  });
});
