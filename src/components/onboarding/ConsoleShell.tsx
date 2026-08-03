import { Fraunces, IBM_Plex_Mono } from "next/font/google";
import "@/app/onboarding/onboarding-background.css";

const displayFont = Fraunces({
  subsets: ["latin"],
  style: ["normal", "italic"],
  weight: ["400", "500", "600"],
  variable: "--font-onboarding-display",
});

const monoFont = IBM_Plex_Mono({
  subsets: ["latin"],
  weight: ["400", "500"],
  variable: "--font-onboarding-mono",
});

interface ConsoleShellProps {
  children: React.ReactNode;
  eyebrow: string;
  cornerLabel: React.ReactNode;
  maxWidthClassName?: string;
}

/**
 * Shared "geospatial access" console frame for onboarding, invite, and join
 * landing pages — the visual continuation of the (auth) route group.
 */
export function ConsoleShell({
  children,
  eyebrow,
  cornerLabel,
  maxWidthClassName = "max-w-2xl",
}: ConsoleShellProps) {
  return (
    <div
      className={`${displayFont.variable} ${monoFont.variable} relative flex min-h-screen items-center justify-center overflow-hidden bg-zinc-950 px-4 py-16 [font-family:var(--font-onboarding-mono)]`}
    >
      <div aria-hidden className="onboarding-waypoint-field pointer-events-none absolute inset-0" />
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 bg-gradient-to-b from-zinc-950 via-transparent to-zinc-950"
      />

      <div
        aria-hidden
        className="pointer-events-none absolute bottom-6 left-6 hidden select-none text-[10px] uppercase leading-relaxed tracking-[0.25em] text-zinc-700 sm:block"
      >
        {cornerLabel}
      </div>

      <div className={`relative z-10 w-full ${maxWidthClassName}`}>
        <div className="mb-8 flex flex-col items-center text-center">
          <span className="text-[10px] uppercase tracking-[0.35em] text-emerald-500">{eyebrow}</span>
          <h1 className="mt-2 text-3xl italic text-zinc-50 [font-family:var(--font-onboarding-display)]">
            Plant<span className="not-italic text-emerald-500">Geo</span>
          </h1>
        </div>

        <div className="relative rounded-xl border border-zinc-800 bg-zinc-900/70 p-8 shadow-2xl shadow-black/40 backdrop-blur-xl">
          <div
            aria-hidden
            className="pointer-events-none absolute inset-x-8 top-0 h-px bg-gradient-to-r from-transparent via-emerald-500/70 to-transparent"
          />
          <span
            aria-hidden
            className="pointer-events-none absolute -left-px -top-px h-4 w-4 border-l border-t border-emerald-500/40"
          />
          <span
            aria-hidden
            className="pointer-events-none absolute -right-px -top-px h-4 w-4 border-r border-t border-emerald-500/40"
          />
          <span
            aria-hidden
            className="pointer-events-none absolute -bottom-px -left-px h-4 w-4 border-b border-l border-emerald-500/40"
          />
          <span
            aria-hidden
            className="pointer-events-none absolute -bottom-px -right-px h-4 w-4 border-b border-r border-emerald-500/40"
          />

          <div className="relative">{children}</div>
        </div>
      </div>
    </div>
  );
}
