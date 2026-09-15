import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { StrictMode } from "react";
import type { InterventionGeometry } from "@/lib/geo/intervention-geometry-schema";

const drawing = vi.hoisted(() => ({
  maps: [] as { center: [number, number]; removed: boolean; events: string[]; getSource: () => void }[],
  sessions: [] as {
    features: { type: "Feature"; geometry: InterventionGeometry; properties: { mode: string } }[];
    listeners: Set<() => void>;
  }[],
  rejectRestore: false,
}));

vi.mock("maplibre-gl", () => ({
  default: {
    Map: class {
      readonly center: [number, number];
      removed = false;
      readonly events: string[] = [];
      constructor(options: { center: [number, number] }) {
        this.center = options.center;
        drawing.maps.push(this);
      }
      resize() {}
      remove() { this.events.push("remove"); this.removed = true; }
      getSource() {
        if (this.removed) throw new Error("Drawing accessed the removed MapLibre style");
      }
      isStyleLoaded() { return true; }
      on() {}
      off() {}
    },
  },
}));
vi.mock("terra-draw", () => ({
  TerraDraw: class {
    private readonly session = {
      features: [] as (typeof drawing.sessions)[number]["features"],
      listeners: new Set<() => void>(),
    };
    constructor(private readonly options: { adapter: { map: (typeof drawing.maps)[number] } }) {
      drawing.sessions.push(this.session);
    }
    start() { this.options.adapter.map.events.push("start"); }
    stop() {
      this.options.adapter.map.getSource();
      this.options.adapter.map.events.push("stop");
    }
    setMode() {}
    addFeatures(features: (typeof drawing.sessions)[number]["features"]) {
      if (drawing.rejectRestore) return [{ valid: false }];
      this.session.features = features;
      this.session.listeners.forEach((listener) => listener());
      return [{ valid: true }];
    }
    clear() {
      this.session.features = [];
      this.session.listeners.forEach((listener) => listener());
    }
    getSnapshot() { return this.session.features; }
    on(_event: string, listener: () => void) { this.session.listeners.add(listener); }
    off(_event: string, listener: () => void) { this.session.listeners.delete(listener); }
  },
  TerraDrawPointMode: class {},
  TerraDrawPolygonMode: class {},
  TerraDrawSelectMode: class {},
}));
vi.mock("terra-draw-maplibre-gl-adapter", () => ({
  TerraDrawMapLibreGLAdapter: class {
    readonly map: (typeof drawing.maps)[number];
    constructor(options: { map: (typeof drawing.maps)[number] }) { this.map = options.map; }
  },
}));
vi.mock("@/lib/map/styles", () => ({ getStyle: () => ({ version: 8, sources: {}, layers: [] }) }));
vi.mock("@/hooks/useRegionalIntelligence", () => ({
  useRegionalIntelligence: () => ({ queryLocation: vi.fn() }),
}));
vi.mock("@/components/panels/RegionalIntelligencePanel", () => ({ default: () => null }));
vi.mock("@/lib/trpc/client", () => ({
  trpc: { interventions: { submitIntervention: { useMutation: () => ({ mutate: vi.fn(), isPending: false }) } } },
}));

import { AiInterventionWorkspace } from "@/components/map/AiInterventionWorkspace";
import { InterventionSubmitModal } from "@/components/panels/InterventionSubmitModal";
import { useInterventionDraftStore } from "@/stores/intervention-draft-store";
import { useRegionalIntelligenceStore } from "@/stores/regional-intelligence-store";

const polygon: InterventionGeometry = {
  type: "Polygon",
  coordinates: [[[-116.3, 43.6], [-116.2, 43.6], [-116.2, 43.7], [-116.3, 43.6]]],
};

beforeEach(() => {
  drawing.maps.length = 0;
  drawing.sessions.length = 0;
  drawing.rejectRestore = false;
  useInterventionDraftStore.getState().clearDraft();
  useRegionalIntelligenceStore.getState().closePanel();
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe("workspace draft recovery", () => {
  it.each(["workspace", "recommendation modal"] as const)(
    "stops every drawing session before its %s owner removes the map, including Strict Mode replay",
    async (owner) => {
      const view = render(
        <StrictMode>
          {owner === "workspace"
            ? <AiInterventionWorkspace coordinates={[-116.2, 43.6]} initialMode="intervention" onClose={vi.fn()} />
            : <InterventionSubmitModal lon={-116.2} lat={43.6} onClose={vi.fn()} />}
        </StrictMode>
      );
      await waitFor(() => expect(drawing.sessions.length).toBeGreaterThan(0));
      expect(() => view.unmount()).not.toThrow();
      for (const map of drawing.maps) {
        expect(map.events.at(-1)).toBe("remove");
        expect(map.events.filter((event) => event === "stop")).toHaveLength(
          map.events.filter((event) => event === "start").length
        );
        expect(map.events.indexOf("remove")).toBe(map.events.length - 1);
      }
    }
  );

  it("recovers a drawn proposal after navigation but never revives a deliberately discarded draft", async () => {
    const first = render(<AiInterventionWorkspace coordinates={[-116.2, 43.6]} initialMode="intervention" onClose={vi.fn()} />);
    await waitFor(() => expect(drawing.sessions).toHaveLength(1));
    fireEvent.change(screen.getByLabelText(/Site Name/), { target: { value: "Saved ridge planting" } });
    act(() => {
      drawing.sessions[0].features = [{ type: "Feature", geometry: polygon, properties: { mode: "polygon" } }];
      drawing.sessions[0].listeners.forEach((listener) => listener());
    });
    expect(useInterventionDraftStore.getState().geometry).toEqual(polygon);
    first.unmount();
    expect(useInterventionDraftStore.getState().geometry).toEqual(polygon);

    const restored = render(<AiInterventionWorkspace coordinates={[-100, 40]} initialMode="intervention" onClose={vi.fn()} />);
    await waitFor(() => expect(drawing.sessions).toHaveLength(2));
    expect(drawing.sessions[1].features).toEqual([{ type: "Feature", geometry: polygon, properties: { mode: "polygon" } }]);
    expect(drawing.maps[1].center).toEqual([-116.2, 43.6]);
    expect((screen.getByLabelText(/Site Name/) as HTMLInputElement).value).toBe("Saved ridge planting");
    expect(screen.getByText(/Site location: 43\.6000, -116\.2000/)).toBeTruthy();
    fireEvent.click(screen.getByRole("tab", { name: /AI analysis/i }));
    fireEvent.click(screen.getByRole("tab", { name: /Propose intervention/i }));
    expect(drawing.sessions).toHaveLength(2);
    expect(drawing.sessions[1].features[0].geometry).toEqual(polygon);

    vi.spyOn(window, "confirm").mockReturnValue(true);
    fireEvent.click(screen.getByRole("button", { name: "Close workspace" }));
    restored.unmount();
    render(<AiInterventionWorkspace coordinates={[-100, 40]} initialMode="intervention" onClose={vi.fn()} />);
    await waitFor(() => expect(drawing.sessions).toHaveLength(3));
    expect(drawing.sessions[2].features).toEqual([]);
    expect(drawing.maps[2].center).toEqual([-100, 40]);
    expect(useInterventionDraftStore.getState().geometry).toBeNull();
    expect((screen.getByLabelText(/Site Name/) as HTMLInputElement).value).toBe("");
  });

  it("keeps failed recovery visible and blocks submission without deleting the draft", async () => {
    const draft = useInterventionDraftStore.getState();
    draft.seedLocation(43.6, -116.2);
    draft.setName("Retained proposal");
    draft.setGeometry(polygon);
    drawing.rejectRestore = true;
    render(<AiInterventionWorkspace coordinates={[-100, 40]} initialMode="intervention" onClose={vi.fn()} />);
    await waitFor(() => expect(drawing.sessions).toHaveLength(1));
    expect(screen.getByRole("alert").textContent).toContain("It is still saved");
    expect(useInterventionDraftStore.getState().geometry).toEqual(polygon);
    expect((screen.getByRole("button", { name: "Submit Recommendation" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "Clear" }));
    expect(useInterventionDraftStore.getState().geometry).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
  });
});
