import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { BoundaryGeometry } from "@/lib/map/intervention-boundary";

const mocks = vi.hoisted(() => ({ submit: vi.fn(), revise: vi.fn() }));
vi.mock("@/lib/trpc/client", () => ({ trpc: { interventions: {
  submitIntervention: { useMutation: () => ({ mutate: mocks.submit, isPending: false }) },
  reviseIntervention: { useMutation: () => ({ mutate: mocks.revise, isPending: false }) },
} } }));
vi.mock("@/lib/map/map-context", () => ({ useMap: () => ({}) }));
const polygon: GeoJSON.Polygon = { type: "Polygon", coordinates: [[[-116, 44], [-115.9, 44], [-115.9, 44.1], [-116, 44]]] };
vi.mock("@/components/map/InterventionBoundaryEditor", () => ({
  InterventionBoundaryEditor: ({ onSave, onCancel }: { onSave: (value: BoundaryGeometry) => void; onCancel: () => void }) => <div><button onClick={() => onSave(polygon)}>Save drawn polygon</button><button onClick={onCancel}>Cancel drawing</button></div>,
}));
import { InterventionSubmitModal } from "@/components/panels/InterventionSubmitModal";

beforeEach(() => vi.clearAllMocks());

function nameAndConsent() {
  fireEvent.change(screen.getByLabelText(/Site Name/), { target: { value: "Ridge restoration" } });
  fireEvent.click(screen.getByRole("checkbox"));
}

describe("intervention submission geometry", () => {
  it("requires an explicit site and sends the drawn Polygon rather than map centre", () => {
    render(<InterventionSubmitModal lat={43} lon={-117} onClose={vi.fn()} />);
    nameAndConsent();
    expect((screen.getByRole("button", { name: "Submit Recommendation" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "Draw polygon" }));
    fireEvent.click(screen.getByRole("button", { name: "Save drawn polygon" }));
    fireEvent.click(screen.getByRole("button", { name: "Submit Recommendation" }));
    expect(mocks.submit).toHaveBeenCalledWith(expect.objectContaining({ geometry: polygon, publicationConsent: true, name: "Ridge restoration" }));
  });

  it("keeps text, consent and prior geometry when drawing is cancelled", () => {
    render(<InterventionSubmitModal lat={43} lon={-117} onClose={vi.fn()} />);
    nameAndConsent();
    fireEvent.click(screen.getByRole("button", { name: "Use map centre as point" }));
    fireEvent.click(screen.getByRole("button", { name: "Draw rectangle" }));
    fireEvent.click(screen.getByRole("button", { name: "Cancel drawing" }));
    fireEvent.click(screen.getByRole("button", { name: "Submit Recommendation" }));
    expect(mocks.submit).toHaveBeenCalledWith(expect.objectContaining({ geometry: { type: "Point", coordinates: [-117, 43] }, name: "Ridge restoration", publicationConsent: true }));
  });

  it("preserves a prior MultiPolygon on reviewed resubmission", () => {
    const geometry: GeoJSON.MultiPolygon = { type: "MultiPolygon", coordinates: [polygon.coordinates] };
    render(<InterventionSubmitModal lat={43} lon={-117} revision={{ featureId: "reviewed-id", name: "Saved site", type: "biochar", geometry }} onClose={vi.fn()} />);
    expect(screen.getByText(/Drawing or choosing a new site replaces the entire/)).toBeTruthy();
    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(screen.getByRole("button", { name: "Submit Recommendation" }));
    expect(mocks.revise).toHaveBeenCalledWith(expect.objectContaining({ featureId: "reviewed-id", geometry, publicationConsent: true }));
    expect(mocks.submit).not.toHaveBeenCalled();
  });
});
