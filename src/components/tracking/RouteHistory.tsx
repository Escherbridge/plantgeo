"use client";

import { useState, useEffect, useRef, useCallback } from "react";
import { api } from "@/lib/trpc/client";

interface RoutePoint {
  asset_id: string;
  time: string;
  lat: number;
  lon: number;
  speed: number | null;
  heading: number | null;
}

interface RouteHistoryProps {
  assetId: string;
  assetName: string;
  map: import("maplibre-gl").Map;
}

function speedColor(speed: number | null): string {
  if (speed === null || speed < 5) return "#ef4444";
  if (speed < 30) return "#f59e0b";
  return "#10b981";
}

const PLAYBACK_SPEEDS = [1, 2, 5, 10];
const SOURCE_ID = "route-history-source";
const LAYER_ID = "route-history-layer";

export function RouteHistory({ assetId, assetName, map }: RouteHistoryProps) {
  const now = new Date();
  const oneHourAgo = new Date(now.getTime() - 60 * 60 * 1000);

  const [from, setFrom] = useState(oneHourAgo.toISOString().slice(0, 16));
  const [to, setTo] = useState(now.toISOString().slice(0, 16));
  const [playbackIndex, setPlaybackIndex] = useState(0);
  const [isPlaying, setIsPlaying] = useState(false);
  const [speed, setSpeed] = useState(1);

  const playbackRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const markerRef = useRef<import("maplibre-gl").Marker | null>(null);

  const { data: route = [], refetch } = api.tracking.getRouteHistory.useQuery(
    { assetId, from: new Date(from).toISOString(), to: new Date(to).toISOString() },
    { enabled: false }
  );

  const points: RoutePoint[] = (route as unknown as RoutePoint[]);

  useEffect(() => {
    return () => {
      if (playbackRef.current) clearInterval(playbackRef.current);
      markerRef.current?.remove();
      if (map.getLayer(LAYER_ID)) map.removeLayer(LAYER_ID);
      if (map.getSource(SOURCE_ID)) map.removeSource(SOURCE_ID);
    };
  }, [map]);

  useEffect(() => {
    if (points.length < 2) return;

    const features = points.slice(0, -1).map((pt, i) => ({
      type: "Feature" as const,
      properties: { color: speedColor(points[i].speed) },
      geometry: {
        type: "LineString" as const,
        coordinates: [
          [pt.lon, pt.lat],
          [points[i + 1].lon, points[i + 1].lat],
        ],
      },
    }));

    const geojson: GeoJSON.FeatureCollection = {
      type: "FeatureCollection",
      features,
    };

    if (map.getSource(SOURCE_ID)) {
      (map.getSource(SOURCE_ID) as import("maplibre-gl").GeoJSONSource).setData(geojson);
    } else {
      map.addSource(SOURCE_ID, { type: "geojson", data: geojson });
      map.addLayer({
        id: LAYER_ID,
        type: "line",
        source: SOURCE_ID,
        paint: {
          "line-color": ["get", "color"],
          "line-width": 3,
          "line-opacity": 0.85,
        },
      });
    }
  }, [map, points]);

  const stopPlayback = useCallback(() => {
    if (playbackRef.current) {
      clearInterval(playbackRef.current);
      playbackRef.current = null;
    }
    setIsPlaying(false);
  }, []);

  const startPlayback = useCallback(() => {
    if (points.length === 0) return;
    setIsPlaying(true);

    playbackRef.current = setInterval(() => {
      setPlaybackIndex((idx) => {
        const next = idx + 1;
        if (next >= points.length) {
          stopPlayback();
          return idx;
        }
        return next;
      });
    }, 500 / speed);
  }, [points, speed, stopPlayback]);

  useEffect(() => {
    if (!isPlaying) return;
    stopPlayback();
    startPlayback();
  }, [speed]);

  useEffect(() => {
    if (points.length === 0 || playbackIndex >= points.length) return;

    const pt = points[playbackIndex];
    import("maplibre-gl").then(({ Marker }) => {
      if (!markerRef.current) {
        const el = document.createElement("div");
        el.style.cssText =
          "width:14px;height:14px;border-radius:50%;background:#fff;border:3px solid #10b981;box-shadow:0 0 6px rgba(16,185,129,0.8);";
        markerRef.current = new Marker({ element: el, anchor: "center" })
          .setLngLat([pt.lon, pt.lat])
          .addTo(map);
      } else {
        markerRef.current.setLngLat([pt.lon, pt.lat]);
      }
    });
  }, [map, points, playbackIndex]);

  function handleFetch() {
    setPlaybackIndex(0);
    stopPlayback();
    refetch();
  }

  return (
    <div className="bg-gray-900 text-white p-4 rounded-lg w-80">
      <h3 className="text-sm font-semibold mb-3">
        Route History — {assetName}
      </h3>

      <div className="space-y-2 mb-3">
        <div>
          <label className="text-xs text-gray-400 block mb-1">From</label>
          <input
            type="datetime-local"
            value={from}
            onChange={(e) => setFrom(e.target.value)}
            className="w-full px-2 py-1.5 bg-gray-800 border border-gray-600 rounded text-xs text-white focus:outline-none focus:border-emerald-500"
          />
        </div>
        <div>
          <label className="text-xs text-gray-400 block mb-1">To</label>
          <input
            type="datetime-local"
            value={to}
            onChange={(e) => setTo(e.target.value)}
            className="w-full px-2 py-1.5 bg-gray-800 border border-gray-600 rounded text-xs text-white focus:outline-none focus:border-emerald-500"
          />
        </div>
        <button
          onClick={handleFetch}
          className="w-full py-1.5 bg-emerald-600 hover:bg-emerald-700 rounded text-xs font-medium transition-colors"
        >
          Load Route
        </button>
      </div>

      {points.length > 0 && (
        <div>
          <div className="text-xs text-gray-400 mb-2">
            {points.length} points · index {playbackIndex + 1}/{points.length}
          </div>

          <div className="flex items-center gap-2 mb-2">
            <button
              onClick={isPlaying ? stopPlayback : startPlayback}
              className="px-3 py-1.5 bg-emerald-600 hover:bg-emerald-700 rounded text-xs font-medium transition-colors"
            >
              {isPlaying ? "Pause" : "Play"}
            </button>

            <button
              onClick={() => {
                stopPlayback();
                setPlaybackIndex(0);
              }}
              className="px-3 py-1.5 bg-gray-700 hover:bg-gray-600 rounded text-xs transition-colors"
            >
              Reset
            </button>

            <select
              value={speed}
              onChange={(e) => setSpeed(Number(e.target.value))}
              className="px-2 py-1.5 bg-gray-800 border border-gray-600 rounded text-xs text-white focus:outline-none"
            >
              {PLAYBACK_SPEEDS.map((s) => (
                <option key={s} value={s}>
                  {s}x
                </option>
              ))}
            </select>
          </div>

          <input
            type="range"
            min={0}
            max={points.length - 1}
            value={playbackIndex}
            onChange={(e) => {
              stopPlayback();
              setPlaybackIndex(Number(e.target.value));
            }}
            className="w-full accent-emerald-500"
          />

          <div className="flex gap-3 mt-2 text-xs">
            <span className="flex items-center gap-1">
              <span className="w-3 h-1.5 bg-emerald-500 rounded inline-block" />
              Fast
            </span>
            <span className="flex items-center gap-1">
              <span className="w-3 h-1.5 bg-amber-500 rounded inline-block" />
              Slow
            </span>
            <span className="flex items-center gap-1">
              <span className="w-3 h-1.5 bg-red-500 rounded inline-block" />
              Stopped
            </span>
          </div>
        </div>
      )}
    </div>
  );
}
