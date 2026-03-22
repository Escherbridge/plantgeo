"use client";

import { MapPin } from "lucide-react";

export interface PrioritySubregion {
  name: string;
  lat: number;
  lon: number;
  score: number;
  primaryIssue: string;
}

interface PriorityTableProps {
  data: PrioritySubregion[];
  onFlyTo?: (lat: number, lon: number) => void;
}

function scoreColor(score: number): string {
  if (score >= 75) return "text-red-600 dark:text-red-400";
  if (score >= 50) return "text-orange-600 dark:text-orange-400";
  if (score >= 25) return "text-yellow-600 dark:text-yellow-400";
  return "text-green-600 dark:text-green-400";
}

function issueLabel(issue: string): string {
  const labels: Record<string, string> = {
    keyline: "Keyline",
    silvopasture: "Silvopasture",
    reforestation: "Reforestation",
    biochar: "Biochar",
    water_harvesting: "Water Harvesting",
    cover_cropping: "Cover Cropping",
    fire_detection: "Fire Risk",
    ndvi_observation: "Vegetation",
  };
  return labels[issue] ?? issue;
}

export function PriorityTable({ data, onFlyTo }: PriorityTableProps) {
  if (!data || data.length === 0) {
    return (
      <div className="text-xs text-[hsl(var(--muted-foreground))] py-4 text-center">
        No priority subregions found in current viewport.
      </div>
    );
  }

  return (
    <div className="w-full overflow-x-auto">
      <table className="w-full text-xs">
        <thead>
          <tr className="border-b border-[hsl(var(--border))]">
            <th className="text-left py-2 pr-2 font-medium text-[hsl(var(--muted-foreground))] w-8">
              #
            </th>
            <th className="text-left py-2 pr-2 font-medium text-[hsl(var(--muted-foreground))]">
              Location
            </th>
            <th className="text-right py-2 pr-2 font-medium text-[hsl(var(--muted-foreground))] w-16">
              Score
            </th>
            <th className="text-left py-2 pr-2 font-medium text-[hsl(var(--muted-foreground))]">
              Issue
            </th>
            <th className="w-8" />
          </tr>
        </thead>
        <tbody>
          {data.map((row, i) => (
            <tr
              key={i}
              className="border-b border-[hsl(var(--border))] last:border-0 hover:bg-[hsl(var(--muted))] transition-colors"
            >
              <td className="py-2 pr-2 text-[hsl(var(--muted-foreground))] font-medium">
                {i + 1}
              </td>
              <td className="py-2 pr-2 font-medium text-[hsl(var(--foreground))] max-w-[140px] truncate">
                {row.name}
              </td>
              <td className={`py-2 pr-2 text-right font-bold tabular-nums ${scoreColor(row.score)}`}>
                {row.score}
              </td>
              <td className="py-2 pr-2 text-[hsl(var(--muted-foreground))]">
                {issueLabel(row.primaryIssue)}
              </td>
              <td className="py-2">
                {onFlyTo && (
                  <button
                    onClick={() => onFlyTo(row.lat, row.lon)}
                    className="p-1 rounded hover:bg-[hsl(var(--accent))] transition-colors"
                    title={`Fly to ${row.name}`}
                    aria-label={`Fly to ${row.name}`}
                  >
                    <MapPin className="h-3.5 w-3.5 text-[hsl(var(--muted-foreground))]" />
                  </button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
