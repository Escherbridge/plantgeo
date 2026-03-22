"use client";

import { useState, useEffect } from "react";
import { useSSE } from "@/hooks/useSSE";

interface MetricState {
  activeFires: number;
  sensorAlerts: number;
  fleetActive: number;
  totalLayers: number;
}

type Status = "green" | "yellow" | "red";

function statusDot(status: Status) {
  const colors: Record<Status, string> = {
    green: "bg-emerald-500",
    yellow: "bg-yellow-400",
    red: "bg-red-500",
  };
  return (
    <span
      className={`inline-block w-2 h-2 rounded-full shrink-0 ${colors[status]}`}
      aria-hidden="true"
    />
  );
}

function getFireStatus(count: number): Status {
  if (count === 0) return "green";
  if (count < 10) return "yellow";
  return "red";
}

function getAlertStatus(count: number): Status {
  if (count === 0) return "green";
  if (count < 5) return "yellow";
  return "red";
}

function getFleetStatus(count: number): Status {
  if (count > 20) return "green";
  if (count > 5) return "yellow";
  return "red";
}

export function MetricsBar() {
  const [metrics, setMetrics] = useState<MetricState>({
    activeFires: 0,
    sensorAlerts: 0,
    fleetActive: 0,
    totalLayers: 0,
  });

  const { data, connectionState } = useSSE("/api/stream/alerts:global");

  useEffect(() => {
    if (!data) return;
    const msg = data as Record<string, unknown>;
    setMetrics((prev) => {
      const next = { ...prev };
      if (typeof msg.activeFires === "number") next.activeFires = msg.activeFires;
      if (typeof msg.sensorAlerts === "number") next.sensorAlerts = msg.sensorAlerts;
      if (typeof msg.fleetActive === "number") next.fleetActive = msg.fleetActive;
      if (typeof msg.totalLayers === "number") next.totalLayers = msg.totalLayers;
      return next;
    });
  }, [data]);

  const items: Array<{
    label: string;
    value: number;
    unit?: string;
    status: Status;
  }> = [
    {
      label: "Active Fires",
      value: metrics.activeFires,
      status: getFireStatus(metrics.activeFires),
    },
    {
      label: "Sensor Alerts",
      value: metrics.sensorAlerts,
      status: getAlertStatus(metrics.sensorAlerts),
    },
    {
      label: "Fleet Active",
      value: metrics.fleetActive,
      unit: "vehicles",
      status: getFleetStatus(metrics.fleetActive),
    },
    {
      label: "Total Layers",
      value: metrics.totalLayers,
      status: "green",
    },
  ];

  return (
    <div className="flex items-center gap-6 flex-wrap">
      {/* Connection indicator */}
      <div className="flex items-center gap-1.5 text-xs text-[hsl(var(--muted-foreground))]">
        <span
          className={`inline-block w-2 h-2 rounded-full ${
            connectionState === "open"
              ? "bg-emerald-500"
              : connectionState === "connecting"
              ? "bg-yellow-400 animate-pulse"
              : "bg-red-500"
          }`}
        />
        {connectionState === "open" ? "Live" : connectionState === "connecting" ? "Connecting…" : "Offline"}
      </div>

      <div className="h-4 w-px bg-[hsl(var(--border))]" aria-hidden="true" />

      {items.map((item) => (
        <div key={item.label} className="flex items-center gap-2">
          {statusDot(item.status)}
          <div className="flex flex-col">
            <span className="text-xs text-[hsl(var(--muted-foreground))] leading-none">
              {item.label}
            </span>
            <span className="text-sm font-semibold text-[hsl(var(--foreground))] leading-tight">
              {item.value.toLocaleString()}
              {item.unit && (
                <span className="text-xs font-normal text-[hsl(var(--muted-foreground))] ml-1">
                  {item.unit}
                </span>
              )}
            </span>
          </div>
        </div>
      ))}
    </div>
  );
}
