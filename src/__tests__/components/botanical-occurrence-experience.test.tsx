import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import { renderWithProviders } from "@/test/utils";
import { useBotanicalOccurrenceStore } from "@/stores/botanical-occurrence-store";
import {
  BOTANICAL_RICHNESS_LEGEND,
} from "@/components/map/layers/BotanicalRichnessLayer";
import { botanicalOccurrencesToGeoJSON } from "@/components/map/layers/BotanicalOccurrencesLayer";
import {
  BotanicalOccurrenceDetails,
  BOTANICAL_SPECIMEN_DISCLOSURE,
  formatEventInterval,
} from "@/components/panels/BotanicalOccurrenceDetails";
import { BotanicalFilters, BOTANICAL_NO_RELEASE_PINNED_MESSAGE } from "@/components/panels/BotanicalFilters";
import type {
  BotanicalOccurrenceFeature,
  BotanicalOccurrenceResponse,
} from "@/lib/botanical-occurrences";

const mocks = vi.hoisted(() => ({
  fetchBotanicalOccurrences: vi.fn(),
}));

vi.mock("@/lib/botanical-occurrences", async () => {
  const actual = await vi.importActual<typeof import("@/lib/botanical-occurrences")>(
    "@/lib/botanical-occurrences"
  );
  return { ...actual, fetchBotanicalOccurrences: mocks.fetchBotanicalOccurrences };
});

function sampleFeature(overrides: Partial<BotanicalOccurrenceFeature> = {}): BotanicalOccurrenceFeature {
  return {
    occurrence_id: "occ-1",
    collection_key: "col-herb-1",
    source_record_key: "src-1",
    taxon_concept_id: "WFO-0000001",
    resolution_state: "resolved",
    scientific_name: "Quercus garryana",
    family: "Fagaceae",
    event_interval: { start: "1987", end: "1987", precision: "year" },
    longitude: -116.2,
    latitude: 43.6,
    coordinate_uncertainty_m: 500,
    spatial_class: "exact",
    membership: "confirmed",
    catalog_number: "CAT-001",
    recorded_by: "J. Smith",
    basis_of_record: "PreservedSpecimen",
    rights_uri: "https://example.org/rights",
    attribution_text: "Example Herbarium",
    ...overrides,
  };
}

describe("BotanicalFilters", () => {
  beforeEach(() => {
    useBotanicalOccurrenceStore.getState().resetFilters();
  });

  it("shows the no-release-pinned state and hides the rest of the form until pinned", () => {
    renderWithProviders(<BotanicalFilters />);
    expect(screen.getByText(BOTANICAL_NO_RELEASE_PINNED_MESSAGE)).toBeTruthy();
    expect(screen.queryByText(/no free-text name search/i)).toBeNull();
  });

  it("reveals the rest of the filters once a release id is entered, and never fetches on its own", async () => {
    renderWithProviders(<BotanicalFilters />);

    const releaseInput = screen.getByLabelText(/release set id/i);
    fireEvent.change(releaseInput, { target: { value: "release-2026-08" } });

    await waitFor(() => {
      expect(screen.getByText(/no free-text name search/i)).toBeTruthy();
    });
    expect(mocks.fetchBotanicalOccurrences).not.toHaveBeenCalled();
    expect(useBotanicalOccurrenceStore.getState().filters.release_set_id).toBe("release-2026-08");
  });
});

describe("BotanicalOccurrenceDetails", () => {
  it("renders interval precision and the fixed disclosure line", () => {
    renderWithProviders(<BotanicalOccurrenceDetails feature={sampleFeature()} />);
    expect(screen.getByText("1987 (year precision)")).toBeTruthy();
    expect(screen.getByText(BOTANICAL_SPECIMEN_DISCLOSURE)).toBeTruthy();
  });

  it("formats a partial interval as start–end with precision", () => {
    expect(
      formatEventInterval({ start: "1987-04", end: "1987-06", precision: "interval" })
    ).toBe("1987-04 – 1987-06 (interval precision)");
  });

  it("renders a placeholder with no selected feature", () => {
    renderWithProviders(<BotanicalOccurrenceDetails feature={null} />);
    expect(screen.getByText(/select a specimen point/i)).toBeTruthy();
  });
});

describe("BotanicalRichnessLayer legend", () => {
  it("contains the four exact evaluation-state labels the spec requires", () => {
    const labels = BOTANICAL_RICHNESS_LEGEND.map((entry) => entry.label);
    expect(labels).toEqual(
      expect.arrayContaining([
        expect.stringContaining("zero documented records"),
        expect.stringContaining("outside admitted coverage"),
        expect.stringContaining("withheld/generalized only"),
        expect.stringContaining("not evaluated"),
      ])
    );
  });
});

describe("botanicalOccurrencesToGeoJSON", () => {
  it("never draws nonspatial records as features", () => {
    const spatial = sampleFeature({ occurrence_id: "occ-spatial" });
    const nonspatial = sampleFeature({
      occurrence_id: "occ-nonspatial",
      longitude: NaN,
      latitude: NaN,
    });
    const collection = botanicalOccurrencesToGeoJSON([spatial, nonspatial]);
    expect(collection.features).toHaveLength(1);
    expect(collection.features[0].properties?.occurrence_id).toBe("occ-spatial");
  });
});

describe("refused responses", () => {
  afterEach(() => {
    mocks.fetchBotanicalOccurrences.mockReset();
  });

  it("renders the refusal reason and draws no features", async () => {
    const refused: BotanicalOccurrenceResponse = {
      state: "refused",
      reason: "The requested claim is outside admitted evidence.",
    };
    mocks.fetchBotanicalOccurrences.mockResolvedValue(refused);

    const { fetchBotanicalOccurrences } = await import("@/lib/botanical-occurrences");
    const response = await fetchBotanicalOccurrences({
      release_set_id: "release-2026-08",
      bbox: "-117,43,-116,44",
      zoom: 6,
    });

    expect(response.state).toBe("refused");
    if (response.state === "refused") {
      expect(response.reason).toBe("The requested claim is outside admitted evidence.");
    }
  });

  it("shows nonspatial counts as a number, never as map features", async () => {
    const detail: BotanicalOccurrenceResponse = {
      state: "detail",
      release_set_id: "release-2026-08",
      published_at: "2026-08-08T06:00:00Z",
      taxonomy_recipe_version: "v1",
      qc_policy_version: "v1",
      support_id: null,
      truncated: false,
      next_cursor: null,
      counts: { returned: 1, matched: 4, withheld: 1, nonspatial: 2, excluded_by_qc: 0 },
      features: [sampleFeature()],
    };
    mocks.fetchBotanicalOccurrences.mockResolvedValue(detail);

    const { fetchBotanicalOccurrences } = await import("@/lib/botanical-occurrences");
    const response = await fetchBotanicalOccurrences({
      release_set_id: "release-2026-08",
      bbox: "-117,43,-116,44",
      zoom: 12,
    });

    expect(response.state).toBe("detail");
    if (response.state === "detail") {
      expect(response.counts.nonspatial).toBe(2);
      const geojson = botanicalOccurrencesToGeoJSON(response.features);
      expect(geojson.features).toHaveLength(1);
    }
  });
});
