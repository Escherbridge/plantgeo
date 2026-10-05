"use client";

import { useEffect } from "react";
import Link from "next/link";

/** Root route error boundary: a render failure below the root layout lands here instead of a blank page. */
export default function RootError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    console.error("Unhandled route error", error);
  }, [error]);

  return (
    <main
      role="alert"
      className="mx-auto flex min-h-[60vh] max-w-lg flex-col justify-center gap-4 px-6 text-[hsl(var(--foreground))]"
    >
      <p className="font-editorial-label text-[11px] uppercase tracking-[0.14em] text-[hsl(var(--muted-foreground))]">
        Something went wrong
      </p>
      <h1 className="font-editorial-display text-2xl font-semibold leading-tight">
        This page could not be displayed.
      </h1>
      <p className="font-editorial-text text-base text-[hsl(var(--muted-foreground))]">
        Your saved work is unaffected. Try again, or return to the map.
        {error.digest ? ` Reference: ${error.digest}.` : ""}
      </p>
      <div className="flex flex-wrap gap-3 text-sm">
        <button
          type="button"
          onClick={reset}
          className="min-h-11 rounded border border-[hsl(var(--border))] px-4 font-medium hover:bg-[hsl(var(--muted))] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--ring))]"
        >
          Try again
        </button>
        <Link
          href="/"
          className="flex min-h-11 items-center rounded px-4 underline underline-offset-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--ring))]"
        >
          Back to the map
        </Link>
      </div>
    </main>
  );
}
