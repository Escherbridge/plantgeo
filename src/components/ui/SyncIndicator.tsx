"use client";

import { CheckCircle, WifiOff, RefreshCw, Clock } from "lucide-react";
import { useOfflineSync } from "@/hooks/useOfflineSync";
import { cn } from "@/lib/utils";

interface SyncIndicatorProps {
  className?: string;
}

export function SyncIndicator({ className }: SyncIndicatorProps) {
  const { isOnline, pendingCount } = useOfflineSync();

  if (!isOnline) {
    return (
      <div
        className={cn(
          "inline-flex items-center gap-1.5 rounded-full px-2.5 py-1",
          "bg-[var(--glass-bg)] border border-[var(--glass-border)]",
          "[backdrop-filter:blur(var(--glass-blur))]",
          "text-xs font-medium text-red-400",
          className
        )}
      >
        <WifiOff className="h-3.5 w-3.5" />
        <span>Offline</span>
      </div>
    );
  }

  if (pendingCount > 0) {
    return (
      <div
        className={cn(
          "inline-flex items-center gap-1.5 rounded-full px-2.5 py-1",
          "bg-[var(--glass-bg)] border border-[var(--glass-border)]",
          "[backdrop-filter:blur(var(--glass-blur))]",
          "text-xs font-medium text-yellow-400",
          className
        )}
      >
        <RefreshCw className="h-3.5 w-3.5 animate-spin" />
        <span>{pendingCount} pending</span>
      </div>
    );
  }

  return (
    <div
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full px-2.5 py-1",
        "bg-[var(--glass-bg)] border border-[var(--glass-border)]",
        "[backdrop-filter:blur(var(--glass-blur))]",
        "text-xs font-medium text-emerald-400",
        className
      )}
    >
      <CheckCircle className="h-3.5 w-3.5" />
      <span>Synced</span>
    </div>
  );
}
