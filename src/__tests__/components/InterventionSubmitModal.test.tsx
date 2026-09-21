import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, act } from "@testing-library/react";
import { createRef } from "react";

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
  onSuccess: null as (() => void) | null,
}));

vi.mock("@/lib/trpc/client", () => ({
  trpc: {
    interventions: {
      submitIntervention: {
        useMutation: (options: { onSuccess: () => void }) => {
          mocks.onSuccess = options.onSuccess;
          return { mutate: mocks.submitMutate, isPending: false };
        },
      },
    },
  },
}));

import { InterventionSubmitModal } from "@/components/panels/InterventionSubmitModal";
import { InterventionProposalForm } from "@/components/panels/InterventionProposalForm";
import { useInterventionDraftStore } from "@/stores/intervention-draft-store";

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

describe.each(["community modal", "workspace proposal"])("data interventions in %s", (surface) => {
  beforeEach(() => {
    mocks.submitMutate.mockClear();
    mocks.onSuccess = null;
    useInterventionDraftStore.getState().clearDraft();
    if (surface === "community modal") {
      render(<InterventionSubmitModal lat={43.65} lon={-116.25} onClose={vi.fn()} />);
      act(() => drawStub.latestOnGeometryChange?.({ type: "Point", coordinates: [-116.25, 43.65] }));
    } else {
      useInterventionDraftStore.getState().setGeometry({ type: "Point", coordinates: [-116.25, 43.65] });
      render(<InterventionProposalForm lat={43.65} lon={-116.25} map={null} mapContainerRef={createRef<HTMLDivElement>()} />);
    }
    fillRequiredFields();
    fireEvent.change(screen.getByLabelText("Category"), { target: { value: "data" } });
  });

  function fillDataDetails() {
    fireEvent.change(screen.getByLabelText(/Data lane/), { target: { value: "water-gauges" } });
    fireEvent.change(screen.getByLabelText(/Collection method/), { target: { value: "Read the staff gauge at noon and record units." } });
  }

  function submitForm() {
    fireEvent.submit(screen.getByRole("button", { name: /Submit Recommendation/ }).closest("form")!);
  }

  it("submits a collection plan with its lane and method, without a verified-source control", () => {
    fillDataDetails();
    expect(screen.queryByLabelText(/Observation day/)).toBeNull();
    expect(screen.getByText(/Community collection plan/)).toBeTruthy();
    submitForm();
    expect(mocks.submitMutate).toHaveBeenCalledWith(expect.objectContaining({
      type: "data_collection",
      category: "data",
      dataDetails: { lane: "water-gauges", collectionMethod: "Read the staff gauge at noon and record units." },
    }));
    expect(mocks.submitMutate.mock.calls[0][0]).not.toHaveProperty("dataOrigin");
  });

  it("requires collection details before sending a data intervention", () => {
    submitForm();
    expect(mocks.submitMutate).not.toHaveBeenCalled();
    expect(screen.getByRole("alert").textContent).toMatch(/lane|details/i);
  });

  it("requires a measured day and safe evidence link for a dataset submission", () => {
    fillDataDetails();
    fireEvent.change(screen.getByLabelText("Intervention Type"), { target: { value: "data_submission" } });
    submitForm();
    expect(mocks.submitMutate).not.toHaveBeenCalled();
    expect(screen.getByRole("alert").textContent).toMatch(/observation date/i);
    fireEvent.change(screen.getByLabelText(/Observation day/), { target: { value: "2025-09-15" } });
    fireEvent.change(screen.getByLabelText(/Dataset or evidence link/), { target: { value: "javascript:alert(1)" } });
    submitForm();
    expect(mocks.submitMutate).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText(/Dataset or evidence link/), { target: { value: "https://example.org/gauges.csv" } });
    submitForm();
    expect(mocks.submitMutate).toHaveBeenCalledWith(expect.objectContaining({
      type: "data_submission",
      category: "data",
      dataDetails: {
        lane: "water-gauges",
        collectionMethod: "Read the staff gauge at noon and record units.",
        observedOn: "2025-09-15",
        dataUrl: "https://example.org/gauges.csv",
      },
    }));
    if (surface === "workspace proposal") {
      act(() => mocks.onSuccess?.());
      expect(useInterventionDraftStore.getState().dataDetails).toEqual({ lane: "", collectionMethod: "" });
    }
  });

  it("omits retained data fields when returning to a land intervention", () => {
    fillDataDetails();
    fireEvent.change(screen.getByLabelText("Category"), { target: { value: "land" } });
    submitForm();
    expect(mocks.submitMutate).toHaveBeenCalledWith(expect.objectContaining({ type: "reforestation", dataDetails: undefined }));
  });

  it("excludes hidden invalid submission fields from a collection plan while retaining the draft", () => {
    fillDataDetails();
    fireEvent.change(screen.getByLabelText("Intervention Type"), { target: { value: "data_submission" } });
    fireEvent.change(screen.getByLabelText(/Observation day/), { target: { value: "2999-09-15" } });
    fireEvent.change(screen.getByLabelText(/Dataset or evidence link/), { target: { value: "javascript:alert(1)" } });
    fireEvent.change(screen.getByLabelText("Intervention Type"), { target: { value: "data_collection" } });
    submitForm();
    expect(mocks.submitMutate).toHaveBeenCalledWith(expect.objectContaining({
      type: "data_collection",
      dataDetails: { lane: "water-gauges", collectionMethod: "Read the staff gauge at noon and record units." },
    }));
    fireEvent.change(screen.getByLabelText("Intervention Type"), { target: { value: "data_submission" } });
    expect((screen.getByLabelText(/Observation day/) as HTMLInputElement).value).toBe("2999-09-15");
    expect((screen.getByLabelText(/Dataset or evidence link/) as HTMLInputElement).value).toBe("javascript:alert(1)");
  });
});
