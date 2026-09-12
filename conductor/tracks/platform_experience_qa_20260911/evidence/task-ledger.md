---
type: evidence
recorded_on: 2026-09-11
observed_at: 2026-09-12T01:39:43Z
---

# Task and candidate ledger

This ledger binds the current QA and authoring tasks before their handoffs are
consumed. `Active working tree` is not an immutable candidate. A task can move
to `integrated` only after its committed tree is reconciled into the canonical
candidate; it can close only after the required independent verdict.

## Bound tasks

| Role | Codex task | Checkout and branch | Bound source or candidate | Current disposition |
| --- | --- | --- | --- | --- |
| QA and orchestration | `01a0904b-756b-7961-b991-cab666123be2` — PlantGeo QA and track orchestration | `C:/Users/atooz/Programming/plantgeo`, `main` | QA planning commit `820753d73543514f534c9c1386e1f84012152775`, tree `b3205373e373641059c3edf10ce01c7f5f61d994` | Active. Owns this ledger, requirement matrix, defect routing, shared Conductor registry/runbook and proof-gated task archival. Runtime integration and final browser verdict remain open. |
| Canonical integration | `01a093c6-69e0-79d2-b1c7-bc5e907dfd1b` — Integrate weather and botanical candidates | `C:/Users/atooz/.codex/worktrees/40ae/plantgeo`, `codex/integrate-botanical-weather-evidence` | Starts from root QA commit `8c3ecf0`; botanical census handoff `557c4c06a58fec6876bae474f14c8070de929ae0`, tree `380e2274e6a5eaa4ff274cb9fed28b9bc6553192` is ready | Active. Botanical evidence is reconciled; waits for the weather visual owner’s reviewed immutable handoff before one final combined sweep. No remote/data-writer action. Owns serialized cherry-picks and final candidate. |
| Botanical species-profile author | `01a092bd-c71d-7bb3-bc46-e0dac684f751` — Build botanical species profiles | Preserved implementation commit `edc6afdeb23f339b40049ec0828551c2fe1a4d45`, tree `01ad55b6220a13604e8fbf8a4ceda773e35d5658`; predecessor worktree archived after Git metadata disappeared | Local implementation complete and independently reviewed; task archived after custody was retained. The separate census owner `01a093be-a6b1-7670-bf41-5e999cb0a2c9` produced receipt commit `557c4c0`; Railway/production census is blocked before DB access and remains open. |
| Historical weather visual author | `01a093b8-e328-7b52-ae2a-a06257d6aed5` — Implement historical weather visual… | `C:/Users/atooz/.codex/worktrees/15a4/plantgeo`, isolated detached author checkout | Dirty owner tree after focused implementation: historical report/cards, Climate placement, precipitation/temperature labels, legend/hover parity, selected-day stale-frame handling, and tests; exact commit/tree pending | Active. Integrated checks and desktop acceptance passed; independent review is correcting aggregate wording before commit. Live glyphs remain unexercised because the local data service is absent. |
| Superseded weather forecast author | `01a09323-6f59-7822-9e6f-146892776749` — Build PlantGeo weather forecast | Archived `C:/Users/atooz/.codex/worktrees/32be/plantgeo` after clean reversion chain | Reverted forecast commits retained in history and superseded shared diff retained in named stash | Archived as superseded custody. No stash was applied or deleted; the historical visual task owns the current repair. |
| Intervention boundary and publication author | `01a0932a-a08f-7712-b643-bfc67bc70d01` — Fix intervention boundaries and publishing | Archived after evidence-only reconciliation | Accepted runtime source `2fc6b30ac1b024e1c955dbf95552495608608a96`; evidence-only receipt `d9e4bd21f46d5d5caf8a0a90a4f39f4f222f1241` | Archived implementation task. Broader intervention boundary, production publication and human-contributor gates remain open in Conductor. |
| PNW land/contact planning author | `01a09326-7319-7930-bee3-ac91e6085776` — Plan PNW land contact layers | Shared main checkout; no implementation branch | Nine planning-only files under `pnw_land_context_reference_plane_20260911` and `pnw_land_contact_experience_20260911`; independent planning review and documentation sweep passed. | Author task complete. Parent registered both tracks as `planned`; no ingestion, outreach or implementation is authorized. Registration observed at `2026-09-12T01:48:27Z`. Retain the task until this registration commit and evidence are reconciled. |

## Authority and ownership constraints

- Railway, production databases, production object storage, deployments,
  external messages and user contact remain outside these tasks.
- The historical-weather visual author must supply an exact committed head,
  tree, changed paths, base blob identities, focused receipts and independent
  verdict before integration. Working-tree state is never a handoff. The
  superseded forecast task is retained only as archived custody evidence.
- The integration task serializes shared map, reader, capability, HTTP, agent,
  MCP and Conductor changes. Documentation-only commit `820753d` must be
  reconciled into the final candidate, with runtime receipt reuse justified by
  an exact unchanged-runtime diff when appropriate.
- Local agent-created intervention submissions prove synthetic mechanics only.
  They cannot close the community track's human-contributor requirement.
- The completed botanical implementation and census tasks may be archived after
  their commits and receipts are retained; blocked production/source gates keep
  their owning Conductor tracks open. The PNW planning task and intervention
  author task are archived with their evidence retained.

## Next ledger update

Replace the weather working-tree entry with its immutable author commit and
tree; record the botanical census integration disposition and receipt hash;
bind the independent browser verifier before Q1 evidence collection. Superseded
identities remain dated evidence and are not relabelled as the final candidate.
