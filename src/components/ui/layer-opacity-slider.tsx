"use client";

import { useCallback, useEffect, useId, useRef, useState } from "react";
import { Slider } from "@/components/ui/slider";
import {
  LAYER_OPACITY_STEP,
  MIN_LAYER_OPACITY,
  DEFAULT_LAYER_OPACITY,
} from "@/lib/map/layer-opacity";
import { layerLabel, type LayerToggleId } from "@/lib/map/layer-registry";
import { useLayerOpacity, useSetLayerOpacity } from "@/lib/map/layer-toggle-context";
import { cn } from "@/lib/utils";

/**
 * A committed value plus a draft the input renders, with the commit coalesced to one
 * animation frame.
 *
 * The map repaints once per frame regardless, so more than one write per frame is waste --
 * and for the two soil-field outlines each write recompiles a `["*", ["case", ...], f]`
 * expression. Coalescing here rather than in each of the ten writers means every consumer of
 * the store is throttled by one implementation: the Lane-A applier in LayerManager and all
 * nine layer components see at most one new value per frame.
 *
 * The draft is what keeps the input from lagging its own thumb. It re-syncs from the store
 * whenever nothing of this slider's own is in flight, so a value set from anywhere else -- the
 * layer tree while this panel is open, a rehydrated persisted blob -- still lands here.
 */
function useFrameCommittedValue(
  value: number,
  commit: (next: number) => void
): readonly [number, (next: number) => void] {
  const [draft, setDraft] = useState(value);
  const frameRef = useRef<number | null>(null);
  const pendingRef = useRef<number | null>(null);
  const commitRef = useRef(commit);

  useEffect(() => {
    commitRef.current = commit;
  }, [commit]);

  useEffect(() => {
    if (pendingRef.current === null) setDraft(value);
  }, [value]);

  useEffect(
    () => () => {
      if (frameRef.current !== null) cancelAnimationFrame(frameRef.current);
    },
    []
  );

  const push = useCallback((next: number) => {
    setDraft(next);
    pendingRef.current = next;
    if (frameRef.current !== null) return;
    frameRef.current = requestAnimationFrame(() => {
      frameRef.current = null;
      const queued = pendingRef.current;
      pendingRef.current = null;
      if (queued !== null) commitRef.current(queued);
    });
  }, []);

  return [draft, push] as const;
}

interface LayerOpacitySliderProps {
  layerId: LayerToggleId;
  /** Row layouts hide the caption and name the control from the row's own label. */
  showCaption?: boolean;
  /** Set when the layer draws nothing, so the control explains itself instead of lying. */
  inertReason?: string | null;
  className?: string;
}

/**
 * The one control for a layer's opacity MULTIPLIER, wherever it is rendered.
 *
 * 100% means "exactly as the layer was designed"; the floor is `MIN_LAYER_OPACITY`, not zero,
 * because an opacity-0 layer is still hit-tested by `queryRenderedFeatures` and would swallow
 * clicks meant for the ground beneath it. The eye is how a layer is turned off.
 *
 * Native `<input type="range">` underneath (via ui/slider.tsx), so arrow keys step by
 * `LAYER_OPACITY_STEP` and Home/End/PageUp/PageDown work without any keyboard code here.
 */
export function LayerOpacitySlider({
  layerId,
  showCaption = true,
  inertReason = null,
  className,
}: LayerOpacitySliderProps) {
  const storedOpacity = useLayerOpacity(layerId);
  const setLayerOpacity = useSetLayerOpacity();
  const captionId = useId();

  const commit = useCallback(
    (next: number) => setLayerOpacity(layerId, next),
    [setLayerOpacity, layerId]
  );
  const [opacity, setOpacity] = useFrameCommittedValue(storedOpacity, commit);

  const percent = Math.round(opacity * 100);
  const isInert = inertReason !== null;

  return (
    <div className={cn("flex flex-col gap-1", className)}>
      {showCaption && (
        <div className="flex items-center justify-between gap-2">
          <span className="text-[10px] text-[hsl(var(--muted-foreground))]">Opacity</span>
          <span
            id={captionId}
            className="text-[10px] tabular-nums text-[hsl(var(--muted-foreground))]"
            data-testid={`layer-opacity-readout-${layerId}`}
          >
            {percent}%
          </span>
        </div>
      )}
      {/* The percentage is DESCRIBED, never folded into the name: the name has to stay stable
          as the thumb moves, and aria-valuetext already speaks the reading. */}
      <Slider
        min={MIN_LAYER_OPACITY}
        max={DEFAULT_LAYER_OPACITY}
        step={LAYER_OPACITY_STEP}
        value={opacity}
        onValueChange={setOpacity}
        disabled={isInert}
        aria-label={`${layerLabel(layerId)} opacity`}
        aria-valuetext={`${percent} percent`}
        aria-describedby={showCaption ? captionId : undefined}
        // The track's own box is 6px; grown to the 44px tap target on small screens, which is
        // what ui/slider.tsx made overridable rather than hard-coding.
        className="max-sm:h-11"
      />
      {isInert && (
        <p className="text-[10px] leading-relaxed text-[hsl(var(--muted-foreground))]">
          {inertReason}
        </p>
      )}
    </div>
  );
}
