"use client";

import { useEffect, useRef, useState } from "react";
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
  teamId?: string;
  workspaceName?: string;
  onClose: () => void;
  onSuccess?: () => void;
}

export function RequestSubmitModal({
  lat,
  lon,
  teamId,
  workspaceName,
  onClose,
  onSuccess,
}: RequestSubmitModalProps) {
  const [strategyType, setStrategyType] = useState<StrategyType>("reforestation");
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [locationConsent, setLocationConsent] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const closeButtonRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    const previouslyFocused = document.activeElement;
    closeButtonRef.current?.focus();
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("keydown", handleKeyDown);
      if (previouslyFocused instanceof HTMLElement) previouslyFocused.focus();
    };
  }, [onClose]);

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
    if (!locationConsent) {
      setError("Confirm the private location storage notice before submitting.");
      return;
    }
    submitMutation.mutate({
      strategyType,
      title: title.trim(),
      description: description.trim() || undefined,
      lat,
      lon,
      teamId,
      locationConsent: true,
    });
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
      <section
        role="dialog"
        aria-modal="true"
        aria-labelledby="strategy-request-title"
        className="bg-[hsl(var(--background))] border border-[hsl(var(--border))] rounded-xl shadow-xl w-full max-w-md mx-4 p-6"
      >
        <div className="flex items-center justify-between mb-4">
          <h2 id="strategy-request-title" className="text-lg font-semibold text-[hsl(var(--foreground))]">
            Submit Strategy Request
          </h2>
          <button
            ref={closeButtonRef}
            type="button"
            onClick={onClose}
            className="min-h-11 min-w-11 text-[hsl(var(--muted-foreground))] hover:text-[hsl(var(--foreground))] transition-colors"
            aria-label="Close strategy request form"
          >
            <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        <p className="text-xs text-[hsl(var(--muted-foreground))] mb-4">
          Approximate area: {lat.toFixed(2)}, {lon.toFixed(2)}
        </p>

        <form onSubmit={handleSubmit} className="flex flex-col gap-4">
          <div className="flex flex-col gap-1">
            <label htmlFor="strategy-request-type" className="text-sm font-medium text-[hsl(var(--foreground))]">
              Strategy Type
            </label>
            <select
              id="strategy-request-type"
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
            <label htmlFor="strategy-request-title-input" className="text-sm font-medium text-[hsl(var(--foreground))]">
              Title <span className="text-red-500">*</span>
            </label>
            <input
              id="strategy-request-title-input"
              type="text"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              maxLength={200}
              placeholder="Brief title for this request"
              className="rounded-lg border border-[hsl(var(--border))] bg-[hsl(var(--card))] text-[hsl(var(--foreground))] px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-[hsl(var(--ring))] placeholder:text-[hsl(var(--muted-foreground))]"
            />
          </div>

          <div className="flex flex-col gap-1">
            <label htmlFor="strategy-request-description" className="text-sm font-medium text-[hsl(var(--foreground))]">
              Description
            </label>
            <textarea
              id="strategy-request-description"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              rows={3}
              placeholder="Describe why this area needs intervention (optional)"
              className="rounded-lg border border-[hsl(var(--border))] bg-[hsl(var(--card))] text-[hsl(var(--foreground))] px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-[hsl(var(--ring))] placeholder:text-[hsl(var(--muted-foreground))] resize-none"
            />
          </div>

          <label className="flex items-start gap-2 rounded-lg border border-[hsl(var(--border))] p-3 text-sm text-[hsl(var(--foreground))]">
            <input
              type="checkbox"
              checked={locationConsent}
              onChange={(event) => setLocationConsent(event.target.checked)}
              className="mt-0.5"
            />
            <span>
              I understand that the exact map location is stored with this
              {teamId
                ? ` request and shared with authenticated members of ${
                    workspaceName ?? "the selected partner workspace"
                  }.`
                : " private request."}{" "}
              It is never shown on the map. To propose a site that can appear
              on the map after review, use &ldquo;+ Recommend&rdquo; instead.
            </span>
          </label>

          {error && (
            <p role="alert" className="text-sm text-red-600">{error}</p>
          )}

          <div className="flex gap-3 justify-end pt-1">
            <button
              type="button"
              onClick={onClose}
              className="min-h-11 px-4 py-2 rounded-lg border border-[hsl(var(--border))] text-sm text-[hsl(var(--foreground))] hover:bg-[hsl(var(--muted))] transition-colors"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={submitMutation.isPending || !locationConsent}
              className="min-h-11 px-4 py-2 rounded-lg bg-[hsl(var(--primary))] text-[hsl(var(--primary-foreground))] text-sm font-medium hover:opacity-90 transition-opacity disabled:opacity-50"
            >
              {submitMutation.isPending ? "Submitting..." : "Submit Request"}
            </button>
          </div>
        </form>
      </section>
    </div>
  );
}
