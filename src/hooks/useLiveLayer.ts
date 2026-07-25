"use client";

import { useEffect, useRef, useCallback } from "react";
import type { Map as MapLibreMap, GeoJSONSource } from "maplibre-gl";
import type { Feature, FeatureCollection } from "geojson";
import { useSSE } from "./useSSE";
import { useRealtimeStore } from "@/stores/realtime-store";

interface UseLiveLayerOptions {
  /** MapLibre map instance */
  map: MapLibreMap | null;
  /** Layer/source id to update */
  layerId: string;
  /** Flash highlight paint property name (e.g. 'circle-color', 'fill-color') */
  flashPaintProperty?: string;
  /** Flash highlight color */
  flashColor?: string;
  /** Original paint property value to restore after flash */
  originalColor?: string;
  /** Flash duration in ms */
  flashDurationMs?: number;
}

const MAX_LIVE_FEATURES = 5_000;
const MAX_FEATURES_PER_EVENT = 500;

/** Returns the publisher-assigned identity required for stream replacement semantics. */
export function getStableFeatureId(feature: Feature): string | null {
  const propertyId = (feature.properties as Record<string, unknown> | null)?.id;
  const candidate = feature.id ?? propertyId;
  if (typeof candidate === "number") {
    return Number.isFinite(candidate) ? String(candidate) : null;
  }
  if (typeof candidate === "string") {
    const normalized = candidate.trim();
    return normalized.length > 0 ? normalized : null;
  }
  return null;
}

/** Upserts one feature while retaining only the most recent bounded set. */
export function upsertBoundedFeature(
  features: Map<string, Feature>,
  feature: Feature,
  maximum = MAX_LIVE_FEATURES
): boolean {
  const id = getStableFeatureId(feature);
  if (id === null || maximum < 1) return false;
  features.delete(id);
  features.set(id, feature);
  while (features.size > maximum) {
    const oldestId = features.keys().next().value as string | undefined;
    if (oldestId === undefined) break;
    features.delete(oldestId);
  }
  return true;
}

/**
 * Connects to the SSE stream for a layer, applies incoming GeoJSON features
 * to the MapLibre source via setData(), and triggers a brief flash animation
 * on the layer to signal new data.
 */
export function useLiveLayer({
  map,
  layerId,
  flashPaintProperty,
  flashColor = "#ffffff",
  originalColor = "#ff6600",
  flashDurationMs = 400,
}: UseLiveLayerOptions) {
  const addConnection = useRealtimeStore((s) => s.addConnection);
  const updateConnection = useRealtimeStore((s) => s.updateConnection);
  const removeConnection = useRealtimeStore((s) => s.removeConnection);

  // Accumulate features received over the stream
  const featuresRef = useRef<Map<string, Feature>>(new Map());
  const flashTimerRef = useRef<ReturnType<typeof setTimeout>>(undefined);
  const updateFrameRef = useRef<number | null>(null);

  const applyFlash = useCallback(() => {
    if (!map || !flashPaintProperty) return;
    try {
      map.setPaintProperty(layerId, flashPaintProperty, flashColor);
      clearTimeout(flashTimerRef.current);
      flashTimerRef.current = setTimeout(() => {
        try {
          map.setPaintProperty(layerId, flashPaintProperty, originalColor);
        } catch {
          // Layer may have been removed
        }
      }, flashDurationMs);
    } catch {
      // Layer may not exist yet
    }
  }, [map, layerId, flashPaintProperty, flashColor, originalColor, flashDurationMs]);

  const handleMessage = useCallback(
    (data: unknown) => {
      if (!map) return;

      let changed = false;
      const feature = data as Feature;
      if (feature?.type === "Feature") {
        changed = upsertBoundedFeature(featuresRef.current, feature);
      } else if (
        (data as FeatureCollection)?.type === "FeatureCollection" &&
        Array.isArray((data as FeatureCollection).features)
      ) {
        const fc = data as FeatureCollection;
        for (const f of fc.features.slice(0, MAX_FEATURES_PER_EVENT)) {
          changed = upsertBoundedFeature(featuresRef.current, f) || changed;
        }
      }

      if (!changed || updateFrameRef.current !== null) return;
      updateFrameRef.current = window.requestAnimationFrame(() => {
        updateFrameRef.current = null;
        const source = map.getSource(layerId) as GeoJSONSource | undefined;
        if (!source) return;
        source.setData({
          type: "FeatureCollection",
          features: Array.from(featuresRef.current.values()),
        });
        applyFlash();
      });
    },
    [map, layerId, applyFlash]
  );

  const { connectionState } = useSSE(`/api/stream/${layerId}`, {
    onMessage: handleMessage,
  });

  // Register connection state in realtime store
  useEffect(() => {
    addConnection(layerId, "connecting");
    return () => {
      removeConnection(layerId);
    };
  }, [layerId, addConnection, removeConnection]);

  useEffect(() => {
    updateConnection(layerId, connectionState);
  }, [layerId, connectionState, updateConnection]);

  useEffect(() => {
    const featureStore = featuresRef.current;
    if (updateFrameRef.current !== null) {
      window.cancelAnimationFrame(updateFrameRef.current);
      updateFrameRef.current = null;
    }
    featureStore.clear();
    return () => {
      if (updateFrameRef.current !== null) {
        window.cancelAnimationFrame(updateFrameRef.current);
        updateFrameRef.current = null;
      }
      featureStore.clear();
    };
  }, [map, layerId]);

  useEffect(() => {
    return () => {
      clearTimeout(flashTimerRef.current);
      if (updateFrameRef.current !== null) {
        window.cancelAnimationFrame(updateFrameRef.current);
        updateFrameRef.current = null;
      }
    };
  }, []);

  return { connectionState };
}
