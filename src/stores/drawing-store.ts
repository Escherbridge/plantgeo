import { create } from "zustand";
import { devtools } from "zustand/middleware";

export type DrawingMode =
  | "point"
  | "line"
  | "polygon"
  | "rectangle"
  | "circle"
  | "freehand"
  | "text"
  | "select"
  | null;

interface DrawingState {
  drawingMode: DrawingMode;
  features: GeoJSON.FeatureCollection;
  selectedFeatureIndex: number | null;
  undoStack: GeoJSON.FeatureCollection[];
  redoStack: GeoJSON.FeatureCollection[];
  isDrawing: boolean;

  setMode: (mode: DrawingMode) => void;
  addFeature: (feature: GeoJSON.Feature) => void;
  updateFeature: (index: number, feature: GeoJSON.Feature) => void;
  removeFeature: (index: number) => void;
  selectFeature: (index: number | null) => void;
  undo: () => void;
  redo: () => void;
  clear: () => void;
  setIsDrawing: (drawing: boolean) => void;
}

const emptyCollection = (): GeoJSON.FeatureCollection => ({
  type: "FeatureCollection",
  features: [],
});

export const useDrawingStore = create<DrawingState>()(
  devtools((set) => ({
    drawingMode: null,
    features: emptyCollection(),
    selectedFeatureIndex: null,
    undoStack: [],
    redoStack: [],
    isDrawing: false,

    setMode: (mode) =>
      set({ drawingMode: mode, selectedFeatureIndex: null, isDrawing: false }),

    addFeature: (feature) =>
      set((s) => {
        const prev = s.features;
        return {
          undoStack: [...s.undoStack, prev],
          redoStack: [],
          features: {
            ...prev,
            features: [...prev.features, feature],
          },
        };
      }),

    updateFeature: (index, feature) =>
      set((s) => {
        const prev = s.features;
        const next = [...prev.features];
        next[index] = feature;
        return {
          undoStack: [...s.undoStack, prev],
          redoStack: [],
          features: { ...prev, features: next },
        };
      }),

    removeFeature: (index) =>
      set((s) => {
        const prev = s.features;
        const next = prev.features.filter((_, i) => i !== index);
        return {
          undoStack: [...s.undoStack, prev],
          redoStack: [],
          features: { ...prev, features: next },
          selectedFeatureIndex: null,
        };
      }),

    selectFeature: (index) => set({ selectedFeatureIndex: index }),

    undo: () =>
      set((s) => {
        if (s.undoStack.length === 0) return s;
        const prev = s.undoStack[s.undoStack.length - 1];
        return {
          undoStack: s.undoStack.slice(0, -1),
          redoStack: [...s.redoStack, s.features],
          features: prev,
          selectedFeatureIndex: null,
        };
      }),

    redo: () =>
      set((s) => {
        if (s.redoStack.length === 0) return s;
        const next = s.redoStack[s.redoStack.length - 1];
        return {
          redoStack: s.redoStack.slice(0, -1),
          undoStack: [...s.undoStack, s.features],
          features: next,
          selectedFeatureIndex: null,
        };
      }),

    clear: () =>
      set((s) => ({
        undoStack: [...s.undoStack, s.features],
        redoStack: [],
        features: emptyCollection(),
        selectedFeatureIndex: null,
        isDrawing: false,
        drawingMode: null,
      })),

    setIsDrawing: (drawing) => set({ isDrawing: drawing }),
  }))
);
