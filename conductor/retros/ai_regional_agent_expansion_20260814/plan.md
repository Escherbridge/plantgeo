---
type: track-plan
---

# Conductor Track Execution Plan: AI Regional Intelligence Agent ML Expansion

## Fabrication audit, 2026-08-14

A same-day audit found this plan checked 4/4 while three of its four deliverables were never
implemented: `regional-context.ts` had not been touched since 2026-08-09 (`soilProperties` and
`mtbsPerimeters` hardcoded `null` despite both having a real server-side read path;
`strategyRecommendations` was the pre-existing heuristic `StrategyScore` shape, not an ML
matview query; no community-proposal read existed at all); `ai-prompt.ts` defined
`GENERATE_REMEDIATION_REPORT_TOOL` but never put it in the `tools` array actually sent to
Anthropic, so it was unreachable; `RegionalIntelligencePanel.tsx` rendered three hard-coded
chips — "Regenerative Ag (+18% tau)", "Biochar Soil (+15% tau)", "Wildfire Buffer (+12% tau)" —
identically on every AI message, with invented causal numbers no model output ever produced; the
export button was real but JSON-only against a spec claiming JSON/Markdown. The contract test
covered only that `GENERATE_REMEDIATION_REPORT_TOOL` existed as a constant, not that it was
wired to anything.

**Governance re-scope (binding):** every τ̂ / causal-effect-bound sub-claim below is struck. The
repo forbids representing strategy-model output as a causal effect claim — the `20260725_0013`
causal plane is empty and deliberately blocked, and the evaluation model in production carries
`label_review_tier = agent_reviewed_pending_owner_signature` (agent-reviewed, not owner-signed;
see `services/agri-data-service/src/agri_data_service/method/AGENTS.md`). The
`geo.mv_strategy_recommendations_*` matviews this track can read (drizzle 0027, itself built by a
different, separately-audited track) DO carry a `causal_benefit_tau` column, but it is computed
over `agri.strategy_selection_candidate` rows assigned to **random** coordinates
(`37.5 + random() * 5.0`, see the migration) — not a located causal estimate of anything. This
implementation never reads that column. Every strategy entry crossing into the agent's context
or the UI carries a `claimTier` (`"heuristic_score"` | `"evaluation_only_model"`) and a relative
`score`, never a benefit percentage or an effect size, and the system prompt explicitly forbids
the model from stating one.

Resolution implemented 2026-08-14, re-ticked below against what the code now actually does.

## Phase 1: Context Aggregator & ML Strategy Query
- [x] Update `src/lib/server/services/regional-context.ts` — **re-verified 2026-08-14, real this
  time**:
  - ~~Fetch nearest ML strategy cell recommendation, top 3 practice options, and estimated
    benefit ($\hat{\tau}$).~~ Re-scoped per the governance note above: `resolveStrategyContext`
    returns the top 3 candidates from `geo.mv_strategy_recommendations_regional` when that
    matview exists (`to_regclass` guard, since drizzle 0027 is not applied in every environment),
    else falls back to the existing heuristic `StrategyScore` list — each entry tagged
    `claimTier`, carrying only a name and a relative `score`, never a $\hat{\tau}$ or a percent
    benefit.
  - Fetch active community proposals within 10km radius. Implemented: `readCommunityProposals`
    reads `geo.features`/`geo.layers` directly (mirrors `interventionsRouter.listProposed`'s
    status/consent gate — a server-side context assembler has no session to route a tRPC call
    through), bounded by `ST_DWithin` at 10km and a `LIMIT 5`.
  - Package baseline environmental observations (SoilGrids texture, ERA5 moisture, USGS gauge
    flow, FIRMS fire risk). `soilProperties` now wired to the live `getSoilProperties` (ISRIC)
    read that already backs the SoilDetails panel; `mtbsPerimeters` now wired to the live
    `getMTBSPerimeters` (ArcGIS-hosted MTBS) read. Streamflow, fire, and weather were already
    real as of the 2026-08-09 work this track's plan falsely claimed to extend.

## Phase 2: AI System Prompt & Tool Functions
- [x] Update `src/lib/server/services/ai-prompt.ts` — **re-verified 2026-08-14, real this time**:
  - ~~Add system prompt rules for interpreting causal effect bounds vs literature
    recommendations.~~ Re-scoped: the prompt now names `strategyContext`'s `claimTier` vocabulary,
    explicitly forbids stating a causal effect size or a benefit percentage for any strategy
    regardless of tier, and instructs the model to say plainly when a numeric benefit is not
    available rather than estimate one.
  - Add tool definition `generate_remediation_report` returning structured JSON for land practice
    recommendations. The constant existed since before this fix; it was never in the `tools`
    array `streamRegionalIntelligence` actually sends to Anthropic, so the model could never call
    it. Now sent alongside `remediation_report`, and the report-dispatch `find()` recognizes a
    `tool_use` under either name — see the contract test that exercises this against a mocked
    Anthropic client and would have failed on both counts against the pre-fix code.

## Phase 3: Regional Intelligence UI Panel Upgrade
- [x] Update `src/components/panels/RegionalIntelligencePanel.tsx` — **re-verified 2026-08-14,
  real this time**:
  - Render strategy breakdown chips directly inside AI response messages. The three hard-coded
    chips with invented causal percentages ("Regenerative Ag (+18% tau)", etc.) are deleted.
    Chips now render one per item in the model's own `remediation` array (the only strategy data
    this panel actually receives — the route does not stream `strategyContext` to the client),
    named from `humanize(item.strategy)` with an evidence-origin badge, and render nothing when
    `remediation` is empty.
  - Add "Export Remediation Report" button generating a clean JSON/Markdown summary. JSON export
    was already real; Markdown export (`reportToMarkdown`) is new, mirroring the same report
    structure.

## Phase 4: Automated Verification
- [x] Run `npx vitest run src/__tests__/api/regional-intelligence-contract.test.ts`: Verify AI
  context assembly and prompt structure. **Re-scoped 2026-08-14**: the pre-fix contract test only
  checked that `GENERATE_REMEDIATION_REPORT_TOOL` existed as a constant, not that anything used
  it — exactly the gap the audit named. It now also asserts the tool is present in the `tools`
  array actually sent to Anthropic and that a `generate_remediation_report` tool_use is dispatched
  as the report, against a mocked Anthropic client. Context-assembly coverage (community
  proposals, `claimTier`-tagged strategy entries, and a regex guard against `tau`/percent-benefit
  language) was added to `src/__tests__/services/regional-temporal-context.test.ts`, the existing
  unit suite for `assembleRegionalContext`, rather than duplicated into the contract test. A
  component test (`src/__tests__/components/RegionalIntelligencePanel.test.tsx`) was added new —
  none existed before — pinning that chips render from real data and render nothing when absent.
