"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import dynamic from "next/dynamic";
import maplibregl from "maplibre-gl";
import { Eye, EyeOff, MapPin, Sparkles, Sprout, X } from "lucide-react";
import { LayerIcon } from "@/components/map/layer-panel/layer-icons";
import { AgentInteraction } from "@/components/map/AgentInteraction";
import { InterventionProposalForm } from "@/components/panels/InterventionProposalForm";
import { getStyle } from "@/lib/map/styles";
import { LAYER_REGISTRY, type LayerToggleId } from "@/lib/map/layer-registry";
import { useLayerToggle, useToggleLayer } from "@/lib/map/layer-toggle-context";
import { hasInterventionDraftWork, useInterventionDraftStore } from "@/stores/intervention-draft-store";
import { useMapStore } from "@/stores/map-store";
import { useRegionalIntelligenceStore } from "@/stores/regional-intelligence-store";
import { useRegionalIntelligence } from "@/hooks/useRegionalIntelligence";
import { cn } from "@/lib/utils";

/**
 * The AI pane keeps the client-only dynamic import its previous mount site in `MapView` used.
 *
 * `InterventionProposalForm` is imported STATICALLY, unlike it: the shell's mount effect needs
 * the form's map container node to exist on the first commit, and a dynamically imported child
 * renders nothing until its chunk resolves -- which would leave the drawing map uncreated. The
 * form is already client-only (`"use client"`) and defers terra-draw itself through
 * `InterventionDrawControl`'s own `ssr: false` import, so nothing server-only is pulled in.
 */
const RegionalIntelligencePanel = dynamic(
  () => import("@/components/panels/RegionalIntelligencePanel"),
  { ssr: false }
);

/** The two things this surface can be doing. There is no third, and never zero. */
export type WorkspaceMode = "ai" | "intervention";

export interface AiInterventionWorkspaceProps {
  /** The map point the user clicked, as `[lon, lat]`. Seeds whichever mode opens. */
  coordinates: [number, number];
  /** Which pane the action the user pressed asked for. */
  initialMode: WorkspaceMode;
  /** The workspace's OWN close. Never called by the mode switch. */
  onClose: () => void;
  /**
   * Fired once an intervention proposal is actually submitted, so the map's draft overlay can be
   * invalidated. Handed straight to the proposal form, which calls it after the draft is cleared.
   */
  onInterventionSubmitted?: () => void;
  /** The workspace this proposal belongs to, when one is selected. Passed through to the form. */
  teamId?: string;
  workspaceName?: string;
}

/**
 * The one surface the map's two location actions open: an AI-analysis pane and an
 * intervention-proposal pane, switchable without either losing what it holds.
 *
 * Why this is a second control surface at all, next to the `LayerPanel` dock that
 * `src/components/map/AGENTS.md` §"One manager, no floating surfaces" says should be the only
 * one: OQ-2 of `conductor/tracks/ai_intervention_workspace_20260913/spec.md` was resolved to
 * (b), a dedicated surface. The dock convention exists to stop TWO controls writing ONE piece of
 * state; a chat transcript and a drawing form have no dock representation to disagree with. The
 * one thing here that does overlap the dock -- the layer strip below -- therefore owns no state
 * of its own and writes `map-store.activeLayers` through the very same `useToggleLayer` the
 * dock's `LayerRow` eye uses.
 *
 * Keep-alive (OQ-1(a)): BOTH panes stay mounted for the workspace's whole life and the inactive
 * one is hidden with the `hidden` attribute (display:none), never conditionally rendered. An
 * unmount would destroy an in-flight stream, a half-typed message and -- once Phase 4 ports the
 * real content in -- the embedded MapLibre draw instance and its terra-draw session. The two
 * `<div data-testid="...-mode-content">` slots below are Phase 3 placeholders; Phase 4 replaces
 * their children with `RegionalIntelligencePanel`'s and `InterventionSubmitModal`'s bodies and
 * must preserve the always-mounted shape.
 */
export function AiInterventionWorkspace({
  coordinates,
  initialMode,
  onClose,
  onInterventionSubmitted,
  teamId,
  workspaceName,
}: AiInterventionWorkspaceProps) {
  const [mode, setMode] = useState<WorkspaceMode>(initialMode);
  const workspaceRef = useRef<HTMLElement | null>(null);
  const [lastRequest, setLastRequest] = useState({ coordinates, initialMode });
  if (lastRequest.coordinates !== coordinates || lastRequest.initialMode !== initialMode) {
    setLastRequest({ coordinates, initialMode });
    setMode(initialMode);
  }
  const [lon, lat] = coordinates;

  const isAnalysisOpen = useRegionalIntelligenceStore((state) => state.isOpen);
  const analysisLocation = useRegionalIntelligenceStore((state) => state.selectedLocation);
  const hidePanel = useRegionalIntelligenceStore((state) => state.hidePanel);
  const showPanel = useRegionalIntelligenceStore((state) => state.showPanel);

  /**
   * While the workspace is open it OWNS the conversation: the standalone right-edge copy of
   * `RegionalIntelligencePanel` that `MapView` still renders stands down, or the two would sit
   * on top of each other showing the same messages. `hidePanel`/`showPanel` are visibility only
   * -- unlike `closePanel`, they touch neither the transcript nor an in-flight request -- so
   * the analysis survives this handover in both directions.
   */
  useEffect(() => {
    hidePanel();
    return () => {
      showPanel();
    };
  }, [hidePanel, showPanel]);

  /**
   * The drawing map, created ONCE per workspace session and owned here rather than by the
   * proposal form (OQ-1(a)). The form renders the container node; this effect fills it. Living
   * at the shell means the instance -- and with it terra-draw's live session, its handles and
   * its undo history -- outlives every mode switch, because the shell only hides the inactive
   * pane and never unmounts it.
   *
   * `[]` deps on purpose: re-centring or restyling would destroy and rebuild the very instance
   * this hoist exists to preserve. The style is read imperatively for the same reason.
   */
  const drawMapContainerRef = useRef<HTMLDivElement | null>(null);
  const [drawMap, setDrawMap] = useState<maplibregl.Map | null>(null);

  useEffect(() => {
    if (mode === "intervention") drawMap?.resize();
  }, [drawMap, mode]);

  useEffect(() => {
    const container = drawMapContainerRef.current;
    if (!container) return;
    const draft = useInterventionDraftStore.getState();
    const center: [number, number] = hasInterventionDraftWork(draft)
      ? [draft.lon ?? coordinates[0], draft.lat ?? coordinates[1]]
      : coordinates;
    const instance = new maplibregl.Map({
      container,
      // The same style the main map renders, so this shows real basemap tiles; the
      // "pmtiles://" protocol is already registered globally by MapView's initMap.
      style: getStyle(useMapStore.getState().currentStyle),
      center,
      zoom: 14,
    });
    setDrawMap(instance);
    return () => {
      instance?.remove?.();
      setDrawMap(null);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- one instance per workspace session
  }, []);

  const seedLocation = useInterventionDraftStore((state) => state.seedLocation);
  const draftLat = useInterventionDraftStore((state) => state.lat);
  const draftLon = useInterventionDraftStore((state) => state.lon);
  const hasDraftWork = useInterventionDraftStore(hasInterventionDraftWork);
  // Only the streaming flag: every token of an analysis writes this store.
  const isAnalysisStreaming = useRegionalIntelligenceStore((state) => state.isLoading);

  // Preserve unfinished form fields and geometry; see panels/AGENTS.md workspace lifecycle.
  useEffect(() => {
    const draft = useInterventionDraftStore.getState();
    const { lat: currentLat, lon: currentLon } = draft;
    if (currentLat === lat && currentLon === lon) return;
    if (hasInterventionDraftWork(draft)) return;
    seedLocation(lat, lon);
  }, [lat, lon, seedLocation]);

  /**
   * The point the user was offered but that was not applied, because applying it would have
   * reset work they have not submitted. DERIVED, never state: the seeding effect above is the
   * only thing that can resolve it, and a `useState` mirror of a store value the same component
   * already subscribes to is a second copy that can only go stale.
   */
  const pendingLocation: [number, number] | null =
    hasDraftWork && (draftLat !== lat || draftLon !== lon) ? [lon, lat] : null;

  const hasUnsavedWork = hasDraftWork || isAnalysisStreaming;
  const displayedLocation = mode === "ai" && analysisLocation
    ? analysisLocation
    : { lat: draftLat ?? lat, lon: draftLon ?? lon, precision: null };
  const displayedDigits = displayedLocation.precision === "approximate" ? 2
    : displayedLocation.precision === "exact" ? 6 : 4;

  // Explicit close ends both sessions after confirming any unfinished work.
  const handleClose = useCallback(() => {
    if (hasDraftWork || isAnalysisStreaming) {
      const losses = [
        hasDraftWork ? "your unsubmitted intervention proposal" : null,
        isAnalysisStreaming ? "an AI analysis that is still streaming" : null,
      ].filter((loss): loss is string => loss !== null);
      const confirmed = window.confirm(
        `Closing this workspace discards ${losses.join(" and ")}. Close anyway?`
      );
      if (!confirmed) return;
    }
    // Closing the workspace ends the conversation, exactly as the standalone panel's own X did:
    // `closePanel` aborts any request and clears the transcript. Without it the panel would
    // reappear as a right-edge overlay the moment this shell unmounted and released `hidePanel`.
    useRegionalIntelligenceStore.getState().closePanel();
    useInterventionDraftStore.getState().clearDraft();
    onClose();
  }, [hasDraftWork, isAnalysisStreaming, onClose]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape" || event.defaultPrevented) return;
      const dialog = event.target instanceof Element ? event.target.closest('[role="dialog"]') : null;
      if (dialog && dialog !== workspaceRef.current) return;
      if (document.querySelector('[role="dialog"][aria-modal="true"]')) return;
      event.preventDefault();
      handleClose();
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [handleClose]);

  return (
    <section
      ref={workspaceRef}
      // Deliberately NOT `aria-modal`: the point of this track is that the map, its layers and
      // the dock stay reachable while the workspace is open. The intervention modal it replaces
      // was `aria-modal="true"` and blocked everything behind it.
      role="dialog"
      aria-modal="false"
      aria-label="AI and intervention workspace"
      data-testid="ai-intervention-workspace"
      // Right edge, matching `RegionalIntelligencePanel`'s existing anchor, so the left-edge
      // `LayerPanel` dock (`left-0 w-[19rem]`, see panel-scroll.ts PANEL_SHELL) and
      // `ManagerRail`'s bottom-left corner are both left clear (FR-5).
      //
      // The width is capped rather than fixed at 24rem because 24rem + the dock's 19rem is
      // 688px: on a viewport between the `sm` breakpoint (640px) and that, a flat `w-96` would
      // have run UNDER the dock, and this sits at z-30 against the dock's z-10, so the dock
      // would have been the one hidden. Below `sm` the workspace goes full-screen, which is the
      // same takeover the dock itself performs there.
      className="absolute right-0 top-0 z-30 flex h-full w-[min(24rem,calc(100vw-19rem))] flex-col border-l border-[hsl(var(--border))] bg-[hsl(var(--card))] text-[hsl(var(--card-foreground))] shadow-xl max-sm:w-full"
    >
      <header className="flex items-start justify-between gap-3 border-b border-[hsl(var(--border))] p-3">
        <div className="flex min-w-0 items-center gap-2">
          <MapPin aria-hidden="true" className="h-4 w-4 shrink-0 text-[hsl(var(--primary))]" />
          <p className="truncate font-mono text-xs text-[hsl(var(--muted-foreground))]">
              {displayedLocation.lat.toFixed(displayedDigits)}, {displayedLocation.lon.toFixed(displayedDigits)}
          </p>
        </div>
        <button
          type="button"
          onClick={handleClose}
          aria-label="Close workspace"
          data-testid="workspace-close"
          className="inline-flex min-h-11 min-w-11 items-center justify-center rounded text-[hsl(var(--muted-foreground))] hover:bg-[hsl(var(--muted))] hover:text-[hsl(var(--foreground))] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--ring))]"
        >
          <X aria-hidden="true" className="h-4 w-4" />
        </button>
      </header>

      {/* The one switch affordance. Both modes are named on it and there is no position in
          which neither pane is showing -- FR-1's first acceptance criterion. */}
      <div
        role="tablist"
        aria-label="Workspace mode"
        className="flex gap-1 border-b border-[hsl(var(--border))] p-1"
      >
        <WorkspaceModeTab
          mode="ai"
          activeMode={mode}
          onSelect={setMode}
          label="AI analysis"
          icon={<Sparkles aria-hidden="true" className="h-4 w-4" />}
        />
        <WorkspaceModeTab
          mode="intervention"
          activeMode={mode}
          onSelect={setMode}
          label="Propose intervention"
          icon={<Sprout aria-hidden="true" className="h-4 w-4" />}
        />
      </div>

      <WorkspaceLayerStrip />

      {pendingLocation !== null && (
        <p
          data-testid="workspace-draft-relocate"
          className="border-b border-[hsl(var(--border))] bg-[hsl(var(--muted)/0.5)] px-3 py-2 text-[11px] leading-relaxed text-[hsl(var(--muted-foreground))]"
        >
          You have an unsubmitted proposal at {draftLat?.toFixed(4)}, {draftLon?.toFixed(4)}. It
          was kept. To start a new proposal at {pendingLocation[1].toFixed(4)},{" "}
          {pendingLocation[0].toFixed(4)}, close this workspace, confirm discarding the draft,
          then select that location on the map again.
        </p>
      )}

      {/* One scroller for the whole body, the same one-scroller discipline the dock holds to. */}
      <div className="min-h-0 flex-1 overflow-y-auto">
        {/* Both panes are ALWAYS rendered; `hidden` is what makes one of them invisible. Do not
            turn either into `{mode === "..." && ...}` -- see the keep-alive note on this
            component. An unmount here cancels a stream and destroys the draw session. */}
        <div
          id="workspace-panel-ai"
          role="tabpanel"
          aria-labelledby="workspace-tab-ai"
          data-testid="ai-mode-content"
          hidden={mode !== "ai"}
          className="h-full"
        >
          {isAnalysisOpen ? (
            <RegionalIntelligencePanel embedded />
          ) : (
            <WorkspaceAnalysisEntry
              key={`${draftLon ?? lon},${draftLat ?? lat}`}
              coordinates={[draftLon ?? lon, draftLat ?? lat]}
              onCancel={() => setMode("intervention")}
            />
          )}
        </div>
        <div
          id="workspace-panel-intervention"
          role="tabpanel"
          aria-labelledby="workspace-tab-intervention"
          data-testid="intervention-mode-content"
          hidden={mode !== "intervention"}
        >
          <InterventionProposalForm
            lat={draftLat ?? lat}
            lon={draftLon ?? lon}
            teamId={teamId}
            workspaceName={workspaceName}
            map={drawMap}
            mapContainerRef={drawMapContainerRef}
            onSubmitted={onInterventionSubmitted}
          />
        </div>
      </div>

      {hasUnsavedWork && (
        <p className="border-t border-[hsl(var(--border))] px-3 py-2 text-[11px] text-[hsl(var(--muted-foreground))]">
          Unsaved work in this workspace. Switching modes keeps it; closing asks first.
        </p>
      )}
    </section>
  );
}

/** Starts analysis only after the shared location consent is explicitly submitted. */
function WorkspaceAnalysisEntry({
  coordinates,
  onCancel,
}: {
  coordinates: [number, number];
  onCancel: () => void;
}) {
  const { queryLocation } = useRegionalIntelligence();
  const handleAnalyze = (precision: "approximate" | "exact") => {
    const digits = precision === "approximate" ? 2 : 6;
    const lat = Number(coordinates[1].toFixed(digits));
    const lon = Number(coordinates[0].toFixed(digits));
    const analysis = useRegionalIntelligenceStore.getState();
    analysis.openPanel(lat, lon, precision);
    analysis.hidePanel();
    void queryLocation(lat, lon, undefined, precision);
  };

  return (
    <AgentInteraction
      embedded
      coordinates={coordinates}
      onAnalyze={handleAnalyze}
      onProposeIntervention={onCancel}
      onClose={onCancel}
    />
  );
}

function WorkspaceModeTab({
  mode,
  activeMode,
  onSelect,
  label,
  icon,
}: {
  mode: WorkspaceMode;
  activeMode: WorkspaceMode;
  onSelect: (mode: WorkspaceMode) => void;
  label: string;
  icon: React.ReactNode;
}) {
  const isActive = mode === activeMode;
  return (
    <button
      type="button"
      role="tab"
      id={`workspace-tab-${mode}`}
      aria-selected={isActive}
      aria-controls={`workspace-panel-${mode}`}
      onClick={() => onSelect(mode)}
      data-testid={`workspace-tab-${mode}`}
      className={cn(
        "flex min-h-11 flex-1 items-center justify-center gap-2 rounded-(--radius) px-2 text-sm font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--ring))]",
        isActive
          ? "bg-[hsl(var(--primary))] text-[hsl(var(--primary-foreground))]"
          : "text-[hsl(var(--muted-foreground))] hover:bg-[hsl(var(--muted))]"
      )}
    >
      {icon}
      {label}
    </button>
  );
}

/**
 * The layers this strip offers, fixed for the workspace's lifetime.
 *
 * Whatever is already switched on when the workspace opens comes first -- those are the layers
 * the reader is actually looking at while they analyse or draw -- topped up from a short list of
 * the ones an intervention proposal most often needs. Computed ONCE, in a state initializer,
 * because a set derived live from `activeLayers` would remove a row the instant someone switched
 * it off and leave them with no way to switch it back on.
 */
const STRIP_FALLBACK_LAYERS: LayerToggleId[] = [
  "fire",
  "drought",
  "soil-moisture",
  "vegetation",
  "water",
  "interventions",
];
const STRIP_LAYER_LIMIT = 6;

function WorkspaceLayerStrip() {
  // Read imperatively, never as a subscription: this is a one-time snapshot, and subscribing
  // would defeat the point of freezing the row set.
  const [stripLayers] = useState<LayerToggleId[]>(() => {
    const chosen = useMapStore
      .getState()
      .activeLayers.filter((layerId): layerId is LayerToggleId => layerId in LAYER_REGISTRY);
    for (const toggleId of STRIP_FALLBACK_LAYERS) {
      if (chosen.length >= STRIP_LAYER_LIMIT) break;
      if (!chosen.includes(toggleId)) chosen.push(toggleId);
    }
    return chosen.slice(0, STRIP_LAYER_LIMIT);
  });

  return (
    <div
      role="group"
      aria-label="Map layers"
      data-testid="workspace-layer-strip"
      className="flex flex-wrap gap-1 border-b border-[hsl(var(--border))] px-2 py-1.5"
    >
      {stripLayers.map((layerId) => (
        <WorkspaceLayerToggle key={layerId} layerId={layerId} />
      ))}
    </div>
  );
}

/**
 * One layer, one eye -- the SAME `map-store.activeLayers` write `LayerRow`'s eye makes, through
 * the same `useToggleLayer`. This strip holds no state of its own, so it cannot drift out of
 * sync with the dock; that is the whole condition on which OQ-3 allowed a second surface here.
 */
function WorkspaceLayerToggle({ layerId }: { layerId: LayerToggleId }) {
  const entry = LAYER_REGISTRY[layerId];
  const isOn = useLayerToggle(layerId);
  const toggleLayer = useToggleLayer();
  const isWithheld = entry.permanentlyUnavailableReason !== null;
  const isActive = isOn && !isWithheld;

  return (
    <button
      type="button"
      role="switch"
      aria-checked={isActive}
      aria-disabled={isWithheld ? "true" : undefined}
      disabled={isWithheld}
      aria-label={`Toggle ${entry.label} on the map`}
      title={entry.permanentlyUnavailableReason ?? entry.label}
      data-testid={`workspace-layer-toggle-${layerId}`}
      onClick={() => toggleLayer(layerId)}
      className={cn(
        "inline-flex min-h-9 items-center gap-1 rounded-(--radius) border px-1.5 text-[11px] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--ring))]",
        isWithheld
          ? "cursor-not-allowed border-[hsl(var(--border))] text-[hsl(var(--muted-foreground))] opacity-50"
          : isActive
            ? "border-[hsl(var(--primary))] text-[hsl(var(--foreground))]"
            : "border-[hsl(var(--border))] text-[hsl(var(--muted-foreground))]"
      )}
    >
      {isActive ? (
        <Eye aria-hidden="true" className="h-3 w-3 shrink-0" />
      ) : (
        <EyeOff aria-hidden="true" className="h-3 w-3 shrink-0" />
      )}
      <LayerIcon name={entry.icon} className="h-3 w-3 shrink-0 opacity-70" />
      <span className="max-w-24 truncate">{entry.label}</span>
    </button>
  );
}
