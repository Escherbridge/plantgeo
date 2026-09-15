---
type: Implementation Plan
title: AI intervention workspace
tags: [ai_intervention_workspace_20260913]
resource: ./spec.md
---

September 14 session 2: proposal-first AI entry now uses explicit shared precision/consent
controls, preserving draft/map state. The integrated frontend suite passed 190 files / 2,502
tests, with type/boundary checks and zero-error lint. See the [session evidence](../platform_experience_qa_20260911/evidence/runbook-session2-20260914.md).
Browser drawing, mobile, streaming and navigation recovery remain open; historical checkboxes
below retain their original evidence scope.

# Implementation Plan: AI intervention workspace

## September 14 navigation recovery

The [third QA session](../platform_experience_qa_20260911/evidence/runbook-session3-20260914.md)
restores saved Point/Polygon geometry after non-explicit unmount and preserves original draft
coordinates on reopen. Map attachment waits for current style readiness on recurring events;
failed restoration retains the draft and reports an error. Candidate 6 passed 191 frontend files /
2,509 tests plus type/boundary checks and zero-error lint. Actual browser drawing/recovery and
mobile/streaming behavior remain open; this checkpoint does not close the track.

## September 14 lifecycle QA correction

The [long-horizon QA session](../platform_experience_qa_20260911/evidence/runbook-session-20260914.md)
reconciles existing implementation and repairs requested-mode changes, preservation of form work,
confirmed close/Escape behavior, the hidden standalone panel's competing Escape listener, and
drawing-map resize on reveal. Caption and relocation guidance retain the actual draft coordinates.
The source candidate remains uncommitted; automated evidence does not close browser acceptance.
Proposal-first AI consent entry, non-explicit navigation persistence, mobile and real drawing/stream
journeys remain open. Historical phase checkboxes below retain their dated evidence.

## Overview

Four phases. Phase 1 is a decision checkpoint (OQ-1/OQ-2/OQ-3 must be answered by the user before
Phase 2 starts — do not guess). Phase 2 builds the shared state layer both panes need. Phase 3
builds the workspace shell and rewires `MapView`. Phase 4 ports each existing pane's content into
the shell without behavior regressions, then a full verification pass.

This order exists because the state-lifecycle decision (OQ-1) determines whether Phase 2 is a
small additive change (OQ-1b) or a materially larger one (OQ-1a, keep-alive embedded map). Starting
component work before that answer risks a rewrite.

## Phase 1: Decision checkpoint (no code) — RESOLVED 2026-09-13

- OQ-1 → (a) true keep-alive. Phase 2 and Phase 4 below carry the resulting sub-tasks.
- OQ-2 → (b) new dedicated surface (not a dock extension).
- OQ-3 → dedicated layer-visibility controls inside the workspace, reading/writing the existing
  `map-store`/`layer-registry` state (not a second store). New task added to Phase 3.

[checkpoint marker: decisions recorded in spec.md, plan updated below]

## Phase 2: Shared state layer

Goal: give the intervention-proposal pane a store to persist its form/geometry state the way the
AI pane's `regional-intelligence-store.ts` already does for its conversation, and add the
hide-vs-close distinction to `regional-intelligence-store.ts` if OQ-1(a) was chosen.

Tasks:
- [x] Task (TDD): Write a failing test asserting a new `intervention-draft-store.ts` (or
      equivalently named) holds `category`, `interventionType`, `name`, `description`, `geometry`,
      `geometryError`, and the seeded `lat`/`lon`, survives being read after a simulated
      unmount/remount cycle (i.e., store state persists independent of any component), and is
      cleared only by an explicit `clearDraft()` action — never implicitly on mount. Implement the
      store to pass it; refactor for shape parity with `regional-intelligence-store.ts`'s existing
      conventions (devtools middleware, explicit action names).
- [x] Task (TDD): Write a failing test for `InterventionSubmitModal` (or its future replacement)
      reading its form fields from the new store instead of local `useState`, verifying that values
      set before a simulated unmount are present after remount. Implement by replacing the
      `useState` calls with store selectors/actions; keep the component's exported props/behavior
      contract (`lat`, `lon`, `onClose`, `onSuccess`) unchanged so `MapView`'s existing call site
      still compiles unmodified at this point in the sequence.
- [x] Task (OQ-1(a), required): Write a failing test asserting
      `regional-intelligence-store.ts` exposes a `hidePanel`/`showPanel` (or equivalently named)
      transition pair that leaves `messages`/`conversationId`/`analysisEvidence`/`isLoading`
      untouched, distinct from the existing `closePanel`, which must continue to clear state exactly
      as it does today (verify existing `closePanel` tests still pass unmodified — this is an
      additive change, not a rename). Implement the new actions.
- [x] Verification: Run the store test suite for both stores; confirm `closePanel`'s existing
      callers (the AI panel's explicit "X" button) are unaffected by the new actions — grep for
      every existing `closePanel()` call site and confirm none needed to change. [checkpoint
      marker]

## Phase 3: Workspace shell and MapView rewiring

Goal: build the switchable shell component and replace `MapView`'s two independent overlay mounts
with one workspace mount, without yet touching either pane's internal content.

Tasks:
- [x] Task (TDD): Write a failing test for a new `AiInterventionWorkspace` shell component (name
      TBD — per OQ-2's recommendation, a new file, not a `LayerPanel` section) that: renders a
      two-way switch control (tabs/segmented control) naming "AI analysis" and "Propose
      intervention"; renders exactly one mode's content area as the active/visible one; and calls a
      distinct `onClose` prop only from its own explicit close action, never from the mode-switch
      control. Implement a minimal shell (no real pane content yet — swap in placeholder children)
      to pass the test.
- [x] Task (TDD): Write a failing test asserting the shell's close action, when a draft geometry
      exists in the Phase 2 store or an AI response is mid-stream, surfaces a confirmation/warning
      before invoking `onClose` (mirrors `cancelAnalysis`'s existing mid-stream handling; extends
      the same pattern to the draft-geometry case, which today has no equivalent). Implement.
- [x] Task (TDD): Write a failing test for `MapView.tsx` asserting that clicking "Send for
      analysis" or "Propose intervention here" in `AgentInteraction` opens the new shell in the
      corresponding mode (replacing today's `agentCoords`/`interventionCoords`-driven separate
      mounts), and that the existing coordinate-seeding behavior (exact coordinate passed through
      to whichever pane is selected) is unchanged. Implement by replacing
      `AgentAnalysisPrompt`/`InterventionProposalModal`'s separate conditional renders with the new
      shell, keeping `handleCloseAgentInteraction`/`handleProposeIntervention`/
      `handleCloseInterventionModal`'s external behavior equivalent (rename/consolidate as needed,
      but preserve the render-count contract `map-view-render-count.test.tsx` pins — read that test
      before changing `MapView`'s hook shape).
- [x] Task (TDD): Write a failing test asserting that opening the shell via a new map click while
      one mode already holds an active, unsubmitted session does not silently clear that session
      (per FR-1's last acceptance criterion) — assert the prior session's store state is still
      present after the new click, whatever the chosen UX (warning, or simply not auto-clearing).
      Implement the guard.
- [x] Task (OQ-3, TDD): Write a failing test for a new compact layer-visibility toggle strip
      rendered inside the workspace shell (either mode, or a persistent header — implementer's
      choice, document it), that reads/writes the *same* `map-store` layer-toggle state
      `LayerPanel`/`LayerRow` already own (no new store, no shadow state) — assert toggling a layer
      from the workspace strip updates the same store field `LayerRow` reads, and vice versa.
      Implement as a thin consumer of the existing store/registry, reusing `LayerRow`'s toggle
      logic/icons where practical rather than re-deriving it.
- [x] Verification (automated part done 2026-09-13; manual click-through still owed): Run `map-view-render-count.test.tsx` and the new shell/MapView tests together;
      manually click through both "Send for analysis" and "Propose intervention here" from a fresh
      map click and confirm the shell opens in the right mode with the right coordinate, and that
      the new layer-toggle strip stays in sync with the main `LayerPanel`. [checkpoint marker]

## Phase 4: Port real pane content, keep-alive wiring, and full sweep

Goal: replace the shell's placeholder children with the real `RegionalIntelligencePanel` and
`InterventionSubmitModal` content, wire whichever OQ-1 lifecycle was chosen, and verify no
regression against every existing behavior named in FR-3/FR-4.

As built (2026-09-13):
- AI slot renders `RegionalIntelligencePanel embedded` (new `embedded` prop: no absolute anchor,
  no second header, no Escape binding, ignores `isVisible`). The STANDALONE copy in `MapView`
  stands down via `hidePanel()` on workspace mount and returns on unmount — visibility only, so
  the stream survives the handover. The shell's own close calls `closePanel()` before `onClose`.
- Intervention slot renders a NEW `src/components/panels/InterventionProposalForm.tsx`, backed by
  `intervention-draft-store.ts`. The drawing MapLibre instance is created ONCE by the shell
  (`[]`-dep effect over a container the form renders), so it and terra-draw's session outlive
  every mode switch. `clearDraft()` is called in exactly one place: the mutation's `onSuccess`.
- `InterventionSubmitModal.tsx` is untouched in behaviour and still serves `CommunityDetails`'s
  "+Recommend" button with its own local state; the two forms now share labels, the category
  table and the geometry validator via `src/lib/environmental/intervention-form.ts`.

Tasks:
- [x] Task (2026-09-13): Port `RegionalIntelligencePanel`'s body into the shell's AI-mode slot. If OQ-1(a) was
      chosen, change its mount gating from `isAIOpen` (unmount-on-close) to the new
      `hidePanel`/`showPanel`-aware visibility from Phase 2, and verify with a test that a
      streaming response continues (state updates keep arriving) while the mode is switched away
      and back. If OQ-1(b), leave its existing `isOpen`-gated mount/unmount as-is; it already
      relies on the store for what needs to persist.
- [x] Task (2026-09-13): Port `InterventionSubmitModal`'s body (form + `InterventionDrawControl`) into the
      shell's intervention-mode slot, now reading from the Phase 2 draft store. If OQ-1(a) was
      chosen, move the embedded MapLibre instance's mount effect (`InterventionSubmitModal.tsx:
      128-145`, currently tied to component mount) up to the shell level so the map instance
      survives a mode switch; write a test asserting the MapLibre instance identity is unchanged
      across a switch-away-and-back cycle, and that `InterventionDrawControl`'s terra-draw state
      (drawn geometry) is still present after the round trip. If OQ-1(b), leave the embedded map
      mount/unmount tied to the pane's own visibility, relying on the Phase 2 store to restore the
      last-drawn geometry's *value* (not the live terra-draw session) on re-show — note in a code
      comment that terra-draw's own interactive undo history does not survive this path, only the
      final geometry.
- [x] Task (pulled forward into Phase 3, 2026-09-13): Update `src/components/panels/AGENTS.md`'s "These are dock sections, not panels"
      carve-out to describe the new workspace explicitly by name (per spec's Technical
      Considerations note), replacing the now-stale one-line exception for
      `RegionalIntelligencePanel`/"the two submit modals."
- [x] Task (2026-09-13): Confirm `LayerPanel` layout non-interference (FR-5) — manually and via a layout/DOM
      test if one already exists for `LayerPanel`'s positioning, verify the new workspace's anchor
      position does not overlap `LayerPanel`'s left-edge dock or `ManagerRail`'s bottom-left
      collapsed state at common viewport widths, including `max-sm`.
- [x] Verification (automated sweep done 2026-09-13; manual FR-1..FR-5 click-through against the
      running app still owed, per "Never run PlantGeo locally" that is an owner-side check):
      One full sweep per `conductor/workflow.md` — run the full affected-boundary
      test suite (component tests for `MapView`, the new shell, both stores, `AgentInteraction`;
      `map-view-render-count.test.tsx` explicitly), typecheck, and lint once at the end covering
      every file touched across all four phases (per the "one sweep" convention — do not re-run
      tests after each individual task). Manually re-verify every acceptance criterion in FR-1
      through FR-5 against the running app. Record which OQ-1/OQ-2/OQ-3 answers were implemented in
      the track's retrospective. [checkpoint marker]
