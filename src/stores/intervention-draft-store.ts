import { create } from 'zustand';
import { devtools } from 'zustand/middleware';
import type { InterventionCategory, InterventionType } from '@/lib/environmental/intervention';
import type { InterventionGeometry } from '@/lib/geo/intervention-geometry-schema';

interface InterventionDraftState {
  category: InterventionCategory;
  interventionType: InterventionType;
  name: string;
  description: string;
  publicationConsent: boolean;
  geometry: InterventionGeometry | null;
  geometryError: string | null;
  lat: number | null;
  lon: number | null;

  setCategory: (category: InterventionCategory) => void;
  setInterventionType: (interventionType: InterventionType) => void;
  setName: (name: string) => void;
  setDescription: (description: string) => void;
  setPublicationConsent: (publicationConsent: boolean) => void;
  setGeometry: (geometry: InterventionGeometry | null) => void;
  setGeometryError: (geometryError: string | null) => void;
  /** Sets the seeded lat/lon for a genuinely new location and resets the rest of the draft. */
  seedLocation: (lat: number, lon: number) => void;
  /** Explicit-only reset. Never called implicitly on mount. */
  clearDraft: () => void;
}

const draftDefaults = {
  category: 'land' as InterventionCategory,
  interventionType: 'reforestation' as InterventionType,
  name: '',
  description: '',
  publicationConsent: false,
  geometry: null as InterventionGeometry | null,
  geometryError: null as string | null,
};

export const useInterventionDraftStore = create<InterventionDraftState>()(
  devtools(
    (set) => ({
      ...draftDefaults,
      lat: null,
      lon: null,

      setCategory: (category) => set({ category }),
      setInterventionType: (interventionType) => set({ interventionType }),
      setName: (name) => set({ name }),
      setDescription: (description) => set({ description }),
      setPublicationConsent: (publicationConsent) => set({ publicationConsent }),
      setGeometry: (geometry) => set({ geometry, geometryError: null }),
      setGeometryError: (geometryError) => set({ geometryError }),
      seedLocation: (lat, lon) => set({ ...draftDefaults, lat, lon }),
      clearDraft: () => set({ ...draftDefaults, lat: null, lon: null }),
    }),
    { name: 'intervention-draft' }
  )
);
