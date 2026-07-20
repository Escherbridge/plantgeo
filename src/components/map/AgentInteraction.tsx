"use client";

import { useEffect, useRef, useState } from "react";
import { MapPin, Sparkles, X } from "lucide-react";

interface AgentInteractionProps {
  coordinates: [number, number];
  onAnalyze: (precision: "approximate" | "exact") => void;
  onClose: () => void;
}

/** Explicitly confirms an analysis request before the AI service receives a location. */
export function AgentInteraction({
  coordinates,
  onAnalyze,
  onClose,
}: AgentInteractionProps) {
  const [lon, lat] = coordinates;
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  const [precision, setPrecision] = useState<"approximate" | "exact">("approximate");
  const coordinateDigits = precision === "approximate" ? 2 : 6;

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

  return (
    <section
      role="dialog"
      aria-modal="false"
      aria-label="Location actions"
      aria-describedby="location-actions-description"
      className="fixed bottom-4 right-4 z-50 w-[min(24rem,calc(100vw-2rem))] rounded-xl border border-[hsl(var(--border))] bg-[hsl(var(--card))] p-4 text-[hsl(var(--card-foreground))] shadow-xl"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 items-center gap-2">
          <MapPin aria-hidden="true" className="h-5 w-5 shrink-0 text-[hsl(var(--primary))]" />
          <div>
            <h2 className="text-sm font-semibold">Location selected</h2>
            <p className="mt-0.5 font-mono text-xs text-[hsl(var(--muted-foreground))]">
              {lat.toFixed(coordinateDigits)}, {lon.toFixed(coordinateDigits)}
            </p>
          </div>
        </div>
        <button
          ref={closeButtonRef}
          type="button"
          onClick={onClose}
          aria-label="Close location actions"
          className="inline-flex min-h-11 min-w-11 items-center justify-center rounded text-[hsl(var(--muted-foreground))] hover:bg-[hsl(var(--muted))] hover:text-[hsl(var(--foreground))] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--ring))]"
        >
          <X aria-hidden="true" className="h-4 w-4" />
        </button>
      </div>

      <p
        id="location-actions-description"
        className="mt-3 text-sm leading-5 text-[hsl(var(--muted-foreground))]"
      >
        Choose what location precision to send. The agent can only provide
        informational analysis; it cannot contact others, send invitations, or
        take field actions.
      </p>

      <fieldset className="mt-3 space-y-2 text-sm">
        <legend className="font-medium">Location precision</legend>
        <label className="flex cursor-pointer items-start gap-2 rounded p-1 hover:bg-[hsl(var(--muted))]">
          <input
            type="radio"
            name="agent-location-precision"
            checked={precision === "approximate"}
            onChange={() => setPrecision("approximate")}
          />
          <span>
            <span className="block font-medium">Approximate location (default)</span>
            <span className="block text-xs text-[hsl(var(--muted-foreground))]">Rounds coordinates to about 1 km.</span>
          </span>
        </label>
        <label className="flex cursor-pointer items-start gap-2 rounded p-1 hover:bg-[hsl(var(--muted))]">
          <input
            type="radio"
            name="agent-location-precision"
            checked={precision === "exact"}
            onChange={() => setPrecision("exact")}
          />
          <span>
            <span className="block font-medium">High-precision selected location</span>
            <span className="block text-xs text-[hsl(var(--muted-foreground))]">Sends the displayed six-decimal coordinates to the analysis service.</span>
          </span>
        </label>
      </fieldset>

      <div className="mt-4 flex justify-end gap-2">
        <button
          type="button"
          onClick={onClose}
          className="min-h-11 rounded-lg px-3 py-2 text-sm font-medium text-[hsl(var(--muted-foreground))] hover:bg-[hsl(var(--muted))] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--ring))]"
        >
          Cancel
        </button>
        <button
          type="button"
          onClick={() => onAnalyze(precision)}
          className="inline-flex min-h-11 items-center gap-2 rounded-lg bg-[hsl(var(--primary))] px-3 py-2 text-sm font-semibold text-[hsl(var(--primary-foreground))] hover:opacity-90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--ring))] focus-visible:ring-offset-2"
        >
          <Sparkles aria-hidden="true" className="h-4 w-4" />
          Send for analysis
        </button>
      </div>
    </section>
  );
}
