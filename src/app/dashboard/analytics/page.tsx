"use client";

import Link from "next/link";
import { UserMenu } from "@/components/auth/UserMenu";
import { SpatialStats } from "@/components/dashboard/SpatialStats";

/**
 * The minimal analytics destination `src/app/dashboard/AGENTS.md` withheld until every offered
 * widget had a published source: `layerStats`/`systemStats`/`featureCounts`/`recentActivity` all
 * read the one public, relational layer (`interventions`) rather than an unpublished warehouse
 * aggregate, and `SpatialStats` already handles its own loading/empty states. This is
 * deliberately NOT `DashboardGrid` -- that component's Fire/Fleet/Environmental presets still
 * point at aggregates (active fire severity, fleet status, sensor time series) that remain
 * unpublished, and reinstating it wholesale would be the exact "renders only its own absence"
 * failure the grid was pulled for in the first place.
 */
export default function DashboardAnalyticsPage() {
  return (
    <div className="viewport-below-top-bar bg-[hsl(var(--background))] flex flex-col">
      <header className="min-h-14 shrink-0 border-b border-[hsl(var(--border))] flex flex-wrap items-center justify-between gap-3 px-4 py-3 sm:px-6">
        <div className="flex items-center gap-4">
          <Link href="/" className="text-lg font-bold text-emerald-500">
            PlantGeo
          </Link>
          <span className="text-[hsl(var(--muted-foreground))] text-sm">/</span>
          <Link href="/dashboard" className="text-sm text-[hsl(var(--muted-foreground))] hover:text-[hsl(var(--foreground))]">
            Dashboard
          </Link>
          <span className="text-[hsl(var(--muted-foreground))] text-sm">/</span>
          <span className="text-sm text-[hsl(var(--foreground))]">Analytics</span>
        </div>
        <UserMenu />
      </header>
      <main className="mx-auto min-h-0 w-full max-w-5xl flex-1 overflow-y-auto px-4 py-8 sm:px-6">
        <h1 className="text-xl font-semibold text-[hsl(var(--foreground))]">Spatial analytics</h1>
        <p className="mt-2 text-sm text-[hsl(var(--muted-foreground))]">
          Published layer and feature counts across the platform&apos;s one public, relational layer.
        </p>
        <div className="mt-6 rounded-lg border border-[hsl(var(--border))] bg-[hsl(var(--card))] p-4">
          <SpatialStats />
        </div>
      </main>
    </div>
  );
}
