import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, act } from "@testing-library/react";

/*
 * Stubs next/dynamic so InterventionDrawControl never has to construct a real terra-draw
 * instance/map in jsdom -- the modal's own wiring (disabled submit, inline validation, the
 * payload it hands submitIntervention) is what this file exercises, matching the approach
 * LayerManager.test.tsx uses for its own dynamically-imported sub-layers.
 */
const drawStub = vi.hoisted(() => ({
  latestOnGeometryChange: null as ((geometry: unknown) => void) | null,
}));

vi.mock("next/dynamic", () => ({
  default: (loader: unknown) => {
    const isDrawControl = /InterventionDrawControl/.test(String(loader));
    if (!isDrawControl) {
      return function UnrelatedDynamicStub() {
        return null;
      };
    }
    return function InterventionDrawControlStub({
      onGeometryChange,
    }: {
      onGeometryChange: (geometry: unknown) => void;
    }) {
      drawStub.latestOnGeometryChange = onGeometryChange;
      return <div data-testid="draw-control-stub" />;
    };
  },
}));

const mocks = vi.hoisted(() => ({
  submitMutate: vi.fn(),
}));

vi.mock("@/lib/trpc/client", () => ({
  trpc: {
    interventions: {
      submitIntervention: {
        useMutation: () => ({ mutate: mocks.submitMutate, isPending: false }),
      },
    },
  },
}));

import { InterventionSubmitModal } from "@/components/panels/InterventionSubmitModal";

const DRAWN_POLYGON = {
  type: "Polygon",
  coordinates: [
    [
      [-116.3, 43.6],
      [-116.2, 43.6],
      [-116.2, 43.7],
      [-116.3, 43.6],
    ],
  ],
};

function fillRequiredFields() {
  fireEvent.change(screen.getByLabelText(/Site Name/), {
    target: { value: "Ridge silvopasture plot" },
  });
  fireEvent.click(
    screen.getByLabelText(/I understand this is a recommendation/)
  );
}

describe("InterventionSubmitModal", () => {
  beforeEach(() => {
    mocks.submitMutate.mockClear();
    drawStub.latestOnGeometryChange = null;
  });

  it("disables submit until geometry is drawn", () => {
    render(
      <InterventionSubmitModal lat={43.65} lon={-116.25} onClose={vi.fn()} />
    );
    fillRequiredFields();

    const submitButton = screen.getByRole("button", {
      name: /Submit/,
    }) as HTMLButtonElement;
    expect(submitButton.disabled).toBe(true);
  });

  it("enables submit once a valid geometry is drawn", () => {
    render(
      <InterventionSubmitModal lat={43.65} lon={-116.25} onClose={vi.fn()} />
    );
    fillRequiredFields();
    act(() => {
      drawStub.latestOnGeometryChange?.(DRAWN_POLYGON);
    });

    const submitButton = screen.getByRole("button", {
      name: /Submit/,
    }) as HTMLButtonElement;
    expect(submitButton.disabled).toBe(false);
  });

  it("shows an inline error for an under-3-vertex / unclosed polygon and keeps submit disabled", () => {
    render(
      <InterventionSubmitModal lat={43.65} lon={-116.25} onClose={vi.fn()} />
    );
    fillRequiredFields();
    act(() => {
      drawStub.latestOnGeometryChange?.({
        type: "Polygon",
        coordinates: [
          [
            [-116.3, 43.6],
            [-116.2, 43.6],
          ],
        ],
      });
    });

    expect(screen.getByRole("alert").textContent).toMatch(/close|vertex/i);
    const submitButton = screen.getByRole("button", {
      name: /Submit/,
    }) as HTMLButtonElement;
    expect(submitButton.disabled).toBe(true);
  });

  it("filters the type dropdown to air-only options when category is air", () => {
    render(
      <InterventionSubmitModal lat={43.65} lon={-116.25} onClose={vi.fn()} />
    );

    fireEvent.change(screen.getByLabelText(/Category/), {
      target: { value: "air" },
    });

    const typeSelect = screen.getByLabelText(
      /Intervention Type/
    ) as HTMLSelectElement;
    const options = Array.from(typeSelect.options).map(
      (option) => option.value
    );
    expect(options).toEqual(["cloud_seeding"]);
  });

  it("submits the exact drawn polygon geometry and chosen category", () => {
    render(
      <InterventionSubmitModal lat={43.65} lon={-116.25} onClose={vi.fn()} />
    );
    fillRequiredFields();
    act(() => {
      drawStub.latestOnGeometryChange?.(DRAWN_POLYGON);
    });

    fireEvent.click(screen.getByRole("button", { name: /Submit/ }));

    expect(mocks.submitMutate).toHaveBeenCalledWith(
      expect.objectContaining({
        geometry: DRAWN_POLYGON,
        category: "land",
      })
    );
  });
});
