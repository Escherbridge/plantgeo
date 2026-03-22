"use client";

import { useState, useMemo } from "react";
import { api } from "@/lib/trpc/client";
import { useTrackingStore } from "@/stores/tracking-store";

type StatusFilter = "all" | "active" | "idle" | "offline";

const STATUS_BADGE: Record<string, string> = {
  active: "bg-emerald-500/20 text-emerald-400",
  idle: "bg-amber-500/20 text-amber-400",
  offline: "bg-red-500/20 text-red-400",
};

interface FleetPanelProps {
  onFlyTo?: (lat: number, lon: number) => void;
}

export function FleetPanel({ onFlyTo }: FleetPanelProps) {
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");

  const { selectVehicle, vehicles } = useTrackingStore();

  const { data: assets = [] } = api.tracking.listAssets.useQuery();

  const counts = useMemo(() => {
    const total = assets.length;
    const active = assets.filter((a) => a.status === "active").length;
    const idle = assets.filter((a) => a.status === "idle").length;
    const offline = assets.filter((a) => a.status === "offline").length;
    return { total, active, idle, offline };
  }, [assets]);

  const filtered = useMemo(() => {
    return assets.filter((asset) => {
      const matchesSearch = asset.name
        .toLowerCase()
        .includes(search.toLowerCase());
      const matchesStatus =
        statusFilter === "all" || asset.status === statusFilter;
      return matchesSearch && matchesStatus;
    });
  }, [assets, search, statusFilter]);

  function handleAssetClick(assetId: string) {
    selectVehicle(assetId);
    const pos = vehicles.get(assetId);
    if (pos && onFlyTo) {
      onFlyTo(pos.lat, pos.lon);
    }
  }

  return (
    <div className="flex flex-col h-full bg-gray-900 text-white">
      <div className="p-4 border-b border-gray-700">
        <h2 className="text-lg font-semibold mb-3">Fleet Tracking</h2>

        <div className="flex gap-2 mb-3 flex-wrap">
          <span className="text-xs px-2 py-1 rounded bg-gray-700 text-gray-300">
            Total: {counts.total}
          </span>
          <span className="text-xs px-2 py-1 rounded bg-emerald-500/20 text-emerald-400">
            Active: {counts.active}
          </span>
          <span className="text-xs px-2 py-1 rounded bg-amber-500/20 text-amber-400">
            Idle: {counts.idle}
          </span>
          <span className="text-xs px-2 py-1 rounded bg-red-500/20 text-red-400">
            Offline: {counts.offline}
          </span>
        </div>

        <input
          type="text"
          placeholder="Search vehicles..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="w-full px-3 py-2 bg-gray-800 border border-gray-600 rounded text-sm text-white placeholder-gray-400 focus:outline-none focus:border-emerald-500"
        />

        <div className="flex gap-1 mt-2">
          {(["all", "active", "idle", "offline"] as StatusFilter[]).map(
            (s) => (
              <button
                key={s}
                onClick={() => setStatusFilter(s)}
                className={`text-xs px-3 py-1 rounded capitalize transition-colors ${
                  statusFilter === s
                    ? "bg-emerald-600 text-white"
                    : "bg-gray-700 text-gray-300 hover:bg-gray-600"
                }`}
              >
                {s}
              </button>
            )
          )}
        </div>
      </div>

      <div className="flex-1 overflow-y-auto">
        {filtered.length === 0 ? (
          <div className="p-4 text-center text-gray-500 text-sm">
            No vehicles found
          </div>
        ) : (
          <ul className="divide-y divide-gray-700/50">
            {filtered.map((asset) => {
              const pos = vehicles.get(asset.id);
              return (
                <li key={asset.id}>
                  <button
                    onClick={() => handleAssetClick(asset.id)}
                    className="w-full text-left px-4 py-3 hover:bg-gray-800 transition-colors"
                  >
                    <div className="flex items-center justify-between">
                      <span className="text-sm font-medium">{asset.name}</span>
                      <span
                        className={`text-xs px-2 py-0.5 rounded-full ${STATUS_BADGE[asset.status ?? "offline"] ?? STATUS_BADGE.offline}`}
                      >
                        {asset.status ?? "offline"}
                      </span>
                    </div>
                    <div className="text-xs text-gray-400 mt-0.5">
                      {asset.type ?? "vehicle"}
                      {pos && (
                        <span className="ml-2">
                          {pos.speed != null
                            ? `${Math.round(pos.speed)} km/h`
                            : ""}
                        </span>
                      )}
                    </div>
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </div>
  );
}
