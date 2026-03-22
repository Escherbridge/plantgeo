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

      const feature = data as Feature;
      if (feature?.type === "Feature") {
        const id = String(
          feature.id ?? (feature.properties as Record<string, unknown>)?.id ?? Math.random()
        );
        featuresRef.current.set(id, feature);
      } else if ((data as FeatureCollection)?.type === "FeatureCollection") {
        const fc = data as FeatureCollection;
        for (const f of fc.features) {
          const id = String(
            f.id ?? (f.properties as Record<string, unknown>)?.id ?? Math.random()
          );
          featuresRef.current.set(id, f);
        }
      }

      const source = map.getSource(layerId) as GeoJSONSource | undefined;
      if (source) {
        const featureCollection: FeatureCollection = {
          type: "FeatureCollection",
          features: Array.from(featuresRef.current.values()),
        };
        source.setData(featureCollection);
        applyFlash();
      }
    },
    [map, layerId, applyFlash]
  );

  const { connectionState } = useSSE(`/api/stream/${layerId}`, {
    onMessage: handleMessage,
  });

  // Register connection state in realtime store
  useEffect(() => {
    addConnection(layerId, connectionState);
    return () => {
      removeConnection(layerId);
    };
  }, [layerId, addConnection, removeConnection]);

  useEffect(() => {
    updateConnection(layerId, connectionState);
  }, [layerId, connectionState, updateConnection]);

  // Cleanup flash timer on unmount
  useEffect(() => {
    return () => {
      clearTimeout(flashTimerRef.current);
    };
  }, []);

  return { connectionState };
}
