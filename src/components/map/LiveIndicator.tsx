"use client";

import { useRealtimeStore, type ConnectionState } from "@/stores/realtime-store";
import {
  Tooltip,
  TooltipTrigger,
  TooltipContent,
} from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";

function deriveOverallState(
  connections: Map<string, ConnectionState>
): ConnectionState {
  if (connections.size === 0) return "closed";
  const states = Array.from(connections.values());
  if (states.every((s) => s === "open")) return "open";
  if (states.some((s) => s === "connecting")) return "connecting";
  return "closed";
}

const STATE_CONFIG: Record<
  ConnectionState,
  { label: string; dotClass: string }
> = {
  open: {
    label: "Live",
    dotClass: "bg-green-500",
  },
  connecting: {
    label: "Reconnecting...",
    dotClass: "bg-yellow-400 animate-pulse",
  },
  closed: {
    label: "Offline",
    dotClass: "bg-red-500",
  },
};

interface LiveIndicatorProps {
  /** Optional layer id to show state for a single stream instead of overall */
  layerId?: string;
  className?: string;
}

export default function LiveIndicator({ layerId, className }: LiveIndicatorProps) {
  const connections = useRealtimeStore((s) => s.connections);

  const state: ConnectionState = layerId
    ? (connections.get(layerId) ?? "closed")
    : deriveOverallState(connections);

  const { label, dotClass } = STATE_CONFIG[state];

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <button
          type="button"
          aria-label={`Stream status: ${label}`}
          className={cn(
            "inline-flex items-center justify-center rounded-full p-1 focus:outline-none focus-visible:ring-2 focus-visible:ring-offset-1",
            className
          )}
        >
          <span
            className={cn("block h-2.5 w-2.5 rounded-full", dotClass)}
          />
        </button>
      </TooltipTrigger>
      <TooltipContent side="bottom">{label}</TooltipContent>
    </Tooltip>
  );
}
