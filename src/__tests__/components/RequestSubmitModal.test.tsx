import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen } from "@testing-library/react";
import { renderWithProviders } from "@/test/utils";

/**
 * Phase 3 of `public_strategy_requests_20260913`: the request form submits PUBLICLY.
 *
 * Two things are asserted, and they are the two that can regress independently. First, that the
 * modal calls `interventions.submitRequest` -- the Phase 2 mutation that writes a published
 * `geo.features` row -- and not the deleted `community.submitRequest`, which wrote a private,
 * non-geospatial row no map layer could draw. Second, that none of the copy still claims the
 * request is private: the product decision reversed, and stale reassurance about privacy is worse
 * than no copy at all, because a contributor reads it and believes it.
 */

const submitRequestMutate = vi.hoisted(() => vi.fn());
const submitRequestUseMutation = vi.hoisted(() =>
  vi.fn((_options?: unknown) => ({
    mutate: submitRequestMutate,
    isPending: false,
  }))
);

vi.mock("@/lib/trpc/client", () => ({
  trpc: {
    interventions: {
      submitRequest: { useMutation: submitRequestUseMutation },
    },
  },
}));

import { RequestSubmitModal } from "@/components/panels/RequestSubmitModal";

beforeEach(() => {
  submitRequestMutate.mockClear();
  submitRequestUseMutation.mockClear();
});

afterEach(() => {
  vi.clearAllMocks();
});

function renderModal(overrides: Partial<{ onClose: () => void; onSuccess: () => void }> = {}) {
  return renderWithProviders(
    <RequestSubmitModal
      lat={43.615}
      lon={-116.2023}
      onClose={overrides.onClose ?? vi.fn()}
      onSuccess={overrides.onSuccess}
    />
  );
}

/** Every string the retired private design put in front of a contributor. */
const RETIRED_PRIVACY_COPY = [
  /private request/i,
  /never shown on the map/i,
  /shared only with authenticated members of/i,
  /requests are private/i,
  /private location/i,
  /not shown on the public map/i,
];

describe("RequestSubmitModal copy", () => {
  it("carries none of the retired private-request copy", () => {
    const { container } = renderModal();
    const text = container.textContent ?? "";

    for (const pattern of RETIRED_PRIVACY_COPY) {
      expect(text).not.toMatch(pattern);
    }
  });

  it("says plainly that the request goes on the public map immediately", () => {
    renderModal();

    expect(screen.getAllByText(/public map/i).length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: /post request/i })).toBeTruthy();
  });

  it("still gates submission behind an explicit publication consent checkbox", () => {
    renderModal();

    const consent = screen.getByRole("checkbox");
    expect((consent as HTMLInputElement).checked).toBe(false);
    expect(
      (screen.getByRole("button", { name: /post request/i }) as HTMLButtonElement)
        .disabled
    ).toBe(true);
  });
});

describe("RequestSubmitModal submission", () => {
  function fillAndSubmit() {
    fireEvent.change(screen.getByPlaceholderText("Brief title for this request"), {
      target: { value: "Swales above the north pasture" },
    });
    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(screen.getByRole("button", { name: /post request/i }));
  }

  it("calls interventions.submitRequest, not the deleted community.submitRequest", () => {
    renderModal();
    fillAndSubmit();

    expect(submitRequestUseMutation).toHaveBeenCalled();
    expect(submitRequestMutate).toHaveBeenCalledTimes(1);
  });

  it("sends a real Point geometry built from the map centre, not a bare lat/lon", () => {
    renderModal();
    fillAndSubmit();

    const input = submitRequestMutate.mock.calls[0][0];
    expect(input.geometry).toEqual({
      type: "Point",
      coordinates: [-116.2023, 43.615],
    });
    expect(input).not.toHaveProperty("lat");
    expect(input).not.toHaveProperty("lon");
  });

  it("sends the unified field names and the explicit consent literal", () => {
    renderModal();
    fillAndSubmit();

    const input = submitRequestMutate.mock.calls[0][0];
    expect(input.name).toBe("Swales above the north pasture");
    expect(input.type).toBe("reforestation");
    expect(input.publicationConsent).toBe(true);
    // `title`/`strategyType`/`locationConsent`/`teamId` were the retired mutation's vocabulary.
    expect(input).not.toHaveProperty("title");
    expect(input).not.toHaveProperty("strategyType");
    expect(input).not.toHaveProperty("locationConsent");
    expect(input).not.toHaveProperty("teamId");
  });

  it("offers water_harvesting, the type Phase 2 added to the land vocabulary", () => {
    renderModal();

    const select = screen.getByLabelText(/strategy type/i) as HTMLSelectElement;
    const values = Array.from(select.options).map((option) => option.value);
    expect(values).toContain("water_harvesting");
    // Air-category types are not reachable through the request flow (OQ-D).
    expect(values).not.toContain("cloud_seeding");
  });

  it("refuses to submit without consent and says why", () => {
    renderModal();
    fireEvent.change(screen.getByPlaceholderText("Brief title for this request"), {
      target: { value: "Swales above the north pasture" },
    });
    fireEvent.submit(screen.getByRole("button", { name: /post request/i }).closest("form")!);

    expect(submitRequestMutate).not.toHaveBeenCalled();
    expect(screen.getByRole("alert").textContent).toMatch(/publish/i);
  });
});
