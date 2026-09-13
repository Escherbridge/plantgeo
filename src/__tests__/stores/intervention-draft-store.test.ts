import { beforeEach, describe, expect, it } from "vitest";
import { useInterventionDraftStore } from "@/stores/intervention-draft-store";
import type { InterventionGeometry } from "@/lib/geo/intervention-geometry-schema";

const sampleGeometry: InterventionGeometry = {
  type: "Point",
  coordinates: [-105.25, 40.12],
} as unknown as InterventionGeometry;

describe("intervention draft store", () => {
  beforeEach(() => {
    useInterventionDraftStore.getState().clearDraft();
  });

  it("holds the draft fields with sane defaults", () => {
    const state = useInterventionDraftStore.getState();
    expect(state).toMatchObject({
      category: "land",
      interventionType: "reforestation",
      name: "",
      description: "",
      publicationConsent: false,
      geometry: null,
      geometryError: null,
      lat: null,
      lon: null,
    });
  });

  it("survives a simulated unmount/remount cycle (values set before are present after)", () => {
    const store = useInterventionDraftStore.getState();
    store.seedLocation(40.12, -105.25);
    store.setCategory("air");
    store.setInterventionType("cloud_seeding");
    store.setName("Test intervention");
    store.setDescription("A description");
    store.setPublicationConsent(true);
    store.setGeometry(sampleGeometry);

    // Simulate unmount: no component reads the store between here and "remount".
    // Simulate remount: read fresh state directly from the store, not from any component.
    const remounted = useInterventionDraftStore.getState();

    expect(remounted).toMatchObject({
      category: "air",
      interventionType: "cloud_seeding",
      name: "Test intervention",
      description: "A description",
      publicationConsent: true,
      geometry: sampleGeometry,
      geometryError: null,
      lat: 40.12,
      lon: -105.25,
    });
  });

  it("setGeometry clears any existing geometryError", () => {
    const store = useInterventionDraftStore.getState();
    store.setGeometryError("Invalid geometry");
    expect(useInterventionDraftStore.getState().geometryError).toBe("Invalid geometry");

    store.setGeometry(sampleGeometry);
    const state = useInterventionDraftStore.getState();
    expect(state.geometry).toBe(sampleGeometry);
    expect(state.geometryError).toBeNull();
  });

  it("seedLocation sets lat/lon and resets the rest of the draft to defaults", () => {
    const store = useInterventionDraftStore.getState();
    store.setCategory("air");
    store.setInterventionType("biochar");
    store.setName("Stale name");
    store.setDescription("Stale description");
    store.setPublicationConsent(true);
    store.setGeometry(sampleGeometry);
    store.setGeometryError("Stale error");

    store.seedLocation(44.66, -118.83);

    const state = useInterventionDraftStore.getState();
    expect(state).toMatchObject({
      lat: 44.66,
      lon: -118.83,
      category: "land",
      interventionType: "reforestation",
      name: "",
      description: "",
      publicationConsent: false,
      geometry: null,
      geometryError: null,
    });
  });

  it("clearDraft is the only action that resets lat/lon back to null", () => {
    const store = useInterventionDraftStore.getState();
    store.seedLocation(40.12, -105.25);
    store.setName("Something");

    store.clearDraft();

    const state = useInterventionDraftStore.getState();
    expect(state).toMatchObject({
      lat: null,
      lon: null,
      category: "land",
      interventionType: "reforestation",
      name: "",
      description: "",
      publicationConsent: false,
      geometry: null,
      geometryError: null,
    });
  });
});
