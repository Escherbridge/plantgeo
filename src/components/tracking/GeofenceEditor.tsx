"use client";

import { useState } from "react";
import { api } from "@/lib/trpc/client";
import { useDrawingStore } from "@/stores/drawing-store";
import { useTrackingStore } from "@/stores/tracking-store";

interface GeofenceEditorProps {
  onClose: () => void;
}

export function GeofenceEditor({ onClose }: GeofenceEditorProps) {
  const [name, setName] = useState("");
  const [alertOnEnter, setAlertOnEnter] = useState(true);
  const [alertOnExit, setAlertOnExit] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const { features, setMode } = useDrawingStore();
  const addGeofence = useTrackingStore((s) => s.addGeofence);

  const createMutation = api.tracking.createGeofence.useMutation({
    onSuccess: (geofence) => {
      addGeofence({
        id: geofence.id,
        name: geofence.name,
        geometry: geofence.geometry as GeoJSON.Polygon,
        alertOnEnter: geofence.alertOnEnter ?? true,
        alertOnExit: geofence.alertOnExit ?? true,
      });
      onClose();
    },
    onError: (err) => setError(err.message),
  });

  const polygonFeature = features.features.find(
    (f) => f.geometry.type === "Polygon"
  );

  function handleStartDrawing() {
    setMode("polygon");
  }

  function handleSave() {
    if (!name.trim()) {
      setError("Name is required");
      return;
    }
    if (!polygonFeature) {
      setError("Draw a polygon on the map first");
      return;
    }

    setError(null);
    createMutation.mutate({
      name: name.trim(),
      geometry: polygonFeature.geometry as unknown as { type: "Polygon"; coordinates: [number, number][][] },
      alertOnEnter,
      alertOnExit,
    });
  }

  return (
    <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50">
      <div className="bg-gray-900 border border-gray-700 rounded-lg p-6 w-96 text-white shadow-xl">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-semibold">Create Geofence</h2>
          <button
            onClick={onClose}
            className="text-gray-400 hover:text-white transition-colors text-xl leading-none"
          >
            ×
          </button>
        </div>

        <div className="space-y-4">
          <div>
            <label className="block text-sm text-gray-400 mb-1">Name</label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. Warehouse Zone A"
              className="w-full px-3 py-2 bg-gray-800 border border-gray-600 rounded text-sm text-white placeholder-gray-400 focus:outline-none focus:border-emerald-500"
            />
          </div>

          <div>
            <label className="block text-sm text-gray-400 mb-2">
              Polygon
            </label>
            {polygonFeature ? (
              <div className="flex items-center gap-2 px-3 py-2 bg-emerald-500/10 border border-emerald-500/30 rounded text-sm text-emerald-400">
                <span>Polygon drawn</span>
                <button
                  onClick={handleStartDrawing}
                  className="ml-auto text-xs text-gray-400 hover:text-white"
                >
                  Redraw
                </button>
              </div>
            ) : (
              <button
                onClick={handleStartDrawing}
                className="w-full py-2 bg-gray-700 hover:bg-gray-600 border border-dashed border-gray-500 rounded text-sm transition-colors"
              >
                Draw on Map
              </button>
            )}
          </div>

          <div className="space-y-2">
            <label className="block text-sm text-gray-400">Alerts</label>
            <label className="flex items-center gap-2 text-sm cursor-pointer">
              <input
                type="checkbox"
                checked={alertOnEnter}
                onChange={(e) => setAlertOnEnter(e.target.checked)}
                className="accent-emerald-500"
              />
              Alert on entry
            </label>
            <label className="flex items-center gap-2 text-sm cursor-pointer">
              <input
                type="checkbox"
                checked={alertOnExit}
                onChange={(e) => setAlertOnExit(e.target.checked)}
                className="accent-emerald-500"
              />
              Alert on exit
            </label>
          </div>

          {error && (
            <p className="text-xs text-red-400 bg-red-400/10 px-3 py-2 rounded">
              {error}
            </p>
          )}

          <div className="flex gap-3 pt-1">
            <button
              onClick={onClose}
              className="flex-1 py-2 bg-gray-700 hover:bg-gray-600 rounded text-sm transition-colors"
            >
              Cancel
            </button>
            <button
              onClick={handleSave}
              disabled={createMutation.isPending}
              className="flex-1 py-2 bg-emerald-600 hover:bg-emerald-700 disabled:opacity-50 rounded text-sm font-medium transition-colors"
            >
              {createMutation.isPending ? "Saving..." : "Save Geofence"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
