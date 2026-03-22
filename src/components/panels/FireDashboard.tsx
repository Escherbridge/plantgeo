"use client";

import { useState, useEffect } from "react";
import { Sheet, SheetContent, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Flame, Wind, AlertTriangle, MapPin } from "lucide-react";
import { trpc } from "@/lib/trpc/client";

interface StatCardProps {
  icon: React.ReactNode;
  label: string;
  value: string | number;
  sub?: string;
  highlight?: boolean;
}

function StatCard({ icon, label, value, sub, highlight }: StatCardProps) {
  return (
    <div
      className={`rounded-lg border p-3 flex flex-col gap-1 ${
        highlight
          ? "border-red-300 bg-red-50 dark:bg-red-950/20"
          : "border-[hsl(var(--border))] bg-[hsl(var(--card))]"
      }`}
    >
      <div className="flex items-center gap-2 text-[hsl(var(--muted-foreground))]">
        {icon}
        <span className="text-xs font-medium">{label}</span>
      </div>
      <span
        className={`text-2xl font-bold ${highlight ? "text-red-600" : "text-[hsl(var(--foreground))]"}`}
      >
        {value}
      </span>
      {sub && (
        <span className="text-xs text-[hsl(var(--muted-foreground))]">{sub}</span>
      )}
    </div>
  );
}

// Simple SVG bar chart — no external library
interface BarChartProps {
  data: { label: string; value: number }[];
}

function BarChart({ data }: BarChartProps) {
  const max = Math.max(...data.map((d) => d.value), 1);
  const BAR_HEIGHT = 80;

  return (
    <div className="flex flex-col gap-2">
      <p className="text-xs font-medium text-[hsl(var(--muted-foreground))]">
        Fire detections — last 7 days
      </p>
      <div className="flex items-end gap-1 h-24">
        {data.map((d, i) => {
          const barH = Math.round((d.value / max) * BAR_HEIGHT);
          return (
            <div key={i} className="flex flex-col items-center gap-1 flex-1">
              <span className="text-[10px] text-[hsl(var(--muted-foreground))]">
                {d.value}
              </span>
              <div
                className="w-full rounded-t bg-orange-500 transition-all"
                style={{ height: barH }}
                title={`${d.label}: ${d.value}`}
              />
              <span className="text-[10px] text-[hsl(var(--muted-foreground))] truncate w-full text-center">
                {d.label}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

interface FireDashboardProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Center of current map view for weather fetch */
  centerLat?: number;
  centerLon?: number;
}

const WIND_ALERT_THRESHOLD_KMH = 50;

export function FireDashboard({
  open,
  onOpenChange,
  centerLat = 37.7749,
  centerLon = -122.4194,
}: FireDashboardProps) {
  const [windAlert, setWindAlert] = useState(false);

  const fireQuery = trpc.wildfire.getFireDetections.useQuery(
    { dayRange: 1 },
    { enabled: open, refetchInterval: 300_000 }
  );

  const weatherQuery = trpc.wildfire.getWeatherForPoint.useQuery(
    { lat: centerLat, lon: centerLon },
    { enabled: open, refetchInterval: 600_000 }
  );

  const weather = weatherQuery.data;
  const fireData = fireQuery.data;
  const activeFireCount = fireData?.features.length ?? 0;

  // Wind alert: windSpeed in m/s -> convert to km/h
  useEffect(() => {
    if (!weather) return;
    const windKmh = weather.windSpeed * 3.6;
    setWindAlert(windKmh > WIND_ALERT_THRESHOLD_KMH);
  }, [weather]);

  // Build mock last-7-days chart data (in real usage this would come from a time-series query)
  const chartData = Array.from({ length: 7 }, (_, i) => {
    const d = new Date();
    d.setDate(d.getDate() - (6 - i));
    return {
      label: d.toLocaleDateString("en-US", { weekday: "short" }),
      value: i === 6 ? activeFireCount : 0,
    };
  });

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" onOpenChange={onOpenChange}>
        <SheetHeader>
          <SheetTitle className="flex items-center gap-2">
            <Flame className="h-5 w-5 text-red-500" />
            Fire Dashboard
          </SheetTitle>
        </SheetHeader>

        <div className="flex flex-col gap-4 mt-4 overflow-y-auto max-h-[calc(100vh-8rem)]">
          {windAlert && (
            <div className="flex items-center gap-2 rounded-lg border border-red-300 bg-red-50 dark:bg-red-950/20 px-3 py-2">
              <AlertTriangle className="h-4 w-4 text-red-600 shrink-0" />
              <p className="text-xs text-red-700 font-medium">
                High wind alert: {((weather?.windSpeed ?? 0) * 3.6).toFixed(0)} km/h — elevated
                fire spread risk.
              </p>
            </div>
          )}

          <div className="grid grid-cols-2 gap-2">
            <StatCard
              icon={<Flame className="h-3.5 w-3.5" />}
              label="Active Fires"
              value={fireQuery.isLoading ? "..." : activeFireCount}
              sub="VIIRS detections (24h)"
              highlight={activeFireCount > 10}
            />
            <StatCard
              icon={<Wind className="h-3.5 w-3.5" />}
              label="Wind Speed"
              value={
                weatherQuery.isLoading
                  ? "..."
                  : weather
                  ? `${(weather.windSpeed * 3.6).toFixed(0)} km/h`
                  : "N/A"
              }
              sub={weather ? `Dir: ${weather.windDirection}°` : undefined}
              highlight={windAlert}
            />
            <StatCard
              icon={<MapPin className="h-3.5 w-3.5" />}
              label="Humidity"
              value={
                weatherQuery.isLoading
                  ? "..."
                  : weather
                  ? `${weather.humidity}%`
                  : "N/A"
              }
              sub="Relative humidity"
            />
            <StatCard
              icon={<Flame className="h-3.5 w-3.5" />}
              label="Temperature"
              value={
                weatherQuery.isLoading
                  ? "..."
                  : weather
                  ? `${weather.temperature.toFixed(1)}°C`
                  : "N/A"
              }
              sub={weather ? `Precip: ${weather.precipitation} mm` : undefined}
            />
          </div>

          <BarChart data={chartData} />

          {(fireQuery.isError || weatherQuery.isError) && (
            <p className="text-xs text-[hsl(var(--destructive))]">
              Some data sources are unavailable. Check API keys and connectivity.
            </p>
          )}
        </div>
      </SheetContent>
    </Sheet>
  );
}
