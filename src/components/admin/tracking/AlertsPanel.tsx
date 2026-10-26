"use client";

import { Check } from "lucide-react";
import { trpc } from "@/lib/trpc/client";
import { TOUCH_TARGET } from "@/components/ui/touch-target";

const ALERT_STYLES: Record<string, string> = {
  geofence_enter: "bg-emerald-500/20 text-emerald-300",
  geofence_exit: "bg-amber-500/20 text-amber-300",
};

export function AlertsPanel() {
  const utils = trpc.useUtils();
  const alerts = trpc.tracking.getAlerts.useQuery(undefined, { refetchInterval: 15_000 });

  const acknowledgeAlert = trpc.tracking.acknowledgeAlert.useMutation({
    onSuccess: async () => {
      await utils.tracking.getAlerts.invalidate();
    },
  });

  if (alerts.isLoading) return <p className="text-slate-400">Loading alerts…</p>;
  if (alerts.isError)
    return <p className="text-rose-300 font-mono text-sm">{alerts.error.message}</p>;

  const rows = alerts.data ?? [];
  if (rows.length === 0)
    return <p className="text-slate-400">No alerts recorded. An empty table is the honest reading.</p>;

  return (
    <ul className="space-y-2">
      {rows.map((alert) => (
        <li
          key={alert.id}
          className="flex items-center justify-between gap-4 bg-slate-900/80 border border-slate-800 rounded-xl p-4"
        >
          <div>
            <span
              className={`px-2 py-0.5 rounded text-xs font-semibold ${
                ALERT_STYLES[alert.type] ?? "bg-slate-700/40 text-slate-300"
              }`}
            >
              {alert.type}
            </span>
            <p className="mt-1 text-sm text-slate-200">{alert.message}</p>
            <p className="text-xs text-slate-500">
              {alert.createdAt ? new Date(alert.createdAt).toLocaleString() : "—"}
            </p>
          </div>
          {alert.acknowledged ? (
            <span className="text-xs text-slate-500 flex items-center gap-1">
              <Check className="w-3.5 h-3.5" /> acknowledged
            </span>
          ) : (
            <button
              type="button"
              onClick={() => acknowledgeAlert.mutate({ id: alert.id })}
              disabled={acknowledgeAlert.isPending}
              className={`flex items-center gap-2 px-3 py-1.5 bg-slate-800 hover:bg-slate-700 disabled:opacity-50 text-slate-200 text-xs font-medium rounded-lg ${TOUCH_TARGET}`}
            >
              <Check className="w-3.5 h-3.5" />
              Acknowledge
            </button>
          )}
        </li>
      ))}
    </ul>
  );
}
