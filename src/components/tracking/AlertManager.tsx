"use client";

import { useEffect, useRef } from "react";
import { useAlertsStore, type Alert } from "@/stores/alerts-store";
import { api } from "@/lib/trpc/client";

const TYPE_LABELS: Record<string, string> = {
  geofence_breach: "Geofence Breach",
  speed_threshold: "Speed Alert",
  offline: "Vehicle Offline",
};

const TYPE_COLORS: Record<string, string> = {
  geofence_breach: "border-amber-500/40 bg-amber-500/5",
  speed_threshold: "border-red-500/40 bg-red-500/5",
  offline: "border-gray-500/40 bg-gray-500/5",
};

interface AlertItemProps {
  alert: Alert;
  onAcknowledge: (id: string) => void;
}

function AlertItem({ alert, onAcknowledge }: AlertItemProps) {
  const colorClass =
    TYPE_COLORS[alert.type] ?? "border-gray-500/40 bg-gray-500/5";
  const date = new Date(alert.createdAt);
  const timeStr = date.toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
  });

  return (
    <div
      className={`border rounded-lg p-3 text-sm ${colorClass} ${alert.acknowledged ? "opacity-50" : ""}`}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="flex-1 min-w-0">
          <div className="font-medium text-xs text-gray-300 mb-0.5">
            {TYPE_LABELS[alert.type] ?? alert.type}
          </div>
          <div className="text-white">{alert.message}</div>
          <div className="text-xs text-gray-400 mt-1">{timeStr}</div>
        </div>
        {!alert.acknowledged && (
          <button
            onClick={() => onAcknowledge(alert.id)}
            className="shrink-0 text-xs px-2 py-1 bg-gray-700 hover:bg-gray-600 rounded transition-colors"
          >
            Ack
          </button>
        )}
      </div>
    </div>
  );
}

interface AlertManagerProps {
  wsAlerts?: Alert[];
}

export function AlertManager({ wsAlerts }: AlertManagerProps) {
  const { alerts, addAlert, acknowledgeAlert, clearAll, unreadCount } =
    useAlertsStore();

  const { data: serverAlerts = [] } = api.tracking.getAlerts.useQuery(
    undefined,
    { refetchInterval: 30_000 }
  );

  const acknowledgeServerAlert = api.tracking.acknowledgeAlert.useMutation();

  const seenServerIds = useRef(new Set<string>());

  useEffect(() => {
    for (const sa of serverAlerts as unknown as Alert[]) {
      if (!seenServerIds.current.has(sa.id)) {
        seenServerIds.current.add(sa.id);
        addAlert(sa);
      }
    }
  }, [serverAlerts, addAlert]);

  useEffect(() => {
    if (!wsAlerts) return;
    for (const alert of wsAlerts) {
      if (!seenServerIds.current.has(alert.id)) {
        seenServerIds.current.add(alert.id);
        addAlert(alert);
      }
    }
  }, [wsAlerts, addAlert]);

  function handleAcknowledge(id: string) {
    acknowledgeAlert(id);
    acknowledgeServerAlert.mutate({ id });
  }

  return (
    <div className="flex flex-col h-full bg-gray-900 text-white">
      <div className="p-4 border-b border-gray-700 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <h2 className="text-lg font-semibold">Alerts</h2>
          {unreadCount > 0 && (
            <span className="text-xs px-2 py-0.5 rounded-full bg-red-500 text-white font-medium">
              {unreadCount}
            </span>
          )}
        </div>
        {alerts.length > 0 && (
          <button
            onClick={clearAll}
            className="text-xs text-gray-400 hover:text-white transition-colors"
          >
            Clear all
          </button>
        )}
      </div>

      <div className="flex-1 overflow-y-auto p-3 space-y-2">
        {alerts.length === 0 ? (
          <div className="text-center text-gray-500 text-sm py-8">
            No alerts
          </div>
        ) : (
          alerts.map((alert) => (
            <AlertItem
              key={alert.id}
              alert={alert}
              onAcknowledge={handleAcknowledge}
            />
          ))
        )}
      </div>
    </div>
  );
}
