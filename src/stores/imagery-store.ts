import { create } from "zustand";
import { devtools } from "zustand/middleware";
import type { MapillaryImage } from "@/lib/server/services/mapillary";

interface ImageryState {
  isStreetViewEnabled: boolean;
  selectedImageId: string | null;
  selectedImage: MapillaryImage | null;
  showSplitView: boolean;

  toggleStreetView: () => void;
  setSelectedImageId: (id: string | null) => void;
  setSelectedImage: (image: MapillaryImage | null) => void;
  toggleSplitView: () => void;
  closeSplitView: () => void;
}

export const useImageryStore = create<ImageryState>()(
  devtools((set) => ({
    isStreetViewEnabled: false,
    selectedImageId: null,
    selectedImage: null,
    showSplitView: false,

    toggleStreetView: () =>
      set((s) => ({ isStreetViewEnabled: !s.isStreetViewEnabled })),

    setSelectedImageId: (id) =>
      set({ selectedImageId: id, showSplitView: id !== null }),

    setSelectedImage: (image) =>
      set({ selectedImage: image }),

    toggleSplitView: () =>
      set((s) => ({ showSplitView: !s.showSplitView })),

    closeSplitView: () =>
      set({ showSplitView: false, selectedImageId: null, selectedImage: null }),
  }))
);
