# Conductor Track Execution Plan: Community & Intervention Lifecycle Engine

## Phase 1: Database & tRPC Proposal Router Integration
- [x] Update `src/lib/server/trpc/routers/interventions.ts` and `community.ts`:
  - `proposeIntervention`: Accepts coordinate bbox, strategy type (Regenerative Ag, Agroforestry, Biochar, Moisture Control), target practice, and linked ML cell ID.
  - `castModerationVote`: Expert voting procedure checking user role (`admin`/`expert`).
  - `transitionLifecycleState`: Moves intervention state from `proposed` → `approved` → `active` → `monitored`.

## Phase 2: Expert Moderation Dashboard (`/moderation`)
- [x] Build `src/app/moderation/page.tsx` & `src/components/panels/ModerationPanel.tsx`:
  - Table of pending community intervention proposals.
  - Causal evidence scorecard displaying ML strategy benefit estimate ($\hat{\tau}$), SoilGrids/drought baseline, and risk factors.
  - One-click approve/reject/request-revision buttons for verified experts.

## Phase 3: Intervention Feed & Map Overlay
- [x] Update `src/app/feed/InterventionFeed.tsx`:
  - Display active community interventions with live telemetry chips (e.g. "+14% Soil Moisture since Biochar application").
- [x] Connect MapLibre `InterventionLayer.tsx` to render active community project polygons/points on the map.

## Phase 4: Verification & Automated Tests
- [x] Run `npx vitest run src/__tests__/api/interventions-trpc.test.ts`: Verify proposal submission, expert role authorization, and state transitions.
