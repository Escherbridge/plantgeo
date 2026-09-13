---
type: Track Spec
title: AI intervention workspace
description: Merge the map's AI regional-analysis session with intervention proposal creation and data-layer controls into one persistent, switchable workspace UI.
tags: [feature, ai_intervention_workspace_20260913, pending]
timestamp: 2026-09-13
resource: ./metadata.json
---

# AI intervention workspace

## Overview

Today, clicking the map opens `AgentInteraction.tsx`'s confirm popup, which offers two actions —
"Send for analysis" (opens `RegionalIntelligencePanel`, a right-edge `aside`) and "Propose
intervention here" (opens `InterventionSubmitModal`, a centered blocking modal seeded with the
clicked point, landed in `intervention_drawing_visibility_20260912`). These are two disconnected
floating surfaces: opening one does not keep the other reachable, switching between them is not
supported at all (the modal is `aria-modal="true"` and blocks interaction with everything behind
it, including the AI panel), and the AI panel's own store resets its conversation on close or on
opening a new location. This track turns that pair into one coherent, switchable workspace: an AI
chat/analysis pane and an intervention drawing/proposal pane that a user can flip between without
either discarding its in-progress state, with the map's data layers still visible underneath.

This is a UI/state-architecture track. It does not change what analysis or intervention data is
computed, validated, or persisted server-side — `useRegionalIntelligence`, the tRPC procedures, the
geometry validator, and `InterventionDrawControl`'s terra-draw wiring are reused, not rewritten.

## Background

- `src/components/map/AgentInteraction.tsx:106-129` — the two actions live on one popup; "Propose
  intervention here" was added by the prior track to seed the modal with the clicked point instead
  of defaulting to map center. That coordinate-seeding problem is solved; this track is about what
  happens after either button is pressed.
- `src/components/map/MapView.tsx:120-486` owns `agentCoords`/`interventionCoords` as two
  independent `useState` slices and conditionally mounts `AgentAnalysisPrompt` (wraps
  `AgentInteraction` + `useRegionalIntelligence`) and `InterventionProposalModal` (wraps
  `InterventionSubmitModal` + `trpc.useUtils()`). `handleProposeIntervention`
  (`MapView.tsx:154-157`) explicitly closes the agent popup (`setAgentCoords(null)`) when opening
  the intervention modal — there is no code path today where both are open, or where closing one
  returns to the other.
- `src/components/panels/RegionalIntelligencePanel.tsx:647-867` renders as an `absolute right-0
  top-0 ... w-96` `aside`. Its state lives in `useRegionalIntelligenceStore`
  (`src/stores/regional-intelligence-store.ts`), which is a Zustand store independent of the
  component's mount — so in principle a conversation *could* survive the panel unmounting. In
  practice it does not: `closePanel()` (store lines 93-110) explicitly clears `messages`,
  `conversationId`, `analysisEvidence`, and every other session field to its initial value, and
  `openPanel()` (lines 74-91) does the same reset whenever a *new* location is picked, including
  aborting any in-flight request. The only way to return to a conversation once closed is
  `resumeConversation()` against a *saved* server-side conversation (`conversationId`), not the
  live local session.
- `src/components/panels/InterventionSubmitModal.tsx:87-197` holds all its own form state
  (`category`, `interventionType`, `name`, `description`, `geometry`, ...) as plain `useState`
  inside the component, plus a second MapLibre instance it mounts itself
  (`InterventionSubmitModal.tsx:128-145`) for the embedded drawing map. None of this survives
  unmount — `MapView`'s `interventionCoords === null` unmounts the whole component, discarding the
  in-progress draw and form fields with it.
- `src/components/map/InterventionDrawControl.tsx` is a thin terra-draw wrapper keyed on the `map`
  instance prop (`useEffect(..., [map])`, line 47) — it re-initializes terra-draw whenever the map
  instance identity changes, so it has no state to preserve independent of its host modal's own
  embedded map.
- `src/components/map/layer-panel/` (see `LayerPanel.tsx:70-95` doc comment, and
  `src/components/map/AGENTS.md` §"One manager, no floating surfaces") is the map's **one** control
  surface: search, render mode, and every layer switch live in one left-edge, 304px-wide, closable
  dock (`LAYER_PANEL_WIDTH_PX`). Every other floating control surface that used to exist on this
  map (search bar, bottom toolbar, command palette, legend card) was folded into it between
  2026-08-08 and 2026-08-09 specifically because two controls over the same state, one of them out
  of sight, was a recurring defect class.
- `src/components/panels/AGENTS.md` §"These are dock sections, not panels" **explicitly carves out**
  `RegionalIntelligencePanel`, `ContributionQueue`, `LayerUpload`, `UserPanel`, and "the two submit
  modals" as **not** manager sections — "they are mounted by routes or by other components." This
  is a standing, named exception to the dock convention, not an oversight; any workspace redesign
  either extends the dock (reopening that exception) or must explicitly justify staying outside it.
- `conductor/tracks/intervention_drawing_visibility_20260912/spec.md` (the prior track) does not
  discuss UI unification at all — it scoped drawing, land/air category, area caps, and the
  draft/proposed map overlay, and left the modal as a standalone floating surface. Its "Open
  Questions" section covers unrelated schema/policy questions (category storage, air-acreage
  ceiling, rejected-submission visibility) and is silent on the coordinate-picking/panel-merge
  question this track answers.

## Open Questions

**Resolved 2026-09-13 by the product owner:**
- **OQ-1 → (a) True keep-alive.** Both panes stay mounted; hidden via CSS rather than unmounted.
  `regional-intelligence-store.ts` needs a hide/show pair distinct from `closePanel`; the embedded
  MapLibre draw-map instance's mount effect must move up to the shell so it survives a mode switch.
- **OQ-2 → (b) New dedicated surface.** Not folded into the `LayerPanel` dock. The panels AGENTS.md
  exception note gets formalized to name this workspace explicitly.
- **OQ-3 → dedicated layer-visibility controls inside the workspace**, diverging from this spec's
  "LayerPanel-visible-is-sufficient" recommendation. These controls must read/write the *same*
  `map-store`/`layer-registry` state `LayerPanel` already owns — a second UI surface over one state
  owner, not a second store — so this does not reopen the "two controls over the same state" defect
  the dock convention exists to prevent. Scope: a compact toggle strip for the currently-active
  layers, not a full re-implementation of `LayerPanel`'s controls.

These must be resolved by the user/product owner before implementation; the plan below stages work
so that answering them late is possible but costly (see Phase 1). None of these have been decided
by this spec.

### OQ-1: What must "switch back and forth without losing session" concretely require?

Does an in-progress AI analysis conversation need to survive while the user switches to drawing an
intervention, and vice versa? Two candidate answers, with different cost:

- **(a) True keep-alive**: both the AI chat pane's DOM/component tree and the intervention drawing
  pane's DOM/component tree (including the embedded MapLibre draw map and its terra-draw instance)
  stay mounted simultaneously, one hidden via CSS rather than unmounted, so neither loses
  in-progress, un-submitted local state (a drafted-but-not-submitted geometry, an unsent chat input
  draft, in-flight streaming) when the other is brought to front. This is a real behavior change:
  today `RegionalIntelligenceStore.closePanel()`/`openPanel()` actively clear state, and
  `InterventionSubmitModal`'s state is plain `useState` tied to mount.
- **(b) Restore-from-store is enough**: the AI *conversation history* (already durable once a
  message round-trips and is saved server-side, per `resumeConversation()`) is sufficient, and the
  intervention *form* is allowed to reset when hidden, as long as the picked coordinate and drawn
  geometry (if any) are hoisted out of the modal's local `useState` into a store that outlives the
  component, so re-opening the pane restores the last-drawn geometry even if the modal itself was
  unmounted.

(a) requires: hoisting `InterventionSubmitModal`'s local form state to a store (or lifting it to
the new workspace shell), keeping both embedded maps' MapLibre instances alive rather than
mounting/unmounting on switch (non-trivial: `InterventionSubmitModal.tsx:128-145` currently creates
and destroys a MapLibre instance per open), and changing `regional-intelligence-store`'s
`closePanel`/`openPanel` to stop clearing state when the panel is merely hidden versus genuinely
closed by the user (introducing a "hide" vs "close" distinction that does not exist today).

(b) is a smaller, additive change: no new "hidden but mounted" lifecycle, just moving already-local
state one layer up and no longer auto-clearing it on the transitions that currently clear it.

**This spec does not pick between (a) and (b).** FR-1/FR-2 below are written to be satisfiable
either way; the plan's Phase 1 includes a decision checkpoint naming this question explicitly.

### OQ-2: Docked/tabbed extension of the existing LayerPanel, or a new dedicated sidebar surface?

The "One manager, no floating surfaces" convention (`src/components/map/AGENTS.md`) exists because
every past floating control surface on this map turned into a second, out-of-sync writer over
state the dock already owned. But `src/components/panels/AGENTS.md` **already and explicitly**
excludes `RegionalIntelligencePanel` and the submit modals from that convention, on the stated
reasoning that they are "mounted by routes or by other components," not because they were evaluated
against the convention and found exempt. Two live options, not silently pre-decided:

- **(a) Extend the dock**: fold the AI/intervention workspace into `LayerPanel.tsx` as new,
  switchable section(s) (a third top-level mode alongside — or replacing the placement of — the
  existing Search/View/layer-group sections), reusing `panel-scroll.ts`'s one-scroller contract and
  `panel-store`'s disclosure machinery. This directly extends the established precedent and keeps
  literally one control surface on the map. Costs: the dock is a fixed 304px-wide, left-edge column
  built for compact controls (search field, toggles, sliders); an AI chat transcript and an
  embedded drawing map are much heavier, taller content than anything currently in it, and the dock
  currently has no "mode" concept — it is one always-visible list of sections, not a switcher
  between mutually exclusive full-height views. `RegionalIntelligencePanel` is also anchored
  `right-0`, opposite the dock's `left-0`; extending the dock would mean relocating the AI
  panel's screen position, not just its component tree.
- **(b) New dedicated sidebar surface**: a right-edge (or otherwise separate) workspace shell,
  structurally similar to `LayerPanel` (one shell, one scroller, closable) but a **second** control
  surface, explicitly justified as a deliberate, documented exception the way the panels AGENTS.md
  already tolerates for `RegionalIntelligencePanel` today — just formalized into a real switchable
  workspace rather than an ad hoc `aside`. Costs: this is the thing the dock convention exists to
  prevent in general (a second surface that could drift out of sync), so the justification must be
  explicit and the new surface must own nothing the dock already owns (no layer toggles, no render
  mode) — it strictly adds AI/intervention-specific controls that have no dock equivalent today.

**Recommendation, not a silent default**: (b), justified narrowly. The dock convention's stated
purpose is preventing *two controls over the same piece of state* (a layer switch in two places, a
render-mode toggle in two places). An AI chat session and an intervention drawing form are not
layer/render-mode state — they have no existing dock representation to duplicate, and
`src/components/panels/AGENTS.md` already treats them as a standing exception rather than a
violation. Forcing them into the 304px dock would either shrink the chat/drawing experience to fit
existing dock chrome or require enlarging the dock's shape for just these two sections, which
risks becoming the same "control surface that stopped meaning one thing" defect the 2026-08-08/09
merges fixed, just moved one level up. But this is a recommendation for the user to confirm, not a
decision this spec makes unilaterally — if confirmed, the plan should also update
`src/components/panels/AGENTS.md`'s exception note to describe the *workspace* explicitly (a named,
scoped exception) rather than leaving the current one-line carve-out as the only documentation.

### OQ-3: Does visible LayerPanel satisfy "viewing the data layers," or does the workspace need its own layer-visibility affordances?

Is the existing `LayerPanel` dock, simply remaining visible/reachable alongside the new workspace
surface, sufficient for "viewing the data layers... while switching between AI session and
intervention proposals" — or does the workspace itself need dedicated layer-visibility controls
(e.g., a compact toggle strip, or auto-opening relevant layers when an AI analysis references
them)?

**Recommendation, not a silent default**: start with "LayerPanel remains visible and independently
operable" as sufficient, since (a) the dock already handles layer visibility for the whole map, and
duplicating any part of it inside the new workspace re-opens exactly the "one manager" defect class
described above, and (b) neither `RegionalIntelligencePanel` nor `InterventionSubmitModal` today
depend on layer visibility to function. The only requirement this spec adds is that the new
workspace surface's layout must not visually block or force-close `LayerPanel` while both are open
(both can coexist on screen: dock left-edge, workspace right-edge or elsewhere, per OQ-2's
recommendation) — this is a layout constraint, not a new control. If the user wants
analysis-driven layer highlighting (e.g., "this response cited soil-moisture data — increase that
layer's opacity or open its report") that is a larger, separate feature and should be scoped as a
follow-up track, not silently folded in here.

## Functional Requirements

### FR-1: One workspace shell hosting both sessions, switchable without data loss
- **Description**: Replace the two independently-mounted overlays (`AgentAnalysisPrompt`'s
  downstream `RegionalIntelligencePanel`, and `InterventionProposalModal`'s
  `InterventionSubmitModal`) with a single workspace shell component that can show either an "AI
  analysis" view or an "Intervention proposal" view, with an explicit switch control between them,
  while satisfying whichever answer OQ-1 resolves to.
- **Acceptance Criteria**:
  - The workspace has exactly one visible switch affordance (e.g., two tabs, or a segmented
    control) naming both modes; there is no way to have both views fully hidden while the
    workspace itself is open.
  - Switching from AI analysis to intervention proposal and back does not clear
    `useRegionalIntelligenceStore`'s `messages`/`conversationId`/`analysisEvidence` (verifies
    against whichever of OQ-1(a)/(b) is chosen — for (b) at minimum the store must not be reset by
    the switch itself, only by an explicit "New chat"/close action the user takes).
  - Switching from intervention proposal to AI analysis and back does not silently discard a
    drawn-but-unsubmitted geometry (verifies against OQ-1's resolution — for (b) at minimum the
    last drawn geometry and form field values must be restorable from a store when the pane is
    re-shown).
  - The existing "Send for analysis" and "Propose intervention here" buttons in `AgentInteraction`
    open the workspace directly into the corresponding mode, seeded with the clicked coordinate —
    unchanged external behavior from the user's point of view, reusing (not duplicating) the
    coordinate-seeding logic already landed in `intervention_drawing_visibility_20260912`.
  - Opening the workspace from a *new* map click while one mode already has an active, unsubmitted
    session must not silently discard that session — clicking "Send for analysis" while an
    unsubmitted intervention draft exists must not delete the draft without at least a visible
    affordance to return to it (exact UX — warn/confirm vs. multi-slot support — is a planning
    decision, but silent data loss on a new click is explicitly disallowed).
- **Priority**: P0

### FR-2: Workspace open/close lifecycle is explicit and distinct from mode-switch
- **Description**: There is a clear, single way to close the entire workspace (ending both
  sessions, if the user confirms discarding unsubmitted work), separate from switching between its
  two modes.
- **Acceptance Criteria**:
  - A close action exists (keeping today's Escape-to-close and explicit close-button conventions
    from both `AgentInteraction` and `InterventionSubmitModal`) that is unambiguous about which
    state it clears — this is the "hide vs. close" distinction OQ-1(a) requires if adopted, or
    simply "close" if OQ-1(b) is adopted.
  - Closing while an intervention geometry is drawn but not submitted, or while an AI response is
    mid-stream, prompts for confirmation or clearly states what will be lost, rather than silently
    discarding it (mirrors the existing `cancelAnalysis` pattern in
    `regional-intelligence-store.ts:151-175`, which already handles the mid-stream case for AI but
    has no equivalent for an in-progress draw).
  - Closing the workspace does not affect `LayerPanel`'s open/closed state or vice versa — the two
    surfaces are independently controlled per OQ-3.
- **Priority**: P0

### FR-3: The intervention drawing map keeps working the same way inside the workspace
- **Description**: `InterventionDrawControl` (terra-draw) and its embedded MapLibre instance
  continue to function identically inside the new workspace shell — this track changes the shell
  around them, not their drawing behavior, validation, or submission contract.
- **Acceptance Criteria**:
  - Point/polygon draw, undo, clear, and the existing closed-polygon/minimum-vertex validation
    (`InterventionSubmitModal.tsx:45-66`) behave identically to today.
  - `submitIntervention`'s call shape, success/error handling, and the
    `invalidateInterventionDraftsOverlay` call on success (`MapView.tsx:62-70`) are unchanged.
  - If OQ-1 resolves to (a) (true keep-alive), the embedded MapLibre draw-map instance must survive
    a mode switch without being destroyed and recreated — verify this explicitly, since
    `InterventionSubmitModal.tsx:128-145` currently creates/destroys that instance on every
    mount/unmount and naively keeping the component mounted-but-hidden is not sufficient if some
    other part of the new shell still causes it to remount.
- **Priority**: P0

### FR-4: The AI analysis pane keeps working the same way inside the workspace
- **Description**: `RegionalIntelligencePanel`'s chat, evidence disclosure, export, history links,
  and streaming/cancel/retry behavior continue to function identically inside the new workspace
  shell.
- **Acceptance Criteria**:
  - All existing behaviors in `RegionalIntelligencePanel.tsx` (send, cancel, retry, new chat,
    evidence details, exports, feedback, freshness footer) work unchanged inside the new host.
  - Streaming responses continue to render live while the AI pane is the visible mode; if OQ-1(a)
    is chosen, streaming must also continue uninterrupted while the AI pane is hidden behind the
    intervention pane (no abort-on-hide), and the panel must correctly reflect the in-progress
    state when switched back to.
- **Priority**: P0

### FR-5: LayerPanel remains independently visible and operable
- **Description**: Per OQ-3's default, the existing map data-layer dock (`LayerPanel`) continues to
  open, close, and control layer visibility exactly as it does today, unaffected by the new
  workspace's presence or state.
- **Acceptance Criteria**:
  - The new workspace's layout does not overlap or force-close `LayerPanel` when both are open
    (visually or via any programmatic close call) — this constrains where the workspace is placed
    on screen but does not require new integration between the two components.
  - `LayerPanel`'s own state (`panel-store.layerPanelOpen`, `expandedDetails`) is untouched by
    opening, closing, or switching modes in the new workspace.
- **Priority**: P0

## Non-Functional Requirements

### NFR-1: Performance
- Whichever mode is not currently visible must not issue new network requests it would not
  otherwise issue while genuinely hidden (e.g., a hidden-but-mounted intervention draw map must not
  re-fetch tiles it already has; a hidden AI pane must not re-issue a completed request). This
  mirrors the existing "mounted means active" query-gating discipline documented in
  `src/components/panels/AGENTS.md` §"These are dock sections, not panels" — but the workspace is
  explicitly NOT a dock section (per OQ-2's recommendation), so this NFR states the *analogous*
  requirement for the new surface rather than claiming that section's rule applies verbatim.
- If OQ-1(a) (true keep-alive) is chosen, mounting two MapLibre instances (the intervention draw
  map) plus the main map simultaneously must not measurably degrade main-map frame rate; validate
  with the existing WebGL-context-loss handling in `MapView.tsx:277-304` still functioning
  correctly with an additional live map instance in the DOM.

### NFR-2: Security / Privacy
- No change to `AgentInteraction`'s approximate-vs-exact location precision confirmation step
  (`src/components/map/AGENTS.md` §"Location selection is a privacy boundary") — the workspace must
  still require the existing explicit precision choice before an analysis request is sent, and
  before an intervention's coordinates are set from a click.
- No change to `listMySubmissions`/`listProposed` authentication requirements or to the AI
  analysis's informational-only guarantee (cannot take external actions) — this track is UI
  composition only, not a new capability surface.

## User Stories

**US-1**: As a user analyzing a location with the AI, I want to switch over to drawing an
intervention proposal for that same spot without losing my conversation, so I can act on the
analysis while it's still fresh without re-reading it from scratch after closing and reopening.
- Given an active AI analysis conversation is open in the workspace, When I switch to the
  intervention proposal mode and draw a geometry, Then my AI conversation is still present
  (per OQ-1's resolution) when I switch back to it.

**US-2**: As a user proposing an intervention, I want to check the AI's analysis of the same
location partway through drawing my proposal, so I can factor its risk assessment into where I draw
the boundary.
- Given I have started drawing a polygon in the intervention pane, When I switch to the AI analysis
  mode and ask a question, Then my in-progress drawing is not discarded (per OQ-1's resolution) and
  is still present when I switch back.

**US-3**: As a user working in the workspace, I want the map's data layers to stay visible and
controllable, so I can reference layer data (e.g. soil moisture, fire perimeters) while chatting
with the AI or drawing a proposal.
- Given the workspace is open in either mode, When I open the `LayerPanel` dock and toggle a layer,
  Then the layer visibility changes on the map and the workspace's own state is unaffected.

## Technical Considerations

- **State ownership**: whichever of OQ-1(a)/(b) is chosen, `InterventionSubmitModal`'s currently
  component-local form state (`category`, `interventionType`, `name`, `description`, `geometry`,
  `geometryError`) needs at minimum a store to survive a re-render/re-show cycle; today nothing
  outside the component holds it. A new store (mirroring `regional-intelligence-store.ts`'s shape)
  or an extension of an existing one is planning work, not pre-decided here.
- **Embedded map lifecycle**: `InterventionSubmitModal.tsx:128-145` mounts and tears down its own
  MapLibre instance keyed to component mount. If OQ-1(a) is chosen, this needs to become
  survive-across-hide rather than mount-tied — likely by moving the embedded map's container/effect
  up into the persistent workspace shell rather than the mode-specific child, mirroring how
  `MapView` itself keeps its main map instance alive across UI state changes.
  `InterventionDrawControl.tsx:47` is keyed on `[map]` identity and will re-run correctly as long as
  the `map` instance itself does not change identity across a hide/show cycle.
  RegionalIntelligencePanel currently reads `isOpen` (`isAIOpen` in `MapView.tsx:148`) to decide
  whether to mount at all; a keep-alive design would need it to instead accept a `visible`/`hidden`
  distinction, or move that gating into the new shell.
  - **Store lifecycle changes are scoped narrowly.** `openPanel`/`closePanel`/`setLocation` in
    `regional-intelligence-store.ts` currently conflate "the panel isn't shown right now" with "the
    user is done with this conversation." Any change here must add a new transition (e.g., a
    `hidePanel`/`showPanel` pair that leaves `messages`/`conversationId`/etc. untouched) rather than
    changing what `closePanel`'s existing callers get when they call it — `closePanel` is called
    today from an explicit user "X" click and must keep clearing state for that action.
- **Placement**: per OQ-2's recommendation, the workspace is a new, separate surface (not folded
  into `LayerPanel`). Its screen position (right edge, matching `RegionalIntelligencePanel`'s
  current `right-0` anchor, versus somewhere else) and width are planning decisions; it must not
  collide with `ManagerRail`'s bottom-left position or `LayerPanel`'s left edge.
- **Existing AGENTS.md exception**: `src/components/panels/AGENTS.md`'s current one-line carve-out
  ("`RegionalIntelligencePanel`... and the two submit modals are not manager sections; they are
  mounted by routes or by other components") should be rewritten as part of this track to describe
  the resulting workspace explicitly, once OQ-2 is resolved — leaving the old sentence in place
  after this track ships would misdescribe the new architecture.

## Out of Scope

- Any change to what the AI analysis computes, what evidence it cites, or how it is validated
  server-side (`useRegionalIntelligence`, `regional-intelligence.ts`, the tRPC analysis router).
- Any change to intervention geometry validation, area caps, or the land/air category system —
  all owned by `intervention_drawing_visibility_20260912` and left as-is.
- Coordinate-picking / seeding behavior for either action — already solved by the prior track's
  "Propose intervention here" button; this track only changes what happens after either button is
  pressed.
- Multi-session support beyond two concurrent modes (e.g., multiple simultaneous AI conversations,
  or multiple simultaneous in-progress intervention drafts at different coordinates) — the
  "new click while a session is active" handling in FR-1 is about not silently destroying the one
  existing session in each mode, not about supporting arbitrarily many.
- Any analysis-driven layer highlighting/auto-opening (see OQ-3) — flagged as a candidate follow-up
  track, not built here.
- Mobile-specific workspace layout beyond reusing the existing full-screen-overlay pattern already
  established for `LayerPanel` on `max-sm` (see `src/components/map/AGENTS.md`
  "Mobile is the same tree in a different box") — the plan should note whether the new workspace
  needs the same treatment, but a full mobile redesign is not this track's scope unless planning
  determines it is trivially required.
