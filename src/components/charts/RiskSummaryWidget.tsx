"use client";

import React from "react";
import { TrendingUp, TrendingDown, Minus } from "lucide-react";

interface RiskSummaryWidgetProps {
  label: string;
  value: number | string;
  trend: "up" | "down" | "stable";
  color: string;
  icon: React.ReactNode;
}

function severityClass(color: string): string {
  if (color === "red") return "border-red-300 bg-red-50 dark:bg-red-950/20";
  if (color === "orange") return "border-orange-300 bg-orange-50 dark:bg-orange-950/20";
  if (color === "yellow") return "border-yellow-300 bg-yellow-50 dark:bg-yellow-950/20";
  if (color === "green") return "border-green-300 bg-green-50 dark:bg-green-950/20";
  if (color === "blue") return "border-blue-300 bg-blue-50 dark:bg-blue-950/20";
  return "border-[hsl(var(--border))] bg-[hsl(var(--card))]";
}

function valueColor(color: string): string {
  if (color === "red") return "text-red-600 dark:text-red-400";
  if (color === "orange") return "text-orange-600 dark:text-orange-400";
  if (color === "yellow") return "text-yellow-600 dark:text-yellow-400";
  if (color === "green") return "text-green-600 dark:text-green-400";
  if (color === "blue") return "text-blue-600 dark:text-blue-400";
  return "text-[hsl(var(--foreground))]";
}

export function RiskSummaryWidget({
  label,
  value,
  trend,
  color,
  icon,
}: RiskSummaryWidgetProps) {
  const TrendIcon =
    trend === "up" ? TrendingUp : trend === "down" ? TrendingDown : Minus;
  const trendColor =
    trend === "up"
      ? "text-red-500"
      : trend === "down"
      ? "text-green-500"
      : "text-[hsl(var(--muted-foreground))]";

  return (
    <div
      className={`rounded-lg border p-3 flex flex-col gap-1 ${severityClass(color)}`}
    >
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2 text-[hsl(var(--muted-foreground))]">
          {icon}
          <span className="text-xs font-medium">{label}</span>
        </div>
        <TrendIcon className={`h-3.5 w-3.5 ${trendColor}`} />
      </div>
      <span className={`text-2xl font-bold ${valueColor(color)}`}>{value}</span>
    </div>
  );
}
