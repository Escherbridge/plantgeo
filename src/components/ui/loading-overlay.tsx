"use client";

import { cn } from "@/lib/utils";

interface LoadingOverlayProps {
  visible: boolean;
  message?: string;
}

export function LoadingOverlay({ visible, message }: LoadingOverlayProps) {
  if (!visible) return null;

  return (
    <div className="fixed inset-0 z-[9998] flex flex-col items-center justify-center bg-black/60 backdrop-blur-sm">
      <div
        className={cn(
          "h-10 w-10 animate-spin rounded-full border-4 border-emerald-500/30 border-t-emerald-500"
        )}
      />
      {message && (
        <p className="mt-4 text-sm font-medium text-[hsl(var(--foreground))]">
          {message}
        </p>
      )}
    </div>
  );
}
