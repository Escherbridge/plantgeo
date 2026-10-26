"use client";

import { useState } from "react";
import { Truck, ShieldAlert, Siren } from "lucide-react";
import { TOUCH_TARGET } from "@/components/ui/touch-target";
import { AssetsPanel } from "@/components/admin/tracking/AssetsPanel";
import { GeofencesPanel } from "@/components/admin/tracking/GeofencesPanel";
import { AlertsPanel } from "@/components/admin/tracking/AlertsPanel";

type TrackingTab = "assets" | "geofences" | "alerts";

const TABS: Array<{ id: TrackingTab; label: string; icon: typeof Truck }> = [
  { id: "assets", label: "Assets", icon: Truck },
  { id: "geofences", label: "Geofences", icon: ShieldAlert },
  { id: "alerts", label: "Alerts", icon: Siren },
];

/**
 * The platform admin view of `tracking.assets` / `tracking.positions` / `tracking.geofences` /
 * `tracking.alerts`, over the `tracking` tRPC router (`adminProcedure` gates every procedure;
 * see src/app/admin/layout.tsx for the page-level gate this panel relies on in addition).
 */
export function TrackingAdminDashboard() {
  const [activeTab, setActiveTab] = useState<TrackingTab>("assets");

  return (
    <div className="w-full max-w-6xl mx-auto p-6 space-y-6 text-slate-100">
      <div className="bg-slate-900/80 backdrop-blur-md border border-slate-800 p-6 rounded-2xl shadow-xl">
        <h1 className="text-2xl font-bold tracking-tight text-white">Asset Tracking</h1>
        <p className="text-slate-400 text-sm mt-1">
          Fleet assets, geofences and alerts over <code className="font-mono">tracking.*</code>.
        </p>
      </div>

      <div className="flex space-x-2 border-b border-slate-800 pb-2">
        {TABS.map(({ id, label, icon: Icon }) => (
          <button
            key={id}
            onClick={() => setActiveTab(id)}
            className={`flex items-center gap-2 px-4 py-2 rounded-xl text-sm font-medium transition ${TOUCH_TARGET} ${
              activeTab === id
                ? "bg-emerald-500/20 text-emerald-300 border border-emerald-500/30"
                : "text-slate-400 hover:text-slate-200"
            }`}
          >
            <Icon className="w-4 h-4" />
            {label}
          </button>
        ))}
      </div>

      {activeTab === "assets" && <AssetsPanel />}
      {activeTab === "geofences" && <GeofencesPanel />}
      {activeTab === "alerts" && <AlertsPanel />}
    </div>
  );
}
