"use client";

import { useEffect } from "react";
import { TRACK_REGION_APPEARANCE } from "@/components/map/layer-panel/layer-coverage-track";
import type { LayerTimeState } from "@/components/map/layer-panel/layer-time-state";
import type { LayerToggleId } from "@/lib/map/layer-registry";
import { cn } from "@/lib/utils";

/**
 * Injected once per document, exactly like `LayerTimeSlider`'s own block and for the same reason:
 * one of these mounts per switched-on row, so a component-body `<style>` would put a dozen
 * identical copies in the DOM.
 *
 * NOTE: no backticks anywhere in this string or its comments -- it is a template literal, and one
 * backtick terminates it early and breaks the file. This bit `LayerTimeSlider` once already.
 */
const LAYER_TIME_STATUS_STYLE_ELEMENT_ID = "plantgeo-layer-time-status-styles";

const layerTimeStatusStyles = `
  /* The whole point of the delay: a warm getSliderCapabilities answers in ~0.28s and a cold one
     in 7.6-8.5s, and the row must not flash a loading block through the warm case. Held at
     opacity 0 for 400ms, then faded in -- so a fast load shows nothing at all and a slow one gets
     a real, unmissable loading state instead of the blank row this replaces. A pure-CSS delay,
     deliberately: a setTimeout would be a second clock per row to keep in step with the fetch. */
  .layer-time-status-deferred {
    opacity: 0;
    animation: plantgeo-layer-time-status-appear 180ms ease-out 400ms forwards;
  }
  /* The delay stays -- it is timing, not motion, and it is what protects the warm load. Only the
     fade is dropped. */
  @media (prefers-reduced-motion: reduce) {
    .layer-time-status-deferred {
      animation-duration: 1ms;
    }
  }
  @keyframes plantgeo-layer-time-status-appear {
    to { opacity: 1; }
  }
  /* The placeholder track for a state that is still settling: a highlight sweeping along a bar of
     exactly the real track's height, so the row keeps its geometry and nothing jumps when the
     axis lands. Distinct from the inert hatch a settled state wears, which is the one thing a
     reader has to be able to tell apart at a glance -- "on its way" versus "this is the answer". */
  .layer-time-status-track-settling {
    background-color: hsl(var(--muted-foreground) / 0.15);
    background-image: linear-gradient(
      90deg,
      transparent 0%,
      hsl(var(--primary) / 0.45) 50%,
      transparent 100%
    );
    background-repeat: no-repeat;
    background-size: 60% 100%;
    background-position: -60% 0;
    animation: plantgeo-layer-time-status-sweep 1.5s ease-in-out infinite;
  }
  /* Static under reduced motion, and still visibly different from the inert hatch: the highlight
     parks in the middle rather than sweeping. The state stays tellable without movement. */
  @media (prefers-reduced-motion: reduce) {
    .layer-time-status-track-settling {
      animation: none;
      background-position: 20% 0;
    }
  }
  @keyframes plantgeo-layer-time-status-sweep {
    0% { background-position: -60% 0; }
    100% { background-position: 160% 0; }
  }
`;

function useLayerTimeStatusStyles(): void {
  useEffect(() => {
    if (typeof document === "undefined") return;
    if (document.getElementById(LAYER_TIME_STATUS_STYLE_ELEMENT_ID) !== null) return;
    const style = document.createElement("style");
    style.id = LAYER_TIME_STATUS_STYLE_ELEMENT_ID;
    style.textContent = layerTimeStatusStyles;
    document.head.appendChild(style);
  }, []);
}

export interface LayerTimeStatusProps {
  layerId: LayerToggleId;
  state: LayerTimeState;
  /**
   * The day this layer is drawing as of, or null when nothing can name one.
   *
   * Null only before the capabilities payload lands -- `resolveLayerDate` falls back to the
   * server's today for every layer it cannot answer for, and to `UNINITIALIZED_DATE` only when
   * even that is unknown. A layer with no axis is still ON THE MAP as of some day, and a
   * mixed-time composite is readable only while every row admits its own; that rule does not
   * lapse just because the day cannot be moved.
   */
  drawingDate: string | null;
  className?: string;
}

/**
 * One layer's time control when there is no axis to scrub: the uniform block every row falls back
 * to, in every one of the five non-`ready` states.
 *
 * The owner's ask, verbatim, was "ideally all the layer UI interfaces can be uniform". This is
 * where that is kept. Before it, the same row could render three different things and one of them
 * was NOTHING: a failed fetch got a sentence, a mounted-without-an-axis layer got a different
 * sentence, an in-flight fetch got a bare `return null`, and a WITHHELD layer got no control at
 * all because `LayerRow`'s gate refused to mount one. So "the census is still cold" and "this
 * lane has never published a byte" were the same blank row, which is exactly the confusion this
 * component exists to end.
 *
 * Every state now renders the identical three-part shape -- chip, placeholder track, sentence --
 * so the states differ in what they SAY and never in whether they say anything. The chip is a
 * noun phrase about the layer; the track is the real track's height, so nothing on the row jumps
 * when an axis finally lands; the sentence names the cause.
 *
 * No `aria-live`. A dozen rows entering the loading state on one map load would announce a dozen
 * times over whatever the reader was doing; `aria-busy` on the block is the silent, standard
 * signal for the same fact, and the sentence itself is ordinary DOM text the reader reaches by
 * reading the row.
 *
 * Presentational and prop-driven -- it reads no store at all, so every state renders from a
 * fixture with no provider.
 */
export function LayerTimeStatus({
  layerId,
  state,
  drawingDate,
  className,
}: LayerTimeStatusProps) {
  useLayerTimeStatusStyles();

  const isSettling = state.isSettling;
  // The operator's copy of the same fact: the wire's own reason spelling and the physical lanes
  // behind it. Deliberately NOT in the visible sentence -- `availability_unpublished` teaches a
  // user nothing -- but one hover away from anyone grepping the serving side for it.
  const evidenceTitle =
    state.reason === null
      ? undefined
      : state.evidenceLanes.length > 0
        ? `${state.reason} (${state.evidenceLanes.join(", ")})`
        : state.reason;

  return (
    <div
      className={cn(
        "flex flex-col gap-1",
        // Only the first load is deferred. A withheld or snapshot layer is a settled fact that
        // will not change while the reader looks at it, so making them fade in would add 400ms of
        // blank row to a state that could have been stated immediately.
        state.kind === "loading" && "layer-time-status-deferred",
        className
      )}
      data-testid={`layer-time-status-${layerId}`}
      data-state={state.kind}
      data-reason={state.reason ?? undefined}
      aria-busy={isSettling ? "true" : undefined}
    >
      <div className="flex flex-wrap items-center gap-1">
        <span
          data-testid={`layer-time-status-badge-${layerId}`}
          title={evidenceTitle}
          className={cn(
            "shrink-0 rounded-(--radius) px-1 py-0.5 text-[10px] font-semibold uppercase tracking-wide",
            // Two tones, and only two: something is happening, or this is the answer. A third
            // colour per reason would turn fifteen reasons into fifteen things to learn, and the
            // sentence beside it already carries the distinction that matters.
            isSettling
              ? "bg-[hsl(var(--primary))]/15 text-[hsl(var(--primary))]"
              : "bg-[hsl(var(--muted-foreground))]/20 text-[hsl(var(--muted-foreground))]"
          )}
        >
          {state.badge}
        </span>

        {/* The day the layer is painting, in the same tabular-nums treatment and the same place
            the real control puts its date field, so the eye finds it identically either way. */}
        {drawingDate !== null && (
          <span
            data-testid={`layer-time-status-date-${layerId}`}
            className="tabular-nums text-[10px] text-[hsl(var(--foreground))]"
          >
            {drawingDate}
          </span>
        )}
      </div>

      {/* h-5 and h-1.5 are the real track's own two heights, so the switch from this block to a
          live axis moves nothing on the row. `aria-hidden` because it draws no fact the sentence
          below does not already state in words. */}
      <div aria-hidden="true" className="relative flex h-5 items-center max-sm:h-11">
        <div
          data-testid={`layer-time-status-track-${layerId}`}
          className={cn(
            "absolute inset-x-0 h-1.5 rounded-full",
            isSettling && "layer-time-status-track-settling"
          )}
          // The settled texture is the coverage track's own `undescribed` hatch, which already
          // means "no claim is made about these days" everywhere else in this dock. Reusing it
          // rather than inventing a sixth texture is what keeps one visual vocabulary across the
          // control and its fallback.
          style={
            isSettling
              ? undefined
              : {
                  backgroundColor: TRACK_REGION_APPEARANCE.undescribed.backgroundColor,
                  backgroundImage: TRACK_REGION_APPEARANCE.undescribed.backgroundImage,
                }
          }
        />
      </div>

      <p
        data-testid={`layer-time-status-detail-${layerId}`}
        className="text-[10px] leading-relaxed text-[hsl(var(--muted-foreground))]"
      >
        {state.detail}
      </p>
    </div>
  );
}
