import { create } from "zustand";
import type { InterventionDetailRecord } from "@/lib/map/intervention-detail";

/**
 * What the intervention detail modal is showing, and how large it is showing it.
 *
 * Its own store, deliberately: the map click handlers write here, the modal
 * reads here, and neither touches `map-store` or the AI workspace's session
 * state -- which is what lets the detail modal and `AiInterventionWorkspace`
 * be open at once without either closing the other (FR-2's last acceptance
 * criterion). Nothing else in the app subscribes to it, so opening the modal
 * costs `MapView` no re-render (see `map-view-render-count.test.tsx`).
 */
interface InterventionDetailState {
  /** The feature under inspection; null closes the modal. */
  featureId: string | null;
  /**
   * The record, when the click could resolve it from memory (the drafts
   * overlay). Null means "fetch it by id" -- the published Martin-tile case.
   */
  record: InterventionDetailRecord | null;
  /** The Facebook-lightbox axis: false is the compact card, true is full-viewport. */
  isExpanded: boolean;
  /** Open on a record already in hand -- no network round trip. */
  openWithRecord: (record: InterventionDetailRecord) => void;
  /** Open on an id alone; the modal fetches the record itself. */
  openById: (featureId: string) => void;
  setExpanded: (isExpanded: boolean) => void;
  close: () => void;
}

export const useInterventionDetailStore = create<InterventionDetailState>()((set) => ({
  featureId: null,
  record: null,
  isExpanded: false,
  // Each open resets the size: a new feature always arrives as the compact card,
  // so a click never throws a full-screen overlay at a reader who did not ask.
  openWithRecord: (record) =>
    set({ featureId: record.id, record, isExpanded: false }),
  openById: (featureId) => set({ featureId, record: null, isExpanded: false }),
  setExpanded: (isExpanded) => set({ isExpanded }),
  close: () => set({ featureId: null, record: null, isExpanded: false }),
}));
