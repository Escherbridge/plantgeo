"use client";

import { X } from "lucide-react";
import { Skeleton } from "@/components/ui/skeleton";

interface WidgetCardProps {
  title: string;
  children: React.ReactNode;
  onClose?: () => void;
  exportData?: unknown[];
  className?: string;
  isLoading?: boolean;
}

export function WidgetCard({ title, children, onClose, exportData, className, isLoading }: WidgetCardProps) {
  function handleExportCSV() {
    if (!exportData || exportData.length === 0) return;
    const keys = Object.keys(exportData[0] as Record<string, unknown>);
    const rows = [
      keys.join(","),
      ...exportData.map((row) =>
        keys.map((k) => JSON.stringify((row as Record<string, unknown>)[k] ?? "")).join(",")
      ),
    ];
    const blob = new Blob([rows.join("\n")], { type: "text/csv" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${title.replace(/\s+/g, "_").toLowerCase()}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  }

  return (
    <div
      className={`rounded-xl border border-[hsl(var(--border))] bg-[hsl(var(--card))] flex flex-col min-h-[200px] ${className ?? ""}`}
    >
      <div className="flex items-center justify-between px-4 py-3 border-b border-[hsl(var(--border))]">
        <span className="text-sm font-semibold text-[hsl(var(--foreground))]">{title}</span>
        <div className="flex items-center gap-2">
          {exportData && exportData.length > 0 && (
            <button
              onClick={handleExportCSV}
              className="text-xs text-[hsl(var(--muted-foreground))] hover:text-[hsl(var(--foreground))] transition-colors px-2 py-0.5 rounded border border-[hsl(var(--border))] hover:border-[hsl(var(--foreground))]"
            >
              CSV
            </button>
          )}
          {onClose && (
            <button
              onClick={onClose}
              className="text-[hsl(var(--muted-foreground))] hover:text-[hsl(var(--foreground))] transition-colors"
              aria-label="Close widget"
            >
              <X className="h-4 w-4" />
            </button>
          )}
        </div>
      </div>
      <div className="flex-1 p-4 resize-y overflow-auto">
        {isLoading ? (
          <div className="flex flex-col gap-3">
            <Skeleton className="h-6 w-2/3" />
            <Skeleton className="h-32 w-full" />
            <Skeleton className="h-4 w-1/2" />
          </div>
        ) : (
          children
        )}
      </div>
    </div>
  );
}
