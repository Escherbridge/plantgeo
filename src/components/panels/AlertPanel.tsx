"use client";

import { useState } from "react";
import {
  Flame,
  Droplets,
  CloudRain,
  MapPin,
  AlertTriangle,
  Info,
  CheckCheck,
  Check,
  Plus,
  Bell,
} from "lucide-react";
import { Sheet, SheetContent, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { trpc } from "@/lib/trpc/client";

interface AlertPanelProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onMarkRead?: () => void;
}

const SEVERITY_CONFIG = {
  critical: {
    color: "text-red-600",
    bg: "bg-red-50 border-red-200",
    badge: "bg-red-500 text-white",
    label: "Critical",
    Icon: Flame,
  },
  warning: {
    color: "text-amber-600",
    bg: "bg-amber-50 border-amber-200",
    badge: "bg-amber-500 text-white",
    label: "Warning",
    Icon: AlertTriangle,
  },
  info: {
    color: "text-blue-600",
    bg: "bg-blue-50 border-blue-200",
    badge: "bg-blue-500 text-white",
    label: "Info",
    Icon: Info,
  },
} as const;

const ALERT_TYPE_ICON: Record<string, React.ElementType> = {
  fire_proximity: Flame,
  drought_escalation: CloudRain,
  streamflow_critical: Droplets,
  priority_zone_created: MapPin,
};

function timeAgo(date: Date | null): string {
  if (!date) return "";
  const secs = Math.floor((Date.now() - new Date(date).getTime()) / 1000);
  if (secs < 60) return "just now";
  if (secs < 3600) return `${Math.floor(secs / 60)}m ago`;
  if (secs < 86400) return `${Math.floor(secs / 3600)}h ago`;
  return `${Math.floor(secs / 86400)}d ago`;
}

type AlertRow = {
  id: string;
  userId: string;
  alertType: string;
  severity: string;
  title: string;
  body: string | null;
  metadata: unknown;
  isRead: boolean | null;
  createdAt: Date | null;
};

function AlertItem({
  alert,
  onMarkRead,
}: {
  alert: AlertRow;
  onMarkRead: (id: string) => void;
}) {
  const sev = (alert.severity in SEVERITY_CONFIG
    ? alert.severity
    : "info") as keyof typeof SEVERITY_CONFIG;
  const cfg = SEVERITY_CONFIG[sev];
  const TypeIcon = ALERT_TYPE_ICON[alert.alertType] ?? Bell;

  return (
    <div
      className={`rounded-lg border p-3 flex gap-3 ${cfg.bg} ${alert.isRead ? "opacity-60" : ""}`}
    >
      <div className={`mt-0.5 shrink-0 ${cfg.color}`}>
        <TypeIcon className="h-4 w-4" />
      </div>
      <div className="flex-1 min-w-0">
        <div className="flex items-start justify-between gap-2">
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-1.5 flex-wrap mb-0.5">
              <span
                className={`inline-block px-1.5 py-0.5 rounded text-[9px] font-bold ${cfg.badge}`}
              >
                {cfg.label}
              </span>
              <span className="text-[hsl(var(--muted-foreground))] text-[10px]">
                {timeAgo(alert.createdAt)}
              </span>
            </div>
            <p className="text-xs font-semibold text-[hsl(var(--foreground))] leading-snug">
              {alert.title}
            </p>
            {alert.body && (
              <p className="text-[11px] text-[hsl(var(--muted-foreground))] mt-0.5 leading-relaxed line-clamp-2">
                {alert.body}
              </p>
            )}
          </div>
          {!alert.isRead && (
            <button
              type="button"
              onClick={() => onMarkRead(alert.id)}
              className="shrink-0 p-1 rounded hover:bg-[hsl(var(--accent))] text-[hsl(var(--muted-foreground))] transition-colors"
              title="Mark as read"
            >
              <Check className="h-3.5 w-3.5" />
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

function AddLocationForm({ onAdded }: { onAdded: () => void }) {
  const [name, setName] = useState("");
  const [lat, setLat] = useState("");
  const [lon, setLon] = useState("");
  const [radiusKm, setRadiusKm] = useState("50");
  const [error, setError] = useState("");

  const addMutation = trpc.alerts.addWatchedLocation.useMutation({
    onSuccess: () => {
      setName("");
      setLat("");
      setLon("");
      setRadiusKm("50");
      setError("");
      onAdded();
    },
    onError: (err) => setError(err.message),
  });

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const latNum = parseFloat(lat);
    const lonNum = parseFloat(lon);
    const radiusNum = parseInt(radiusKm, 10);

    if (!name.trim()) return setError("Name is required");
    if (isNaN(latNum) || latNum < -90 || latNum > 90) return setError("Invalid latitude");
    if (isNaN(lonNum) || lonNum < -180 || lonNum > 180) return setError("Invalid longitude");
    if (isNaN(radiusNum) || radiusNum < 1) return setError("Invalid radius");

    setError("");
    addMutation.mutate({ name: name.trim(), lat: latNum, lon: lonNum, radiusKm: radiusNum });
  }

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-2 mt-3">
      <input
        className="border border-[hsl(var(--border))] rounded px-2 py-1.5 text-xs bg-[hsl(var(--background))] text-[hsl(var(--foreground))] placeholder:text-[hsl(var(--muted-foreground))]"
        placeholder="Location name (e.g. My Farm)"
        value={name}
        onChange={(e) => setName(e.target.value)}
        maxLength={100}
      />
      <div className="grid grid-cols-2 gap-2">
        <input
          className="border border-[hsl(var(--border))] rounded px-2 py-1.5 text-xs bg-[hsl(var(--background))] text-[hsl(var(--foreground))] placeholder:text-[hsl(var(--muted-foreground))]"
          placeholder="Latitude"
          value={lat}
          onChange={(e) => setLat(e.target.value)}
          type="number"
          step="any"
        />
        <input
          className="border border-[hsl(var(--border))] rounded px-2 py-1.5 text-xs bg-[hsl(var(--background))] text-[hsl(var(--foreground))] placeholder:text-[hsl(var(--muted-foreground))]"
          placeholder="Longitude"
          value={lon}
          onChange={(e) => setLon(e.target.value)}
          type="number"
          step="any"
        />
      </div>
      <input
        className="border border-[hsl(var(--border))] rounded px-2 py-1.5 text-xs bg-[hsl(var(--background))] text-[hsl(var(--foreground))] placeholder:text-[hsl(var(--muted-foreground))]"
        placeholder="Radius (km, default 50)"
        value={radiusKm}
        onChange={(e) => setRadiusKm(e.target.value)}
        type="number"
        min={1}
        max={500}
      />
      {error && <p className="text-red-500 text-[10px]">{error}</p>}
      <button
        type="submit"
        disabled={addMutation.isPending}
        className="flex items-center justify-center gap-1.5 bg-[hsl(var(--primary))] text-[hsl(var(--primary-foreground))] rounded px-3 py-1.5 text-xs font-medium hover:opacity-90 disabled:opacity-50 transition-opacity"
      >
        <Plus className="h-3.5 w-3.5" />
        {addMutation.isPending ? "Adding…" : "Add Location"}
      </button>
    </form>
  );
}

export function AlertPanel({ open, onOpenChange, onMarkRead }: AlertPanelProps) {
  const [showAddForm, setShowAddForm] = useState(false);

  const alertsQuery = trpc.alerts.getAlerts.useQuery(
    { limit: 50, unreadOnly: false },
    { enabled: open }
  );

  const locationsQuery = trpc.alerts.getWatchedLocations.useQuery(undefined, {
    enabled: open,
  });

  const markReadMutation = trpc.alerts.markRead.useMutation({
    onSuccess: () => {
      alertsQuery.refetch();
      onMarkRead?.();
    },
  });

  const markAllReadMutation = trpc.alerts.markAllRead.useMutation({
    onSuccess: () => {
      alertsQuery.refetch();
      onMarkRead?.();
    },
  });

  const allAlerts: AlertRow[] = alertsQuery.data ?? [];
  const unreadAlerts = allAlerts.filter((a) => !a.isRead);
  const criticalAlerts = allAlerts.filter((a) => a.severity === "critical");
  const locations = locationsQuery.data ?? [];

  const hasUnread = unreadAlerts.length > 0;

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" onOpenChange={onOpenChange}>
        <SheetHeader>
          <div className="flex items-center justify-between gap-2">
            <SheetTitle className="flex items-center gap-2">
              <Bell className="h-5 w-5 text-[hsl(var(--foreground))]" />
              Environmental Alerts
            </SheetTitle>
            {hasUnread && (
              <button
                type="button"
                onClick={() => markAllReadMutation.mutate()}
                disabled={markAllReadMutation.isPending}
                className="flex items-center gap-1 text-[10px] text-[hsl(var(--muted-foreground))] hover:text-[hsl(var(--foreground))] transition-colors disabled:opacity-50"
              >
                <CheckCheck className="h-3.5 w-3.5" />
                Mark all read
              </button>
            )}
          </div>
        </SheetHeader>

        <div className="mt-4 overflow-y-auto max-h-[calc(100vh-8rem)]">
          <Tabs defaultValue="all">
            <TabsList className="w-full">
              <TabsTrigger value="all" className="flex-1 text-xs">
                All
                {allAlerts.length > 0 && (
                  <span className="ml-1 text-[hsl(var(--muted-foreground))]">
                    ({allAlerts.length})
                  </span>
                )}
              </TabsTrigger>
              <TabsTrigger value="unread" className="flex-1 text-xs">
                Unread
                {unreadAlerts.length > 0 && (
                  <span className="ml-1 text-red-500 font-semibold">
                    ({unreadAlerts.length})
                  </span>
                )}
              </TabsTrigger>
              <TabsTrigger value="critical" className="flex-1 text-xs">
                Critical
                {criticalAlerts.length > 0 && (
                  <span className="ml-1 text-red-600 font-semibold">
                    ({criticalAlerts.length})
                  </span>
                )}
              </TabsTrigger>
              <TabsTrigger value="locations" className="flex-1 text-xs">
                <MapPin className="h-3 w-3 mr-0.5" />
                Locations
              </TabsTrigger>
            </TabsList>

            {/* All alerts */}
            <TabsContent value="all" className="flex flex-col gap-2 mt-4">
              {alertsQuery.isLoading && (
                <p className="text-xs text-[hsl(var(--muted-foreground))]">Loading alerts…</p>
              )}
              {!alertsQuery.isLoading && allAlerts.length === 0 && (
                <div className="text-center py-8">
                  <Bell className="h-8 w-8 mx-auto mb-2 text-[hsl(var(--muted-foreground))] opacity-40" />
                  <p className="text-xs text-[hsl(var(--muted-foreground))]">
                    No alerts — add watched locations to get started.
                  </p>
                </div>
              )}
              {allAlerts.map((alert) => (
                <AlertItem
                  key={alert.id}
                  alert={alert}
                  onMarkRead={(id) => markReadMutation.mutate({ alertId: id })}
                />
              ))}
            </TabsContent>

            {/* Unread only */}
            <TabsContent value="unread" className="flex flex-col gap-2 mt-4">
              {alertsQuery.isLoading && (
                <p className="text-xs text-[hsl(var(--muted-foreground))]">Loading alerts…</p>
              )}
              {!alertsQuery.isLoading && unreadAlerts.length === 0 && (
                <div className="text-center py-8">
                  <CheckCheck className="h-8 w-8 mx-auto mb-2 text-green-500 opacity-60" />
                  <p className="text-xs text-[hsl(var(--muted-foreground))]">
                    All caught up — no unread alerts.
                  </p>
                </div>
              )}
              {unreadAlerts.map((alert) => (
                <AlertItem
                  key={alert.id}
                  alert={alert}
                  onMarkRead={(id) => markReadMutation.mutate({ alertId: id })}
                />
              ))}
            </TabsContent>

            {/* Critical only */}
            <TabsContent value="critical" className="flex flex-col gap-2 mt-4">
              {alertsQuery.isLoading && (
                <p className="text-xs text-[hsl(var(--muted-foreground))]">Loading alerts…</p>
              )}
              {!alertsQuery.isLoading && criticalAlerts.length === 0 && (
                <div className="text-center py-8">
                  <Flame className="h-8 w-8 mx-auto mb-2 text-[hsl(var(--muted-foreground))] opacity-40" />
                  <p className="text-xs text-[hsl(var(--muted-foreground))]">
                    No critical alerts right now.
                  </p>
                </div>
              )}
              {criticalAlerts.map((alert) => (
                <AlertItem
                  key={alert.id}
                  alert={alert}
                  onMarkRead={(id) => markReadMutation.mutate({ alertId: id })}
                />
              ))}
            </TabsContent>

            {/* Watched locations management */}
            <TabsContent value="locations" className="flex flex-col gap-3 mt-4">
              <div className="flex items-center justify-between">
                <p className="text-xs font-semibold text-[hsl(var(--foreground))]">
                  Watched Locations ({locations.length})
                </p>
                <button
                  type="button"
                  onClick={() => setShowAddForm((v) => !v)}
                  className="flex items-center gap-1 text-xs text-[hsl(var(--primary))] hover:opacity-80 transition-opacity"
                >
                  <Plus className="h-3.5 w-3.5" />
                  Add
                </button>
              </div>

              {showAddForm && (
                <div className="rounded-lg border border-[hsl(var(--border))] bg-[hsl(var(--card))] p-3">
                  <p className="text-xs font-medium text-[hsl(var(--foreground))] mb-1">
                    New Watched Location
                  </p>
                  <AddLocationForm
                    onAdded={() => {
                      setShowAddForm(false);
                      locationsQuery.refetch();
                    }}
                  />
                </div>
              )}

              {locationsQuery.isLoading && (
                <p className="text-xs text-[hsl(var(--muted-foreground))]">
                  Loading locations…
                </p>
              )}

              {!locationsQuery.isLoading && locations.length === 0 && !showAddForm && (
                <div className="text-center py-6">
                  <MapPin className="h-8 w-8 mx-auto mb-2 text-[hsl(var(--muted-foreground))] opacity-40" />
                  <p className="text-xs text-[hsl(var(--muted-foreground))]">
                    No watched locations yet. Add one to start receiving alerts.
                  </p>
                </div>
              )}

              <div className="flex flex-col gap-2">
                {locations.map((loc) => (
                  <div
                    key={loc.id}
                    className="rounded-lg border border-[hsl(var(--border))] bg-[hsl(var(--card))] p-3"
                  >
                    <div className="flex items-start justify-between gap-2">
                      <div>
                        <p className="text-xs font-semibold text-[hsl(var(--foreground))]">
                          {loc.name}
                        </p>
                        <p className="text-[10px] text-[hsl(var(--muted-foreground))] mt-0.5">
                          {loc.lat.toFixed(4)}, {loc.lon.toFixed(4)} &mdash;{" "}
                          {loc.radiusKm ?? 50} km radius
                        </p>
                      </div>
                      <MapPin className="h-3.5 w-3.5 text-[hsl(var(--muted-foreground))] mt-0.5 shrink-0" />
                    </div>
                  </div>
                ))}
              </div>
            </TabsContent>
          </Tabs>
        </div>
      </SheetContent>
    </Sheet>
  );
}
