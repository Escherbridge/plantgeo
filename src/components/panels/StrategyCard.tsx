"use client";

import { useState } from "react";
import {
  ChevronDown,
  ChevronUp,
  Droplets,
  Trees,
  Flame,
  Sprout,
  Leaf,
  ExternalLink,
  Users,
} from "lucide-react";
import type { StrategyScore } from "@/lib/server/services/strategy-scoring";
import type { Supplier } from "@/lib/server/services/plantcommerce-api";

// ── Score colour helpers ─────────────────────────────────────────────────────

function scoreColor(score: number): string {
  if (score >= 70) return "bg-green-100 text-green-800 border-green-200";
  if (score >= 40) return "bg-amber-100 text-amber-800 border-amber-200";
  return "bg-red-100 text-red-700 border-red-200";
}

function scoreBarColor(score: number): string {
  if (score >= 70) return "#22c55e";
  if (score >= 40) return "#f59e0b";
  return "#ef4444";
}

// ── Strategy icon mapping ────────────────────────────────────────────────────

const STRATEGY_ICONS: Record<string, React.ReactNode> = {
  keyline: <Droplets className="h-4 w-4" />,
  silvopasture: <Trees className="h-4 w-4" />,
  reforestation: <Trees className="h-4 w-4" />,
  biochar: <Flame className="h-4 w-4" />,
  water_harvesting: <Droplets className="h-4 w-4" />,
  cover_cropping: <Sprout className="h-4 w-4" />,
};

// ── Factor label mapping ─────────────────────────────────────────────────────

const FACTOR_LABELS: Record<string, string> = {
  waterStress: "Water Stress",
  soilHealth: "Soil Degradation",
  fireRisk: "Fire Risk",
  vegetationDegradation: "Veg. Degradation",
  communityDemand: "Community Demand",
};

// ── Mini SVG factor bar chart ────────────────────────────────────────────────

interface FactorBarsProps {
  factors: StrategyScore["factors"];
}

function FactorBars({ factors }: FactorBarsProps) {
  return (
    <div className="flex flex-col gap-1.5 mt-2">
      {(Object.entries(factors) as [keyof typeof factors, number][]).map(
        ([key, value]) => (
          <div key={key} className="flex items-center gap-2">
            <span className="text-[10px] text-[hsl(var(--muted-foreground))] w-28 shrink-0">
              {FACTOR_LABELS[key] ?? key}
            </span>
            <div className="flex-1 h-1.5 rounded-full bg-[hsl(var(--muted))]">
              <div
                className="h-1.5 rounded-full transition-all"
                style={{
                  width: `${value}%`,
                  backgroundColor: scoreBarColor(value),
                }}
              />
            </div>
            <span className="text-[10px] text-[hsl(var(--muted-foreground))] w-7 text-right shrink-0">
              {value}
            </span>
          </div>
        )
      )}
    </div>
  );
}

// ── Supplier row ─────────────────────────────────────────────────────────────

function SupplierRow({ supplier }: { supplier: Supplier }) {
  return (
    <div className="flex items-start justify-between gap-2 py-1.5 border-t border-[hsl(var(--border))] first:border-0">
      <div className="flex flex-col gap-0.5 min-w-0">
        <span className="text-xs font-medium text-[hsl(var(--foreground))] truncate">
          {supplier.name}
        </span>
        <span className="text-[10px] text-[hsl(var(--muted-foreground))]">
          {supplier.productsAvailable.slice(0, 2).join(", ")}
          {supplier.productsAvailable.length > 2 ? " +more" : ""}
        </span>
        <div className="flex gap-1 items-center">
          {Array.from({ length: 5 }).map((_, i) => (
            <span
              key={i}
              className={`text-[10px] ${
                i < Math.round(supplier.rating) ? "text-amber-400" : "text-[hsl(var(--muted))]"
              }`}
            >
              ★
            </span>
          ))}
          <span className="text-[10px] text-[hsl(var(--muted-foreground))]">
            {supplier.region}
          </span>
        </div>
      </div>
      {supplier.url && (
        <a
          href={supplier.url}
          target="_blank"
          rel="noopener noreferrer"
          className="shrink-0 flex items-center gap-1 text-[10px] text-[hsl(var(--primary))] hover:underline"
        >
          View
          <ExternalLink className="h-2.5 w-2.5" />
        </a>
      )}
    </div>
  );
}

// ── StrategyCard ─────────────────────────────────────────────────────────────

export interface StrategyCardProps {
  strategy: StrategyScore;
  suppliers: Supplier[];
  suppliersLoading: boolean;
  communityDemandCount?: number;
  selected: boolean;
  onCompareToggle: (strategyId: string) => void;
  onViewSuppliers: (strategyId: string) => void;
}

export function StrategyCard({
  strategy,
  suppliers,
  suppliersLoading,
  communityDemandCount,
  selected,
  onCompareToggle,
  onViewSuppliers,
}: StrategyCardProps) {
  const [expanded, setExpanded] = useState(false);

  const icon = STRATEGY_ICONS[strategy.strategyId] ?? (
    <Leaf className="h-4 w-4" />
  );

  return (
    <div
      className={`rounded-lg border bg-[hsl(var(--card))] p-4 flex flex-col gap-2 transition-colors ${
        selected
          ? "border-[hsl(var(--primary))] ring-1 ring-[hsl(var(--primary))]"
          : "border-[hsl(var(--border))]"
      }`}
    >
      {/* Header row */}
      <div className="flex items-start justify-between gap-2">
        <div className="flex items-center gap-2 min-w-0">
          <span className="text-[hsl(var(--muted-foreground))] shrink-0">{icon}</span>
          <h3 className="font-semibold text-sm text-[hsl(var(--foreground))] truncate">
            {strategy.name}
          </h3>
        </div>
        <div className="flex items-center gap-1.5 shrink-0">
          {/* Compare checkbox */}
          <label className="flex items-center gap-1 cursor-pointer select-none">
            <input
              type="checkbox"
              checked={selected}
              onChange={() => onCompareToggle(strategy.strategyId)}
              className="rounded w-3 h-3"
            />
            <span className="text-[10px] text-[hsl(var(--muted-foreground))]">
              Compare
            </span>
          </label>
          {/* Score badge */}
          <span
            className={`text-xs font-bold px-2 py-0.5 rounded-full border shrink-0 ${scoreColor(
              strategy.score
            )}`}
          >
            {strategy.score}
          </span>
        </div>
      </div>

      {/* Community demand badge */}
      {communityDemandCount !== undefined && communityDemandCount > 0 && (
        <div className="flex items-center gap-1 text-[10px] font-medium text-purple-700 bg-purple-50 border border-purple-200 rounded-full px-2 py-0.5 w-fit">
          <Users className="h-2.5 w-2.5 shrink-0" />
          High Community Demand · {communityDemandCount} votes nearby
        </div>
      )}

      {/* Confidence pill */}
      <div className="flex items-center gap-2">
        <span
          className={`text-[10px] px-1.5 py-0.5 rounded border font-medium ${
            strategy.confidence === "high"
              ? "bg-green-50 text-green-700 border-green-200"
              : strategy.confidence === "medium"
              ? "bg-amber-50 text-amber-700 border-amber-200"
              : "bg-gray-50 text-gray-500 border-gray-200"
          }`}
        >
          {strategy.confidence} confidence
        </span>
        <span className="text-[10px] text-[hsl(var(--muted-foreground))]">
          Suitability score
        </span>
      </div>

      {/* Top reasons */}
      <ul className="flex flex-col gap-1">
        {strategy.topReasons.map((reason, i) => (
          <li
            key={i}
            className="text-[11px] text-[hsl(var(--muted-foreground))] leading-snug flex gap-1.5 items-start"
          >
            <span className="text-green-500 mt-0.5 shrink-0">•</span>
            {reason}
          </li>
        ))}
      </ul>

      {/* Expand / collapse */}
      <button
        onClick={() => setExpanded((v) => !v)}
        className="flex items-center gap-1 text-[11px] text-[hsl(var(--primary))] hover:underline w-fit"
      >
        {expanded ? (
          <>
            <ChevronUp className="h-3 w-3" />
            Hide details
          </>
        ) : (
          <>
            <ChevronDown className="h-3 w-3" />
            Show factor breakdown
          </>
        )}
      </button>

      {/* Expanded: factor bar chart + suppliers */}
      {expanded && (
        <>
          <FactorBars factors={strategy.factors} />

          {/* Supplier section */}
          <div className="mt-2 pt-2 border-t border-[hsl(var(--border))]">
            <div className="flex items-center justify-between mb-1">
              <span className="text-xs font-semibold text-[hsl(var(--foreground))]">
                Suppliers
              </span>
              <button
                onClick={() => onViewSuppliers(strategy.strategyId)}
                className="text-[10px] text-[hsl(var(--primary))] hover:underline"
              >
                Load suppliers
              </button>
            </div>

            {suppliersLoading && (
              <p className="text-[11px] text-[hsl(var(--muted-foreground))]">
                Loading…
              </p>
            )}

            {!suppliersLoading && suppliers.length === 0 && (
              <p className="text-[11px] text-[hsl(var(--muted-foreground))]">
                No suppliers available for this strategy in your region.
              </p>
            )}

            {!suppliersLoading && suppliers.length > 0 && (
              <div className="flex flex-col">
                {suppliers.map((s) => (
                  <SupplierRow key={s.id} supplier={s} />
                ))}
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}
