"use client";

import {
  ArrowLeft,
  ArrowRight,
  ArrowUp,
  CornerDownLeft,
  CornerDownRight,
  Milestone,
  Flag,
  Navigation,
} from "lucide-react";
import type { Maneuver } from "@/lib/server/services/routing";

const MANEUVER_ICONS: Record<number, React.ReactNode> = {
  0: <Navigation className="h-4 w-4" />,
  1: <ArrowUp className="h-4 w-4" />,
  2: <ArrowUp className="h-4 w-4" />,
  3: <ArrowLeft className="h-4 w-4" />,
  4: <CornerDownLeft className="h-4 w-4" />,
  5: <ArrowLeft className="h-4 w-4" />,
  6: <ArrowRight className="h-4 w-4" />,
  7: <CornerDownRight className="h-4 w-4" />,
  8: <ArrowRight className="h-4 w-4" />,
  9: <ArrowLeft className="h-4 w-4" />,
  10: <ArrowRight className="h-4 w-4" />,
  15: <Milestone className="h-4 w-4" />,
  23: <Flag className="h-4 w-4" />,
};

function getManeuverIcon(type: number): React.ReactNode {
  return MANEUVER_ICONS[type] ?? <ArrowUp className="h-4 w-4" />;
}

function formatDistance(km: number): string {
  if (km < 1) return `${Math.round(km * 1000)} m`;
  return `${km.toFixed(1)} km`;
}

interface DirectionsListProps {
  maneuvers: Maneuver[];
}

export function DirectionsList({ maneuvers }: DirectionsListProps) {
  if (maneuvers.length === 0) return null;

  return (
    <div className="flex flex-col gap-1 mt-2">
      <h3 className="text-sm font-semibold text-[hsl(var(--foreground))]">Directions</h3>
      <ol className="flex flex-col divide-y divide-[hsl(var(--border))]">
        {maneuvers.map((m, i) => (
          <li key={i} className="flex items-start gap-3 py-2">
            <span className="mt-0.5 flex-shrink-0 text-emerald-500">
              {getManeuverIcon(m.type)}
            </span>
            <div className="flex-1 min-w-0">
              <p className="text-sm text-[hsl(var(--foreground))]">{m.instruction}</p>
              {m.distance > 0 && (
                <p className="text-xs text-[hsl(var(--muted-foreground))]">
                  {formatDistance(m.distance)}
                </p>
              )}
            </div>
          </li>
        ))}
      </ol>
    </div>
  );
}
