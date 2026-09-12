import type { LegendBlock, LegendRampStop } from "@/lib/map/layer-legends";

/** Evenly spaced palette stops; see `LegendRampBlock` in layer-legends.ts. */
function rampGradient(stops: readonly LegendRampStop[]): string {
  if (stops.length < 2) return stops[0]?.color ?? "transparent";
  const lastIndex = stops.length - 1;
  const positioned = stops.map(
    (stop, index) => `${stop.color} ${((index / lastIndex) * 100).toFixed(2)}%`
  );
  return `linear-gradient(to right, ${positioned.join(", ")})`;
}

/** A bar's captions: its two ends, plus its middle stop when that one is captioned. */
function rampCaptions(stops: readonly LegendRampStop[]): string[] {
  const middle = stops.length >= 3 ? stops[Math.floor(stops.length / 2)].label : undefined;
  return [stops[0]?.label, middle, stops[stops.length - 1]?.label].filter(
    (label): label is string => label !== undefined
  );
}

/** Shared encoding blocks for the collapsed manager's legend and active layer rows. */
export function LegendBlockView({ block }: { block: LegendBlock }) {
  if (block.kind === "note") {
    return (
      <p className="text-[10px] leading-snug text-[hsl(var(--muted-foreground))]">
        {block.text}
      </p>
    );
  }

  if (block.kind === "swatch") {
    return (
      <div className="flex items-center gap-2">
        <span
          aria-hidden="true"
          className="h-3 w-3 shrink-0 rounded-sm border"
          style={{
            backgroundColor: block.fillColor ?? "transparent",
            borderColor: block.outlineColor,
          }}
        />
        <span className="text-xs text-[hsl(var(--foreground))]">{block.label}</span>
      </div>
    );
  }

  if (block.kind === "ramp") {
    const captions = rampCaptions(block.stops);
    return (
      <div className="flex flex-col gap-1">
        {block.caption !== undefined && (
          <span className="text-[10px] text-[hsl(var(--muted-foreground))]">
            {block.caption}
          </span>
        )}
        <span
          aria-hidden="true"
          className="block h-2 w-full rounded-full"
          style={{ backgroundImage: rampGradient(block.stops) }}
        />
        {captions.length > 0 && (
          <div className="flex justify-between gap-2 text-[10px] text-[hsl(var(--muted-foreground))]">
            {captions.map((caption) => (
              <span key={caption}>{caption}</span>
            ))}
          </div>
        )}
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-1">
      {block.caption !== undefined && (
        <span className="text-[10px] text-[hsl(var(--muted-foreground))]">
          {block.caption}
        </span>
      )}
      <ul className="flex flex-col gap-1">
        {block.classes.map((legendClass) => (
          <li key={legendClass.label} className="flex items-center gap-2">
            <span
              aria-hidden="true"
              className={`h-3 w-3 shrink-0 border border-black/10 ${
                block.shape === "dot" ? "rounded-full" : "rounded-sm"
              }`}
              style={{ backgroundColor: legendClass.color }}
            />
            <span className="text-xs leading-snug text-[hsl(var(--foreground))]">
              {legendClass.label}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}
