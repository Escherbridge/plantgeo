"use client";

import { useState } from "react";
import { trpc } from "@/lib/trpc/client";

const STRATEGY_TYPES = [
  { value: "keyline", label: "Keyline Design" },
  { value: "silvopasture", label: "Silvopasture" },
  { value: "reforestation", label: "Reforestation" },
  { value: "biochar", label: "Biochar" },
  { value: "water_harvesting", label: "Water Harvesting" },
  { value: "cover_cropping", label: "Cover Cropping" },
] as const;

type StrategyType = (typeof STRATEGY_TYPES)[number]["value"];

interface RequestSubmitModalProps {
  lat: number;
  lon: number;
  onClose: () => void;
  onSuccess?: () => void;
}

export function RequestSubmitModal({
  lat,
  lon,
  onClose,
  onSuccess,
}: RequestSubmitModalProps) {
  const [strategyType, setStrategyType] = useState<StrategyType>("reforestation");
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [error, setError] = useState<string | null>(null);

  const submitMutation = trpc.community.submitRequest.useMutation({
    onSuccess: () => {
      onSuccess?.();
      onClose();
    },
    onError: (err) => {
      setError(err.message);
    },
  });

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    if (title.trim().length < 3) {
      setError("Title must be at least 3 characters.");
      return;
    }
    submitMutation.mutate({
      strategyType,
      title: title.trim(),
      description: description.trim() || undefined,
      lat,
      lon,
    });
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
      <div className="bg-[hsl(var(--background))] border border-[hsl(var(--border))] rounded-xl shadow-xl w-full max-w-md mx-4 p-6">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-semibold text-[hsl(var(--foreground))]">
            Submit Strategy Request
          </h2>
          <button
            onClick={onClose}
            className="text-[hsl(var(--muted-foreground))] hover:text-[hsl(var(--foreground))] transition-colors"
            aria-label="Close"
          >
            <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        <p className="text-xs text-[hsl(var(--muted-foreground))] mb-4">
          Location: {lat.toFixed(5)}, {lon.toFixed(5)}
        </p>

        <form onSubmit={handleSubmit} className="flex flex-col gap-4">
          <div className="flex flex-col gap-1">
            <label className="text-sm font-medium text-[hsl(var(--foreground))]">
              Strategy Type
            </label>
            <select
              value={strategyType}
              onChange={(e) => setStrategyType(e.target.value as StrategyType)}
              className="rounded-lg border border-[hsl(var(--border))] bg-[hsl(var(--card))] text-[hsl(var(--foreground))] px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-[hsl(var(--ring))]"
            >
              {STRATEGY_TYPES.map((s) => (
                <option key={s.value} value={s.value}>
                  {s.label}
                </option>
              ))}
            </select>
          </div>

          <div className="flex flex-col gap-1">
            <label className="text-sm font-medium text-[hsl(var(--foreground))]">
              Title <span className="text-red-500">*</span>
            </label>
            <input
              type="text"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              maxLength={200}
              placeholder="Brief title for this request"
              className="rounded-lg border border-[hsl(var(--border))] bg-[hsl(var(--card))] text-[hsl(var(--foreground))] px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-[hsl(var(--ring))] placeholder:text-[hsl(var(--muted-foreground))]"
            />
          </div>

          <div className="flex flex-col gap-1">
            <label className="text-sm font-medium text-[hsl(var(--foreground))]">
              Description
            </label>
            <textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              rows={3}
              placeholder="Describe why this area needs intervention (optional)"
              className="rounded-lg border border-[hsl(var(--border))] bg-[hsl(var(--card))] text-[hsl(var(--foreground))] px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-[hsl(var(--ring))] placeholder:text-[hsl(var(--muted-foreground))] resize-none"
            />
          </div>

          {error && (
            <p className="text-sm text-red-600">{error}</p>
          )}

          <div className="flex gap-3 justify-end pt-1">
            <button
              type="button"
              onClick={onClose}
              className="px-4 py-2 rounded-lg border border-[hsl(var(--border))] text-sm text-[hsl(var(--foreground))] hover:bg-[hsl(var(--muted))] transition-colors"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={submitMutation.isPending}
              className="px-4 py-2 rounded-lg bg-[hsl(var(--primary))] text-[hsl(var(--primary-foreground))] text-sm font-medium hover:opacity-90 transition-opacity disabled:opacity-50"
            >
              {submitMutation.isPending ? "Submitting..." : "Submit Request"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
