"use client";

import { useEffect, useRef } from "react";
import { useMap } from "@/lib/map/map-context";
import { useDrawingStore } from "@/stores/drawing-store";
import {
  createPointHandler,
  createLineHandler,
  createPolygonHandler,
  createRectangleHandler,
  createCircleHandler,
  createFreehandHandler,
} from "@/lib/map/drawing";
import type { DrawingMode } from "@/stores/drawing-store";

export function useDrawing() {
  const map = useMap();
  const drawingMode = useDrawingStore((s) => s.drawingMode);
  const addFeature = useDrawingStore((s) => s.addFeature);
  const updateFeature = useDrawingStore((s) => s.updateFeature);
  const features = useDrawingStore((s) => s.features);
  const setIsDrawing = useDrawingStore((s) => s.setIsDrawing);
  const cleanupRef = useRef<(() => void) | null>(null);
  const previewIndexRef = useRef<number | null>(null);

  useEffect(() => {
    if (cleanupRef.current) {
      cleanupRef.current();
      cleanupRef.current = null;
    }
    previewIndexRef.current = null;

    if (!map || !drawingMode || drawingMode === "select") return;

    setIsDrawing(true);

    const onAdd = (feature: GeoJSON.Feature) => {
      addFeature(feature);
      setIsDrawing(false);
    };

    const onUpdate = (feature: GeoJSON.Feature) => {
      if (previewIndexRef.current === null) {
        previewIndexRef.current = features.features.length;
        addFeature(feature);
      } else {
        updateFeature(previewIndexRef.current, feature);
      }
    };

    const onFinish = (feature: GeoJSON.Feature) => {
      if (previewIndexRef.current !== null) {
        updateFeature(previewIndexRef.current, feature);
        previewIndexRef.current = null;
      } else {
        addFeature(feature);
      }
      setIsDrawing(false);
    };

    let cleanup: (() => void) | null = null;

    switch (drawingMode as DrawingMode) {
      case "point":
        cleanup = createPointHandler(map, onAdd);
        break;
      case "line":
        cleanup = createLineHandler(map, onUpdate, onFinish);
        break;
      case "polygon":
        cleanup = createPolygonHandler(map, onUpdate, onFinish);
        break;
      case "rectangle":
        cleanup = createRectangleHandler(map, onUpdate, onFinish);
        break;
      case "circle":
        cleanup = createCircleHandler(map, onUpdate, onFinish);
        break;
      case "freehand":
        cleanup = createFreehandHandler(map, onUpdate, onFinish);
        break;
      default:
        setIsDrawing(false);
        break;
    }

    cleanupRef.current = cleanup;

    return () => {
      if (cleanupRef.current) {
        cleanupRef.current();
        cleanupRef.current = null;
      }
    };
  }, [map, drawingMode]);
}
