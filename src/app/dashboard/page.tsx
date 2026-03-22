import Link from "next/link";
import { DashboardGrid } from "@/components/dashboard/DashboardGrid";

export default function DashboardPage() {
  return (
    <div className="min-h-screen bg-[hsl(var(--background))] flex flex-col">
      {/* Header */}
      <header className="h-14 border-b border-[hsl(var(--border))] flex items-center justify-between px-6">
        <div className="flex items-center gap-4">
          <Link href="/" className="text-lg font-bold text-emerald-500">
            PlantGeo
          </Link>
          <span className="text-[hsl(var(--muted-foreground))] text-sm">/</span>
          <span className="text-sm text-[hsl(var(--foreground))]">Analytics Dashboard</span>
        </div>
        <Link
          href="/"
          className="text-sm text-[hsl(var(--muted-foreground))] hover:text-[hsl(var(--foreground))] transition-colors flex items-center gap-1"
        >
          Open Map →
        </Link>
      </header>

      {/* Split layout */}
      <div className="flex flex-1 overflow-hidden dashboard-layout">
        {/* Left: dashboard panels */}
        <div className="flex-1 overflow-y-auto p-6 dashboard-panels">
          <DashboardGrid />
        </div>

        {/* Right: map preview */}
        <div className="w-[420px] border-l border-[hsl(var(--border))] flex flex-col dashboard-map-preview">
          <div className="px-4 py-3 border-b border-[hsl(var(--border))]">
            <span className="text-sm font-semibold text-[hsl(var(--foreground))]">Map Preview</span>
          </div>
          <div className="flex-1 relative bg-[hsl(var(--muted))] flex items-center justify-center">
            <div className="text-center flex flex-col gap-3">
              <span className="text-4xl">🗺️</span>
              <p className="text-sm text-[hsl(var(--muted-foreground))]">
                Interactive map available on the main view
              </p>
              <Link
                href="/"
                className="inline-flex items-center justify-center px-4 py-2 rounded-lg bg-emerald-500 text-white text-sm font-medium hover:bg-emerald-600 transition-colors"
              >
                Open Full Map
              </Link>
            </div>
          </div>
          {/* Map screenshot link */}
          <div className="px-4 py-3 border-t border-[hsl(var(--border))]">
            <button
              onClick={() => typeof window !== "undefined" && window.open("/", "_blank")}
              className="w-full text-xs text-[hsl(var(--muted-foreground))] hover:text-[hsl(var(--foreground))] transition-colors text-center"
            >
              Open map in new tab for screenshot
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
