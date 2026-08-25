"use client";

import { useEffect, useId, useRef, useState } from "react";
import type maplibregl from "maplibre-gl";
import { Eye, EyeOff, RefreshCw, Trash2 } from "lucide-react";
import { LayerIcon } from "@/components/map/layer-panel/layer-icons";
import { LayerSwatch } from "@/components/map/layer-panel/LayerSwatch";
import { LayerTimeSlider } from "@/components/map/layer-panel/LayerTimeSlider";
import { LayerOpacitySlider } from "@/components/ui/layer-opacity-slider";
import {
  layerLegendSpec,
  LEGENDLESS_TOGGLE_REASONS,
  type LegendContext,
} from "@/lib/map/layer-legends";
import { layerPublicationStandingCaption } from "@/lib/map/layer-publication-standing";
import { LAYER_REGISTRY, type LayerToggleId } from "@/lib/map/layer-registry";
import { MARTIN_SOURCE_BY_LAYER_TOGGLE } from "@/lib/map/layers";
import { useMap } from "@/lib/map/map-context";
import { refreshDynamicTiles } from "@/lib/offline/tile-cache";
import {
  useLayerOpacity,
  useLayerRenderState,
  useLayerToggle,
  useToggleLayer,
} from "@/lib/map/layer-toggle-context";
import { cn } from "@/lib/utils";
import { hasSelectableDay, useTimeSliderStore } from "@/stores/time-slider-store";
// Contractually-fixed surface from the parallel lane building this store; do not stub it, do not
// edit it. See src/components/map/AGENTS.md §synced-days-track for the pinned signatures.
import {
  clearLayerSyncedDays,
  useLayerSyncedBytes,
  useSyncedDays,
  useSyncIndexReady,
} from "@/stores/sync-index-store";

interface LayerRowProps {
  layerId: LayerToggleId;
  /** The display modes that decide what this layer paints, and so what its chip shows. */
  legendContext: LegendContext;
  /**
   * True while this layer's map data is still in flight. Threaded straight through to
   * `LayerTimeSlider`'s `isFetchingCurrentDay` -- this row has no query of its own to read that
   * from, so whoever mounts `LayerRow` supplies it. Optional; omitted call sites simply show no
   * pending state. Recommended source: `useDrawnLayerDayStore((state) =>
   * state.drawnDays[layerId]?.isLoading ?? false)` from `@/stores/useMetricAtDate` -- see
   * `LayerTimeSlider`'s own doc and src/components/map/AGENTS.md §synced-days-track.
   */
  isFetchingSelectedDay?: boolean;
}

/** Bytes as a short, human count -- "0 B", "482 B", "3.2 MB". */
function formatSyncedBytes(byteCount: number): string {
  if (!Number.isFinite(byteCount) || byteCount <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  let value = byteCount;
  let unitIndex = 0;
  while (value >= 1024 && unitIndex < units.length - 1) {
    value /= 1024;
    unitIndex += 1;
  }
  return `${unitIndex === 0 ? value.toFixed(0) : value.toFixed(1)} ${units[unitIndex]}`;
}

/**
 * Destructive control for ONE layer's local sync data -- offered in the controls stack below the
 * time slider, deliberately never beside the slider's own "Latest" button. "Latest" resets the
 * slider's POSITION and is fully reversible; this empties IndexedDB entries the browser has
 * fetched and saved, which scrubbing back cannot undo. Two controls that differ that much in
 * consequence must never be reachable by the same misclick, so this one lives on its own row --
 * see src/components/map/AGENTS.md §synced-days-track for the full home decision -- and is armed
 * by an explicit second confirmation rather than firing on the first click.
 */
/**
 * "Refresh" -- the friendly surface for the one thing that makes a cached tile refetch.
 *
 * Dynamic Martin tiles are served cache-first by the service worker and are NEVER revalidated
 * on their own (see the routing comment in `public/sw.js`): a background revalidate on every
 * hit would restore exactly the per-tile origin load the cache exists to remove. So refresh is
 * the consumer's call, and this button is where a person makes it.
 *
 * Deliberately NOT merged into "Clear saved days" beside it. That control is destructive, is
 * styled and confirmed as such, and its confirm copy promises downloaded map tiles are left
 * alone. These are two different intents -- "show me new data" versus "stop storing this on my
 * device" -- and collapsing them would break a stated promise to reuse one button.
 *
 * Only rendered for toggles backed by a Martin source. A geojson-backed layer has no tile cache
 * to drop, and its data already revalidates through the query persister's own SWR path.
 */
function LayerRefreshControl({ layerId, label }: { layerId: LayerToggleId; label: string }) {
  const sourceId = MARTIN_SOURCE_BY_LAYER_TOGGLE[layerId];
  const map = useMap();
  const [state, setState] = useState<"idle" | "refreshing" | "done">("idle");

  // A layer that draws from geojson or from nothing tiled has no tile cache to drop.
  if (!sourceId) return null;

  return (
    <button
      type="button"
      onClick={async () => {
        if (state === "refreshing") return;
        setState("refreshing");
        await refreshDynamicTiles(sourceId);
        // Dropping the service-worker entry is not enough on its own: MapLibre holds its own
        // in-memory copy of every tile it has already drawn, so without this the refetch would
        // not be visible until the user panned somewhere new. Re-setting the same URL template
        // is the public API for "reload this source now".
        const source = map?.getSource(sourceId);
        if (source && "setTiles" in source) {
          const vector = source as maplibregl.VectorTileSource;
          if (Array.isArray(vector.tiles)) vector.setTiles([...vector.tiles]);
        }
        setState("done");
        window.setTimeout(() => setState("idle"), 2_000);
      }}
      data-testid={`layer-refresh-${layerId}`}
      aria-label={`Refresh ${label} from the server, discarding this device's cached tiles for it`}
      title={`Fetch ${label} again from the server. Cached tiles for this layer are dropped and re-requested; saved days are kept.`}
      className={cn(
        "inline-flex w-fit items-center gap-1 self-start rounded-(--radius) border px-1.5 py-0.5 text-[10px] max-sm:min-h-11 max-sm:px-3",
        "border-[hsl(var(--border))] text-[hsl(var(--muted-foreground))] hover:text-[hsl(var(--foreground))]"
      )}
    >
      <RefreshCw
        aria-hidden="true"
        className={cn("h-3 w-3 shrink-0", state === "refreshing" && "animate-spin")}
      />
      <span>{state === "done" ? "Refreshed" : state === "refreshing" ? "Refreshing…" : "Refresh"}</span>
    </button>
  );
}

function LayerSyncResetControl({ layerId, label }: { layerId: LayerToggleId; label: string }) {
  const syncedDays = useSyncedDays(layerId);
  const syncIndexReady = useSyncIndexReady();
  const syncedBytes = useLayerSyncedBytes(layerId);
  const [isConfirming, setIsConfirming] = useState(false);
  const [isClearing, setIsClearing] = useState(false);
  const [clearFailed, setClearFailed] = useState(false);
  const triggerButtonRef = useRef<HTMLButtonElement>(null);
  const cancelButtonRef = useRef<HTMLButtonElement>(null);
  // The count this hook reported at the moment a clear was confirmed, so the effect below can
  // tell whether the index actually changed. Null whenever no clear is outstanding.
  const countBeforeClearRef = useRef<number | null>(null);
  // Skips the focus effect's very first run: only a real open/close TRANSITION should steal
  // focus, never the row's own initial mount.
  const hasRenderedOnceRef = useRef(false);

  const syncedDayCount = syncedDays.size;
  const hasSyncedDays = syncIndexReady && syncedDayCount > 0;

  // Neither direction of the confirm dialog may move focus INLINE in the handler that flips
  // `isConfirming` -- the root element's TYPE changes (`<div role="group">` <-> `<button>`), so
  // React unmounts the OUTGOING subtree and nulls its ref in the SAME commit that starts the
  // transition. A ref read synchronously alongside that state write is therefore reading a ref
  // that is either not yet attached (opening) or was already nulled when the outgoing element
  // unmounted (closing) -- the latter is what sent focus to `<body>` on Cancel, silently, since
  // `?.focus()` on a null ref is simply a no-op. Both directions wait for the commit instead, in
  // one effect: opening focuses Cancel (the safe action, matching the "Three ways to cancel"
  // precedent the soil query-point pin sets -- src/components/map/AGENTS.md "Picking a point to
  // query"), closing -- via Cancel OR a completed Confirm, both of which flip this same flag --
  // returns focus to the trigger that opened it.
  useEffect(() => {
    if (!hasRenderedOnceRef.current) {
      hasRenderedOnceRef.current = true;
      return;
    }
    if (isConfirming) {
      cancelButtonRef.current?.focus();
    } else {
      triggerButtonRef.current?.focus();
    }
  }, [isConfirming]);

  // `clearLayerSyncedDays` is documented never to reject: a write that silently no-ops under
  // quota pressure or a version-change lock resolves exactly like a successful one, so a
  // try/catch around the call alone can never observe that failure -- it is dead code for the
  // realistic failure mode. This is the real signal: once `isClearing` drops back to false,
  // compare this hook's LATEST report against what it reported the moment the clear was
  // confirmed. A count that has not dropped is treated as a failure even though nothing threw.
  useEffect(() => {
    if (countBeforeClearRef.current === null || isClearing) return;
    const actuallyCleared = syncedDayCount < countBeforeClearRef.current;
    countBeforeClearRef.current = null;
    if (actuallyCleared) {
      setIsConfirming(false);
    } else {
      setClearFailed(true);
    }
  }, [syncedDayCount, isClearing]);

  const handleCancel = () => {
    setIsConfirming(false);
    setClearFailed(false);
  };

  const handleConfirm = async () => {
    setIsClearing(true);
    setClearFailed(false);
    countBeforeClearRef.current = syncedDayCount;
    try {
      await clearLayerSyncedDays(layerId);
    } catch {
      // Not the documented path (see above) -- but an unexpected throw is still a failure, not
      // a silent success, and the ref is cleared here so the effect above does not ALSO act on
      // whatever count it next observes.
      countBeforeClearRef.current = null;
      setClearFailed(true);
    } finally {
      setIsClearing(false);
    }
  };

  if (isConfirming) {
    return (
      <div
        role="group"
        aria-label={`Confirm clearing ${label}'s saved days`}
        data-testid={`layer-sync-reset-confirm-${layerId}`}
        onKeyDown={(event) => {
          if (event.key === "Escape") handleCancel();
        }}
        className="flex flex-col gap-1 rounded-(--radius) border border-[hsl(var(--destructive)/0.4)] bg-[hsl(var(--destructive)/0.08)] p-1.5"
      >
        <p className="text-[10px] leading-relaxed text-[hsl(var(--foreground))]">
          Clear {syncedDayCount} saved {syncedDayCount === 1 ? "day" : "days"} (about{" "}
          {formatSyncedBytes(syncedBytes)}) of {label} from this device? Downloaded map tiles and
          anything not yet synced to the server are not affected.
        </p>
        {clearFailed && (
          <p className="text-[10px] text-[hsl(var(--destructive))]">
            Could not clear -- nothing here changed. Try again.
          </p>
        )}
        <div className="flex gap-1">
          <button
            type="button"
            ref={cancelButtonRef}
            onClick={handleCancel}
            disabled={isClearing}
            aria-label={`Cancel clearing ${label}'s saved days`}
            data-testid={`layer-sync-reset-cancel-${layerId}`}
            className="flex-1 rounded-(--radius) border border-[hsl(var(--border))] px-1.5 py-1 text-[10px] text-[hsl(var(--foreground))] disabled:cursor-not-allowed disabled:opacity-50 max-sm:min-h-11"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={handleConfirm}
            disabled={isClearing}
            aria-label={`Confirm clearing ${syncedDayCount} saved days of ${label}`}
            data-testid={`layer-sync-reset-confirm-button-${layerId}`}
            className="flex-1 rounded-(--radius) border border-[hsl(var(--destructive))] bg-[hsl(var(--destructive))] px-1.5 py-1 text-[10px] font-medium text-[hsl(var(--destructive-foreground))] disabled:cursor-not-allowed disabled:opacity-50 max-sm:min-h-11"
          >
            {isClearing ? "Clearing…" : "Clear from device"}
          </button>
        </div>
      </div>
    );
  }

  return (
    <button
      type="button"
      ref={triggerButtonRef}
      // `aria-disabled`, deliberately NOT the native `disabled` attribute: `clearLayerSyncedDays`
      // clears a layer's entries wholesale, so EVERY successful clear drops this button's own
      // `hasSyncedDays` to false in the exact commit that closes the dialog and tries to return
      // focus here (see the focus-management effect above). A native `disabled` element cannot
      // receive `.focus()` -- the browser simply refuses -- so that focus-return call would have
      // been a silent no-op on the one path it exists for, and focus would fall to `<body>`
      // regardless of the fix above. `aria-disabled` keeps the element focusable and in the tab
      // order (a secondary benefit: a screen-reader user can still discover this control exists
      // even when it currently has nothing to do), so the click handler guards the action itself
      // instead of relying on the browser to block it.
      onClick={() => {
        if (!hasSyncedDays) return;
        setIsConfirming(true);
      }}
      aria-disabled={!hasSyncedDays || undefined}
      data-testid={`layer-sync-reset-${layerId}`}
      aria-label={
        !syncIndexReady
          ? `Checking what's saved locally for ${label}`
          : hasSyncedDays
            ? `Clear ${syncedDayCount} saved days of ${label} from this device (about ${formatSyncedBytes(syncedBytes)})`
            : `${label} has nothing saved on this device`
      }
      title={
        !syncIndexReady
          ? "Checking what's saved on this device…"
          : hasSyncedDays
            ? `${syncedDayCount} ${syncedDayCount === 1 ? "day" : "days"} saved on this device (about ${formatSyncedBytes(syncedBytes)}). Clears only ${label}'s own saved days.`
            : "Nothing saved on this device for this layer yet."
      }
      className={cn(
        "inline-flex w-fit items-center gap-1 self-start rounded-(--radius) border px-1.5 py-0.5 text-[10px] max-sm:min-h-11 max-sm:px-3",
        hasSyncedDays
          ? "border-[hsl(var(--destructive)/0.5)] text-[hsl(var(--destructive))]"
          : "cursor-not-allowed border-[hsl(var(--border))] text-[hsl(var(--muted-foreground))] opacity-50"
      )}
    >
      <Trash2 aria-hidden="true" className="h-3 w-3 shrink-0" />
      <span>Clear saved days{hasSyncedDays ? ` (${syncedDayCount})` : ""}</span>
    </button>
  );
}

/**
 * One layer: eye, chip, name, strength.
 *
 * The eye writes `map-store.activeLayers` through the same `useToggleLayer` the sheets'
 * switches use, so the tree and the sheets are two views of one value and cannot disagree --
 * that single source is the rule in src/components/map/AGENTS.md.
 *
 * The eye and the two sliders are INDEPENDENT controls of different things. The eye sets layout
 * visibility (or mounts/unmounts the component), which is what removes a layer from
 * `queryRenderedFeatures`; the opacity slider only scales how strongly it paints and is floored
 * above zero, because a fully transparent layer still swallows clicks meant for the ground
 * beneath it (see src/lib/map/layer-opacity.ts); the time slider moves this layer's own day and
 * nothing else's.
 *
 * The time slider is per ROW because the map is a mixed-time composite since 2026-08-09: fire on
 * 2026-08-07 beside vegetation on 2025-06-14 looks like one moment in a screenshot, so the day
 * has to be stated on the row that owns it rather than once for the whole map. This row decides
 * WHETHER a layer gets that control; `LayerTimeSlider` owns everything the control then says,
 * including the mark for a layer sitting behind its own newest published day.
 */
export function LayerRow({ layerId, legendContext, isFetchingSelectedDay }: LayerRowProps) {
  const entry = LAYER_REGISTRY[layerId];
  const isToggledOn = useLayerToggle(layerId);
  const toggleLayer = useToggleLayer();
  const opacity = useLayerOpacity(layerId);
  // Advisory only, and only worth saying while the layer is on: a layer that is drawing
  // nothing because the warehouse has no rows for this layer's day is indistinguishable from
  // one the reader switched off, which is the confusion this caption exists to prevent.
  const { unavailableReason, warehouseLayerName } = useLayerRenderState(layerId);

  // Whether this row mounts a time control at all, and the answer is deliberately THREE-valued
  // collapsed to two.
  //
  // `hasSelectableDay` is the contract's single rule for "this layer is date-filtered, so it
  // must have a control" -- it already refuses a toggle no warehouse stream backs, a stream this
  // payload does not carry, a SNAPSHOT (watersheds' 9,396 basins share one 2013 WBD loaddate, so
  // a track over them would advertise years of scrubbing across an unchanging boundary set), and
  // a stream that has observed nothing. Mounting a control for those is the fabricated affordance
  // layer-legends.ts exists to prevent.
  //
  // But with NO capabilities that function cannot answer, and it returns false -- which is not
  // "this layer has no dates", it is "nobody knows yet". Treating the two alike is what stripped
  // the time control from every row during the read model's bigint 500 with nothing anywhere
  // saying why, so the unknown mounts the control and lets it state the outage. The registry
  // name is the one thing knowable without the payload, and it keeps the eight layers that never
  // had a warehouse stream out of it.
  //
  // Selected as a BOOLEAN, so the five-minute capabilities poll re-renders this row only when
  // the answer actually changes rather than on every fresh payload object.
  //
  // `streamsUnavailable` joins `capabilities === null` on the unknown side for the same reason.
  // A payload whose stream scan timed out carries only the geo.features layers, so every
  // stream-backed layer reads as "no selectable day" -- indistinguishable from a layer that
  // genuinely has no history, and the exact shape of the outage above: the sliders vanished
  // from drought, the three soil measures and the nine climate signals with nothing saying why.
  const mountsTimeSlider = useTimeSliderStore(
    (state) =>
      warehouseLayerName !== null &&
      (state.capabilities === null ||
        state.capabilities.streamsUnavailable ||
        hasSelectableDay(state.capabilities, layerId))
  );

  const withheldReason = entry.permanentlyUnavailableReason;
  const isWithheld = withheldReason !== null;
  // Stated whether the layer is on or off, unlike `unavailableReason` below: a reader who has to
  // switch a layer on to learn it draws nothing has already seen the empty map this caption
  // exists to replace. It does NOT disable the switch -- see the record's own doc for why a
  // standing is not a `permanentlyUnavailableReason`.
  const publicationStanding = layerPublicationStandingCaption(layerId);
  const isActive = !isWithheld && isToggledOn;
  const legendlessReason = LEGENDLESS_TOGGLE_REASONS[layerId] ?? null;
  const spec = layerLegendSpec(layerId, legendContext);

  // Links every caption under this row to the eye, so a screen reader landing on a disabled or
  // empty layer hears why rather than just "switch, off".
  //
  // The "not on its own latest day" mark is deliberately NOT here. `LayerTimeSlider` states it,
  // in words, inside the control that moved the day -- and stating it twice one line apart is
  // the same defect the dock already recorded once, when `TimeSlider` kept an `<h2>Map date</h2>`
  // directly under a section header saying "Map date".
  //
  // A standing REPLACES `unavailableReason` rather than stacking with it. That reason is derived
  // from a slider capability, and for a layer no lane backs the capability is either absent or
  // all-null -- which `layerAvailabilityAt` reads as `not_yet_observed` and captions "has no
  // observations this far back", a claim about HISTORY for a layer whose blocker is a publish
  // step that never runs. Two captions, one of them false, is worse than the blank map.
  const captionId = useId();
  const captions = [
    isWithheld ? withheldReason : null,
    publicationStanding,
    isActive && publicationStanding === null ? unavailableReason : null,
  ].filter((caption): caption is string => caption !== null);

  return (
    <li
      data-testid={`layer-row-${layerId}`}
      className="flex flex-col gap-1 rounded-md px-1 py-1 hover:bg-[hsl(var(--muted)/0.4)]"
    >
      <div className="grid grid-cols-[2.75rem_0.75rem_1fr_auto] items-center gap-2">
        <button
          type="button"
          role="switch"
          aria-checked={isActive}
          aria-disabled={isWithheld ? "true" : undefined}
          aria-label={`Show ${entry.label} on map`}
          aria-describedby={captions.length > 0 ? captionId : undefined}
          disabled={isWithheld}
          onClick={() => toggleLayer(layerId)}
          // 44px, the tap-target floor the rest of the map chrome holds to.
          className={[
            "flex h-11 w-11 items-center justify-center rounded focus-visible:outline-none",
            "focus-visible:ring-2 focus-visible:ring-[hsl(var(--ring))]",
            isWithheld
              ? "cursor-not-allowed opacity-50"
              : isActive
                ? "text-[hsl(var(--foreground))]"
                : "text-[hsl(var(--muted-foreground))]",
          ].join(" ")}
        >
          {isActive ? (
            <Eye aria-hidden="true" className="h-4 w-4" />
          ) : (
            <EyeOff aria-hidden="true" className="h-4 w-4" />
          )}
        </button>

        <LayerSwatch layerId={layerId} spec={spec} />

        <span
          className={[
            "flex min-w-0 items-center gap-1.5 text-xs",
            isActive
              ? "font-medium text-[hsl(var(--foreground))]"
              : "text-[hsl(var(--muted-foreground))]",
          ].join(" ")}
        >
          <LayerIcon name={entry.icon} className="h-3 w-3 shrink-0 opacity-70" />
          <span className="truncate" title={entry.label}>
            {entry.label}
          </span>
        </span>

        {/* Dimmed at full strength: 100% is the default, so calling it out every row would be
            noise. Anything below it is a deliberate change and reads as one. */}
        <span
          aria-hidden="true"
          className={[
            "text-[10px] tabular-nums",
            isActive && opacity < 1
              ? "text-[hsl(var(--foreground))]"
              : "text-[hsl(var(--muted-foreground))] opacity-50",
          ].join(" ")}
        >
          {Math.round(opacity * 100)}%
        </span>
      </div>

      {/* Both sliders appear only for a layer that is actually on: a control that adjusts
          nothing is the fabricated affordance layer-legends.ts exists to prevent. A layer that
          is on but paints nothing (soil, whose tile template is still empty) gets the opacity
          control disabled with the reason, rather than a live slider over an absent raster.

          The time slider is additionally gated on the layer having a day to select -- or on
          that being unknown -- so a snapshot and a layer with no warehouse feed behind it get no
          track rather than a dead one, while a failed capabilities load still reaches a control
          that can say so; see `mountsTimeSlider`. Stacked under the opacity slider rather than
          set beside it: the dock column is 19rem, and splitting it would leave each track under
          7rem, where a four-year axis cannot address a day at all. */}
      {isActive && (
        <div className="flex flex-col gap-1 pl-[3.5rem] pr-1">
          <LayerOpacitySlider
            layerId={layerId}
            showCaption={false}
            inertReason={legendlessReason}
          />
          {/* Wrapped so the decision this row makes -- does this layer get a time control at
              all -- is observable without reaching into the slider's own markup. The gate is the
              contract here; what the control then says, axis or outage, belongs to
              `LayerTimeSlider`.

              The sync-reset control shares this same gate rather than mounting unconditionally:
              a layer with no timeline has no per-day cache entries to speak of, so offering to
              "reset its timeline" would be the fabricated affordance layer-legends.ts already
              guards every other control on this row against. It is a SIBLING of the slot, not a
              child of it, and deliberately not part of the slider's own top row where "Latest"
              lives -- see LayerSyncResetControl's own doc for why that separation matters. */}
          {mountsTimeSlider && (
            <>
              <div data-testid={`layer-time-slider-slot-${layerId}`}>
                <LayerTimeSlider layerId={layerId} isFetchingCurrentDay={isFetchingSelectedDay} />
              </div>
              <div className="flex flex-wrap items-center gap-1">
                <LayerRefreshControl layerId={layerId} label={entry.label} />
                <LayerSyncResetControl layerId={layerId} label={entry.label} />
              </div>
            </>
          )}
        </div>
      )}

      {captions.length > 0 && (
        <p
          id={captionId}
          className="pl-[3.5rem] pr-1 text-[10px] leading-relaxed text-[hsl(var(--muted-foreground))]"
        >
          {captions.join(" ")}
        </p>
      )}
    </li>
  );
}
