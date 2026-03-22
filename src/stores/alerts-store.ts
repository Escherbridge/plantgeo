import { create } from "zustand";
import { devtools } from "zustand/middleware";

export interface Alert {
  id: string;
  assetId: string | null;
  geofenceId: string | null;
  type: "geofence_breach" | "speed_threshold" | "offline";
  message: string;
  acknowledged: boolean;
  metadata: Record<string, unknown>;
  createdAt: string;
}

interface AlertsStoreState {
  alerts: Alert[];
  unreadCount: number;

  addAlert: (alert: Alert) => void;
  acknowledgeAlert: (id: string) => void;
  clearAll: () => void;
}

export const useAlertsStore = create<AlertsStoreState>()(
  devtools((set) => ({
    alerts: [],
    unreadCount: 0,

    addAlert: (alert) =>
      set((s) => ({
        alerts: [alert, ...s.alerts],
        unreadCount: s.unreadCount + (alert.acknowledged ? 0 : 1),
      })),

    acknowledgeAlert: (id) =>
      set((s) => {
        const alerts = s.alerts.map((a) =>
          a.id === id ? { ...a, acknowledged: true } : a
        );
        const unreadCount = alerts.filter((a) => !a.acknowledged).length;
        return { alerts, unreadCount };
      }),

    clearAll: () => set({ alerts: [], unreadCount: 0 }),
  }))
);
