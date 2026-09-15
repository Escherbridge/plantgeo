import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";

/**
 * Phase 4 gave both slots real content, so this shell-contract file now has to stub what that
 * content reaches for: a MapLibre instance (the shell owns the drawing map now), the tRPC
 * mutation the proposal form holds, terra-draw, and the analysis pane itself. None of those are
 * this file's subject -- it pins shell behaviour, which is unchanged.
 */
vi.mock("maplibre-gl", () => ({
  default: {
    Map: class {
      remove() {}
      resize() {}
    },
  },
}));
vi.mock("@/lib/map/styles", () => ({
  getStyle: () => ({ version: 8, sources: {}, layers: [] }),
  skyThemes: new Proxy({}, { get: () => ({}) }),
}));
vi.mock("@/lib/trpc/client", () => ({
  trpc: {
    interventions: {
      submitIntervention: {
        useMutation: () => ({ mutate: vi.fn(), isPending: false }),
      },
    },
  },
}));
vi.mock("@/components/map/InterventionDrawControl", () => ({
  InterventionDrawControl: () => null,
}));
vi.mock("@/components/panels/RegionalIntelligencePanel", () => ({
  default: () => <div data-testid="regional-panel-stub" />,
}));

import { AiInterventionWorkspace } from "@/components/map/AiInterventionWorkspace";
import { useInterventionDraftStore } from "@/stores/intervention-draft-store";
import { useRegionalIntelligenceStore } from "@/stores/regional-intelligence-store";
import { useMapStore } from "@/stores/map-store";
import type { InterventionGeometry } from "@/lib/geo/intervention-geometry-schema";

/**
 * The workspace shell's contract, pinned at Phase 3 -- before either pane's real content is
 * ported into it. Everything asserted here is shell behaviour, not pane behaviour: which mode is
 * shown, that BOTH stay mounted (OQ-1(a) keep-alive), that the mode switch is not a close, that a
 * close over unsaved work asks first, and that the layer strip writes the dock's own store rather
 * than a shadow copy of it.
 */

const drawnPolygon: InterventionGeometry = {
  type: "Polygon",
  coordinates: [
    [
      [-120, 46],
      [-120.1, 46],
      [-120.1, 46.1],
      [-120, 46],
    ],
  ],
};

function resetStores() {
  useInterventionDraftStore.getState().clearDraft();
  useRegionalIntelligenceStore.setState({ isLoading: false, abortController: null });
  useMapStore.setState({ activeLayers: [] });
}

beforeEach(resetStores);

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  resetStores();
});

function renderWorkspace(
  overrides: Partial<React.ComponentProps<typeof AiInterventionWorkspace>> = {}
) {
  const onClose = vi.fn();
  render(
    <AiInterventionWorkspace
      coordinates={[-120, 46]}
      initialMode="ai"
      onClose={onClose}
      {...overrides}
    />
  );
  return { onClose };
}

describe("AiInterventionWorkspace mode switching", () => {
  it("keeps a manual tab selection on stable rerenders but honors a fresh action at the same point", () => {
    const onClose = vi.fn();
    const coordinates: [number, number] = [-120, 46];
    const { rerender } = render(
      <AiInterventionWorkspace coordinates={coordinates} initialMode="ai" onClose={onClose} />
    );
    fireEvent.click(screen.getByRole("tab", { name: /propose intervention/i }));
    rerender(<AiInterventionWorkspace coordinates={coordinates} initialMode="ai" onClose={onClose} />);
    expect(screen.getByTestId("intervention-mode-content").hasAttribute("hidden")).toBe(false);
    rerender(<AiInterventionWorkspace coordinates={[-120, 46]} initialMode="ai" onClose={onClose} />);
    expect(screen.getByTestId("ai-mode-content").hasAttribute("hidden")).toBe(false);
  });
  it("honors another map action without remounting either pane", () => {
    const onClose = vi.fn();
    const { rerender } = render(
      <AiInterventionWorkspace coordinates={[-120, 46]} initialMode="ai" onClose={onClose} />
    );
    const pane = screen.getByTestId("intervention-mode-content");
    rerender(
      <AiInterventionWorkspace coordinates={[-119, 45]} initialMode="intervention" onClose={onClose} />
    );
    expect(screen.getByRole("tab", { name: /propose intervention/i }).getAttribute("aria-selected")).toBe("true");
    expect(screen.getByTestId("intervention-mode-content")).toBe(pane);
    fireEvent.click(screen.getByRole("tab", { name: /ai analysis/i }));
    rerender(
      <AiInterventionWorkspace coordinates={[-118, 44]} initialMode="intervention" onClose={onClose} />
    );
    expect(pane.hasAttribute("hidden")).toBe(false);
    expect(onClose).not.toHaveBeenCalled();
  });
  it("names both modes on one switch control and shows the initial one", () => {
    renderWorkspace({ initialMode: "ai" });

    const aiTab = screen.getByRole("tab", { name: /ai analysis/i });
    const interventionTab = screen.getByRole("tab", { name: /propose intervention/i });

    expect(aiTab.getAttribute("aria-selected")).toBe("true");
    expect(interventionTab.getAttribute("aria-selected")).toBe("false");
    expect(screen.getByTestId("ai-mode-content").hasAttribute("hidden")).toBe(false);
    expect(screen.getByTestId("intervention-mode-content").hasAttribute("hidden")).toBe(true);
  });

  it("opens directly in intervention mode when asked to", () => {
    renderWorkspace({ initialMode: "intervention" });

    expect(
      screen.getByRole("tab", { name: /propose intervention/i }).getAttribute("aria-selected")
    ).toBe("true");
    expect(screen.getByTestId("intervention-mode-content").hasAttribute("hidden")).toBe(false);
  });

  it("keeps BOTH mode contents mounted across a switch, hiding the inactive one", () => {
    renderWorkspace({ initialMode: "ai" });

    // Both are in the DOM before the switch...
    expect(screen.getByTestId("intervention-mode-content")).toBeTruthy();

    fireEvent.click(screen.getByRole("tab", { name: /propose intervention/i }));

    // ...and the AI pane is still the very same node afterwards, merely hidden. An unmount
    // would have destroyed whatever live state it holds, which is exactly what OQ-1(a) forbids.
    const aiContent = screen.getByTestId("ai-mode-content");
    expect(aiContent).toBeTruthy();
    expect(aiContent.hasAttribute("hidden")).toBe(true);
    expect(screen.getByTestId("intervention-mode-content").hasAttribute("hidden")).toBe(false);
  });

  it("never calls onClose from the mode switch", () => {
    const { onClose } = renderWorkspace({ initialMode: "ai" });

    fireEvent.click(screen.getByRole("tab", { name: /propose intervention/i }));
    fireEvent.click(screen.getByRole("tab", { name: /ai analysis/i }));

    expect(onClose).not.toHaveBeenCalled();
  });
});

describe("AiInterventionWorkspace close confirmation", () => {
  it("does not handle Escape addressed to another dialog", () => {
    const { onClose } = renderWorkspace();
    const otherDialog = document.createElement("div");
    otherDialog.setAttribute("role", "dialog");
    document.body.append(otherDialog);
    fireEvent.keyDown(otherDialog, { key: "Escape" });
    otherDialog.remove();
    expect(onClose).not.toHaveBeenCalled();
  });

  it("preserves both sessions on canceled Escape and discards both on confirmed Escape", () => {
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);
    const { onClose } = renderWorkspace();
    const controller = new AbortController();
    act(() => {
      useInterventionDraftStore.getState().setName("Unfinished proposal");
      useRegionalIntelligenceStore.getState().openPanel(46, -120, "approximate");
      useRegionalIntelligenceStore.getState().setLoading(true);
      useRegionalIntelligenceStore.getState().setAbortController(controller);
    });
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onClose).not.toHaveBeenCalled();
    expect(controller.signal.aborted).toBe(false);
    expect(useInterventionDraftStore.getState().name).toBe("Unfinished proposal");
    confirmSpy.mockReturnValue(true);
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(controller.signal.aborted).toBe(true);
    expect(useRegionalIntelligenceStore.getState().isOpen).toBe(false);
    expect(useInterventionDraftStore.getState()).toMatchObject({ name: "", geometry: null, lat: null, lon: null });
  });
  it("closes without asking when there is nothing unsaved", () => {
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    const { onClose } = renderWorkspace();

    fireEvent.click(screen.getByRole("button", { name: /close workspace/i }));

    expect(confirmSpy).not.toHaveBeenCalled();
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("asks before closing over a drawn-but-unsubmitted geometry", () => {
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);
    const { onClose } = renderWorkspace();
    act(() => useInterventionDraftStore.getState().setGeometry(drawnPolygon));

    fireEvent.click(screen.getByRole("button", { name: /close workspace/i }));

    expect(confirmSpy).toHaveBeenCalledTimes(1);
    expect(onClose).not.toHaveBeenCalled();

    confirmSpy.mockReturnValue(true);
    fireEvent.click(screen.getByRole("button", { name: /close workspace/i }));
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(useInterventionDraftStore.getState().geometry).toBeNull();
  });

  it("asks before closing while an AI response is mid-stream", () => {
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);
    const { onClose } = renderWorkspace();
    act(() => useRegionalIntelligenceStore.getState().setLoading(true));

    fireEvent.click(screen.getByRole("button", { name: /close workspace/i }));

    expect(confirmSpy).toHaveBeenCalledTimes(1);
    expect(onClose).not.toHaveBeenCalled();
  });
});

describe("AiInterventionWorkspace draft preservation", () => {
  it("preserves a typed proposal before drawing when a new coordinate arrives", () => {
    const onClose = vi.fn();
    const { rerender } = render(
      <AiInterventionWorkspace coordinates={[-120, 46]} initialMode="intervention" onClose={onClose} />
    );
    act(() => {
      const draft = useInterventionDraftStore.getState();
      draft.setName("Ridge planting");
      draft.setDescription("Retain this draft");
      draft.setCategory("air");
      draft.setInterventionType("cloud_seeding");
      draft.setPublicationConsent(true);
    });
    rerender(
      <AiInterventionWorkspace coordinates={[-118, 44]} initialMode="intervention" onClose={onClose} />
    );
    expect(useInterventionDraftStore.getState()).toMatchObject({
      name: "Ridge planting", description: "Retain this draft", category: "air",
      interventionType: "cloud_seeding", publicationConsent: true, geometry: null, lat: 46, lon: -120,
    });
    expect(screen.getByText(/Site location: 46\.0000, -120\.0000/)).toBeTruthy();
    expect(screen.getByTestId("workspace-draft-relocate").textContent).toContain("confirm discarding the draft");
  });
  it("does not clear an unsubmitted draft when reopened at a new coordinate", () => {
    const { unmount } = render(
      <AiInterventionWorkspace
        coordinates={[-120, 46]}
        initialMode="intervention"
        onClose={vi.fn()}
      />
    );
    act(() => {
      useInterventionDraftStore.getState().setGeometry(drawnPolygon);
      useInterventionDraftStore.getState().setName("Ridge planting");
    });
    unmount();

    render(
      <AiInterventionWorkspace
        coordinates={[-118, 44]}
        initialMode="intervention"
        onClose={vi.fn()}
      />
    );

    const draft = useInterventionDraftStore.getState();
    expect(draft.geometry).toEqual(drawnPolygon);
    expect(draft.name).toBe("Ridge planting");
    // The new point is offered, not silently applied over the existing work.
    expect(screen.getByTestId("workspace-draft-relocate")).toBeTruthy();
  });

  it("seeds the clicked coordinate when the draft holds no unsaved work", () => {
    render(
      <AiInterventionWorkspace
        coordinates={[-118, 44]}
        initialMode="intervention"
        onClose={vi.fn()}
      />
    );

    expect(useInterventionDraftStore.getState().lat).toBe(44);
    expect(useInterventionDraftStore.getState().lon).toBe(-118);
  });
});

describe("AiInterventionWorkspace layer strip", () => {
  it("writes the same map-store activeLayers LayerRow reads", () => {
    useMapStore.setState({ activeLayers: ["fire"] });
    renderWorkspace();

    fireEvent.click(screen.getByTestId("workspace-layer-toggle-fire"));
    expect(useMapStore.getState().activeLayers).not.toContain("fire");

    fireEvent.click(screen.getByTestId("workspace-layer-toggle-fire"));
    expect(useMapStore.getState().activeLayers).toContain("fire");
  });

  it("reflects a toggle made anywhere else in the app", () => {
    renderWorkspace();
    const toggle = screen.getByTestId("workspace-layer-toggle-drought");
    expect(toggle.getAttribute("aria-checked")).toBe("false");

    fireEvent.click(toggle); // the store write LayerRow's eye also makes
    expect(screen.getByTestId("workspace-layer-toggle-drought").getAttribute("aria-checked")).toBe(
      "true"
    );
  });
});
