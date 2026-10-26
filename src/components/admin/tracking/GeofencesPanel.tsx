"use client";

import { useState } from "react";
import { z } from "zod";
import { Plus, Trash2 } from "lucide-react";
import { trpc } from "@/lib/trpc/client";
import { TOUCH_TARGET, TOUCH_TARGET_SQUARE, NO_IOS_FOCUS_ZOOM } from "@/components/ui/touch-target";

/**
 * Mirrors `geofenceCreateSchema.geometry` in `routers/tracking.ts` exactly, so a shape the
 * textarea accepts is guaranteed to pass the server's own validation too -- this is client-side
 * UX only, never the authorization or shape boundary (that stays server-side, same as every
 * other admin mutation here).
 */
const polygonGeometrySchema = z.object({
  type: z.literal("Polygon"),
  coordinates: z.array(z.array(z.tuple([z.number(), z.number()]))),
});

const SAMPLE_GEOMETRY = `{
  "type": "Polygon",
  "coordinates": [[[-105, 39], [-104, 39], [-104, 40], [-105, 39]]]
}`;

export function GeofencesPanel() {
  const utils = trpc.useUtils();
  const [name, setName] = useState("");
  const [geometryText, setGeometryText] = useState("");
  const [geometryError, setGeometryError] = useState<string | null>(null);
  const [alertOnEnter, setAlertOnEnter] = useState(true);
  const [alertOnExit, setAlertOnExit] = useState(true);

  const geofences = trpc.tracking.listGeofences.useQuery(undefined, { refetchInterval: 30_000 });

  const createGeofence = trpc.tracking.createGeofence.useMutation({
    onSuccess: async () => {
      setName("");
      setGeometryText("");
      setGeometryError(null);
      await utils.tracking.listGeofences.invalidate();
    },
    onError: (error) => setGeometryError(error.message),
  });

  const deleteGeofence = trpc.tracking.deleteGeofence.useMutation({
    onSuccess: async () => {
      await utils.tracking.listGeofences.invalidate();
    },
  });

  function submitCreate(event: React.FormEvent) {
    event.preventDefault();
    if (!name.trim()) return;

    let parsed: unknown;
    try {
      parsed = JSON.parse(geometryText);
    } catch {
      setGeometryError("Geometry must be valid JSON.");
      return;
    }

    const result = polygonGeometrySchema.safeParse(parsed);
    if (!result.success) {
      setGeometryError(
        `Geometry must be a GeoJSON Polygon: {"type":"Polygon","coordinates":[[[lng,lat], ...]]}.`
      );
      return;
    }

    setGeometryError(null);
    createGeofence.mutate({ name: name.trim(), geometry: result.data, alertOnEnter, alertOnExit });
  }

  return (
    <div className="space-y-6">
      <form
        onSubmit={submitCreate}
        className="space-y-3 bg-slate-900/60 border border-slate-800 p-4 rounded-xl"
      >
        <input
          value={name}
          onChange={(event) => setName(event.target.value)}
          placeholder="Geofence name"
          aria-label="New geofence name"
          className={`w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-slate-100 ${NO_IOS_FOCUS_ZOOM} ${TOUCH_TARGET}`}
        />
        <textarea
          value={geometryText}
          onChange={(event) => setGeometryText(event.target.value)}
          placeholder={SAMPLE_GEOMETRY}
          aria-label="Geofence GeoJSON Polygon geometry"
          rows={5}
          className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-slate-100 font-mono text-xs"
        />
        {geometryError && <p className="text-rose-300 text-xs">{geometryError}</p>}
        <div className="flex items-center gap-6 text-sm text-slate-300">
          <label className="flex items-center gap-2">
            <input
              type="checkbox"
              checked={alertOnEnter}
              onChange={(event) => setAlertOnEnter(event.target.checked)}
            />
            Alert on enter
          </label>
          <label className="flex items-center gap-2">
            <input
              type="checkbox"
              checked={alertOnExit}
              onChange={(event) => setAlertOnExit(event.target.checked)}
            />
            Alert on exit
          </label>
        </div>
        <button
          type="submit"
          disabled={createGeofence.isPending || !name.trim() || !geometryText.trim()}
          className={`flex items-center gap-2 px-4 py-2 bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 text-white font-medium rounded-lg ${TOUCH_TARGET}`}
        >
          <Plus className="w-4 h-4" />
          Add geofence
        </button>
      </form>

      {geofences.isLoading ? (
        <p className="text-slate-400">Loading geofences…</p>
      ) : geofences.isError ? (
        <p className="text-rose-300 font-mono text-sm">{geofences.error.message}</p>
      ) : (geofences.data ?? []).length === 0 ? (
        <p className="text-slate-400">No geofences defined yet.</p>
      ) : (
        <ul className="space-y-2">
          {(geofences.data ?? []).map((geofence) => (
            <li
              key={geofence.id}
              className="flex items-center justify-between bg-slate-900/80 border border-slate-800 rounded-xl p-4"
            >
              <div>
                <p className="font-semibold text-white">{geofence.name}</p>
                <p className="text-xs text-slate-500">
                  enter: {geofence.alertOnEnter ? "on" : "off"} · exit:{" "}
                  {geofence.alertOnExit ? "on" : "off"}
                </p>
              </div>
              <button
                type="button"
                onClick={() => deleteGeofence.mutate({ id: geofence.id })}
                disabled={deleteGeofence.isPending}
                aria-label={`Delete geofence ${geofence.name}`}
                className={`p-2 rounded-lg text-rose-300 hover:bg-rose-500/10 disabled:opacity-50 ${TOUCH_TARGET_SQUARE}`}
              >
                <Trash2 className="w-4 h-4" />
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
