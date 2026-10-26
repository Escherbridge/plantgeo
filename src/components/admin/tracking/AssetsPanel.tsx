"use client";

import { useMemo, useState } from "react";
import dynamic from "next/dynamic";
import { Plus, RefreshCw, MapPin, Clock } from "lucide-react";
import { trpc } from "@/lib/trpc/client";
import { TOUCH_TARGET, NO_IOS_FOCUS_ZOOM } from "@/components/ui/touch-target";

const AssetRouteMap = dynamic(() => import("@/components/admin/tracking/AssetRouteMap"), {
  ssr: false,
});

const ASSET_STATUSES = ["active", "idle", "offline"] as const;

/** `datetime-local`'s own value format, floored to the minute. */
function toDatetimeLocal(date: Date): string {
  const pad = (value: number) => String(value).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(
    date.getHours()
  )}:${pad(date.getMinutes())}`;
}

interface NewAssetForm {
  name: string;
  type: string;
  status: (typeof ASSET_STATUSES)[number];
}

const EMPTY_FORM: NewAssetForm = { name: "", type: "vehicle", status: "offline" };

/**
 * Assets tab: create/list fleet assets, then drill into one asset's route history on a small
 * MapLibre map. `getLastPositions` seeds a "last seen" badge per row rather than a second
 * per-asset query, since it is already bounded to the live fleet (services/tracking.ts).
 */
export function AssetsPanel() {
  const utils = trpc.useUtils();
  const [form, setForm] = useState<NewAssetForm>(EMPTY_FORM);
  const [selectedAssetId, setSelectedAssetId] = useState<string | null>(null);
  const [rangeTo, setRangeTo] = useState(() => toDatetimeLocal(new Date()));
  const [rangeFrom, setRangeFrom] = useState(() =>
    toDatetimeLocal(new Date(Date.now() - 24 * 60 * 60 * 1000))
  );

  const assets = trpc.tracking.listAssets.useQuery(undefined, { refetchInterval: 30_000 });
  const lastPositions = trpc.tracking.getLastPositions.useQuery(undefined, {
    refetchInterval: 30_000,
  });
  const routeHistory = trpc.tracking.getRouteHistory.useQuery(
    {
      assetId: selectedAssetId ?? "",
      from: new Date(rangeFrom).toISOString(),
      to: new Date(rangeTo).toISOString(),
    },
    { enabled: selectedAssetId !== null }
  );

  const lastSeenByAsset = useMemo(() => {
    const map = new Map<string, Date>();
    for (const row of lastPositions.data ?? []) {
      map.set(row.asset_id, new Date(row.time));
    }
    return map;
  }, [lastPositions.data]);

  const createAsset = trpc.tracking.createAsset.useMutation({
    onSuccess: async () => {
      setForm(EMPTY_FORM);
      await utils.tracking.listAssets.invalidate();
    },
  });

  function submitCreate(event: React.FormEvent) {
    event.preventDefault();
    if (!form.name.trim()) return;
    createAsset.mutate({ name: form.name.trim(), type: form.type, status: form.status });
  }

  return (
    <div className="space-y-6">
      <form
        onSubmit={submitCreate}
        className="flex flex-col md:flex-row gap-3 bg-slate-900/60 border border-slate-800 p-4 rounded-xl"
      >
        <input
          value={form.name}
          onChange={(event) => setForm((previous) => ({ ...previous, name: event.target.value }))}
          placeholder="Asset name (e.g. Crew truck 4)"
          aria-label="New asset name"
          className={`flex-1 bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-slate-100 ${NO_IOS_FOCUS_ZOOM} ${TOUCH_TARGET}`}
        />
        <select
          value={form.type}
          onChange={(event) => setForm((previous) => ({ ...previous, type: event.target.value }))}
          aria-label="Asset type"
          className={`bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-slate-100 ${TOUCH_TARGET}`}
        >
          <option value="vehicle">Vehicle</option>
          <option value="crew">Crew</option>
          <option value="equipment">Equipment</option>
        </select>
        <select
          value={form.status}
          onChange={(event) =>
            setForm((previous) => ({
              ...previous,
              status: event.target.value as NewAssetForm["status"],
            }))
          }
          aria-label="Asset status"
          className={`bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-slate-100 ${TOUCH_TARGET}`}
        >
          {ASSET_STATUSES.map((status) => (
            <option key={status} value={status}>
              {status}
            </option>
          ))}
        </select>
        <button
          type="submit"
          disabled={createAsset.isPending || !form.name.trim()}
          className={`flex items-center justify-center gap-2 px-4 py-2 bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 text-white font-medium rounded-lg ${TOUCH_TARGET}`}
        >
          <Plus className="w-4 h-4" />
          Add asset
        </button>
      </form>

      {assets.isLoading ? (
        <p className="text-slate-400">Loading assets…</p>
      ) : assets.isError ? (
        <p className="text-rose-300 font-mono text-sm">{assets.error.message}</p>
      ) : (assets.data ?? []).length === 0 ? (
        <p className="text-slate-400">No assets registered yet.</p>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {(assets.data ?? []).map((asset) => {
            const lastSeen = lastSeenByAsset.get(asset.id);
            const isSelected = asset.id === selectedAssetId;
            return (
              <button
                key={asset.id}
                type="button"
                onClick={() => setSelectedAssetId(isSelected ? null : asset.id)}
                aria-pressed={isSelected}
                className={`text-left bg-slate-900/80 border p-4 rounded-xl transition ${TOUCH_TARGET} ${
                  isSelected
                    ? "border-emerald-500/50 bg-emerald-500/5"
                    : "border-slate-800 hover:border-slate-700"
                }`}
              >
                <div className="flex items-center justify-between">
                  <span className="font-semibold text-white">{asset.name}</span>
                  <span className="text-xs font-mono text-slate-400">{asset.status}</span>
                </div>
                <div className="mt-1 text-xs text-slate-500">
                  {asset.type} ·{" "}
                  {lastSeen ? (
                    <span className="inline-flex items-center gap-1">
                      <MapPin className="w-3 h-3" /> last seen {lastSeen.toLocaleString()}
                    </span>
                  ) : (
                    "no recent position"
                  )}
                </div>
              </button>
            );
          })}
        </div>
      )}

      {selectedAssetId && (
        <div className="bg-slate-900/80 border border-slate-800 rounded-2xl p-5 space-y-4">
          <div className="flex items-center gap-2 text-slate-300 text-sm font-semibold">
            <Clock className="w-4 h-4" /> Route history
          </div>
          <div className="flex flex-col md:flex-row gap-3">
            <label className="flex-1 text-xs text-slate-400">
              From
              <input
                type="datetime-local"
                value={rangeFrom}
                onChange={(event) => setRangeFrom(event.target.value)}
                className={`mt-1 w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-slate-100 ${NO_IOS_FOCUS_ZOOM} ${TOUCH_TARGET}`}
              />
            </label>
            <label className="flex-1 text-xs text-slate-400">
              To
              <input
                type="datetime-local"
                value={rangeTo}
                onChange={(event) => setRangeTo(event.target.value)}
                className={`mt-1 w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-slate-100 ${NO_IOS_FOCUS_ZOOM} ${TOUCH_TARGET}`}
              />
            </label>
            <button
              type="button"
              onClick={() => void utils.tracking.getRouteHistory.invalidate()}
              className={`self-end flex items-center gap-2 px-3 py-2 bg-slate-800 hover:bg-slate-700 text-slate-200 rounded-lg ${TOUCH_TARGET}`}
            >
              <RefreshCw className="w-4 h-4" />
              Refresh
            </button>
          </div>

          {routeHistory.isLoading ? (
            <p className="text-slate-400 text-sm">Reading route history…</p>
          ) : routeHistory.isError ? (
            <p className="text-rose-300 font-mono text-sm">{routeHistory.error.message}</p>
          ) : (routeHistory.data?.positions.length ?? 0) === 0 ? (
            <p className="text-slate-400 text-sm">No positions reported in this window.</p>
          ) : (
            <>
              <p className="text-xs text-slate-500">
                {routeHistory.data?.positions.length} position(s) ·{" "}
                {routeHistory.data?.stops.length ?? 0} stop(s) detected
              </p>
              <AssetRouteMap
                positions={routeHistory.data?.positions ?? []}
                stops={routeHistory.data?.stops ?? []}
              />
            </>
          )}
        </div>
      )}
    </div>
  );
}
