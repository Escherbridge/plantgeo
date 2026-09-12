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
| Canonical integration | `01a0919f-40dd-7ae2-a5a3-cd8cf7e13489` — Integrate PlantGeo track candidates | `C:/Users/atooz/.codex/worktrees/4ccb/plantgeo`, `codex/active-track-integration-20260911` | Frozen six-lane source `89e8494422b8232c8f16dbffdcf2321c7ea17bc8`, tree `777fe20689dd68c337677cc49e3fd4e63bfd4bcc`; evidence checkpoint `9b46e86155a65e56a5186fcd087fea944bdf5ba1` | Active. All 16 first-candidate local gates passed. Botanical intake and QA documentation commit `820753d` are being composed before one new combined sweep. Owns shared-file merge order and final candidate. |
| Botanical species-profile author | `01a092bd-c71d-7bb3-bc46-e0dac684f751` — Build botanical species profiles | `C:/Users/atooz/.codex/worktrees/0caf/plantgeo`, `codex/botanical-species-profile-lookup` | Commit `edc6afdeb23f339b40049ec0828551c2fe1a4d45`, tree `01ad55b6220a13604e8fbf8a4ceda773e35d5658`; base `bc7b5e1ff5dbb6eac929d5db927acd6b7ff2ea4a` | Author complete and independently reviewed; pending canonical composition and post-integration QA. Keep task and worktree until exact integration is accepted. |
| Weather forecast author | `01a09323-6f59-7822-9e6f-146892776749` — Build PlantGeo weather forecast | `C:/Users/atooz/.codex/worktrees/32be/plantgeo`, `codex/weather-forecast-20260911` | Base `362422e3dffebb61a68fd4d7303234c3a14a43ed`, tree `bf9a79d6ec6f4fd9d7ec5cad8a0797010ef9a821`; active working tree, no handoff commit yet | Active. Independent forecast modules are author-frozen and under final checks. Shared map, reader, schedule, HTTP, agent and MCP registration waits for the canonical post-botanical transfer. Production publication and mounted browser evidence remain open. |
| Intervention boundary and publication author | `01a0932a-a08f-7712-b643-bfc67bc70d01` — Fix intervention boundaries and publishing | `C:/Users/atooz/.codex/worktrees/2f1d/plantgeo`, `codex/intervention-boundary-publication-20260911` | Base `89e8494422b8232c8f16dbffdcf2321c7ea17bc8`, tree `777fe20689dd68c337677cc49e3fd4e63bfd4bcc`; active working tree, no handoff commit yet | Active. Owns modal boundary authoring, canonical publication, cache refresh, error states and local mechanics QA. A two-line `MapView.tsx` publication-sync mount is the only approved shared map edit. The corrected full suite passed, including real local PostGIS; the independent review's stale-card concurrency fix and authenticated browser acceptance remain pending. |
| PNW land/contact planning author | `01a09326-7319-7930-bee3-ac91e6085776` — Plan PNW land contact layers | Shared main checkout; no implementation branch | Nine planning-only files under `pnw_land_context_reference_plane_20260911` and `pnw_land_contact_experience_20260911`; independent planning review and documentation sweep passed. | Author task complete. Parent registered both tracks as `planned`; no ingestion, outreach or implementation is authorized. Registration observed at `2026-09-12T01:48:27Z`. Retain the task until this registration commit and evidence are reconciled. |

## Authority and ownership constraints

- Railway, production databases, production object storage, deployments,
  external messages and user contact remain outside these tasks.
- Weather and intervention authors must supply exact committed heads, trees,
  changed paths, base blob identities, receipts and independent verdicts before
  integration. Working-tree state is never a handoff.
- The integration task serializes shared map, reader, capability, HTTP, agent,
  MCP and Conductor changes. Documentation-only commit `820753d` must be
  reconciled into the final candidate, with runtime receipt reuse justified by
  an exact unchanged-runtime diff when appropriate.
- Local agent-created intervention submissions prove synthetic mechanics only.
  They cannot close the community track's human-contributor requirement.
- The botanical author task and any completed planning task remain unarchived
  until their commits or tracked evidence are retained and independently
  reconciled. Blocked production/source gates keep their owning tracks open.

## Next ledger update

Replace active-working-tree entries with immutable author commits and trees;
record integration dispositions and receipt hashes; bind the independent
browser verifier before Q1 evidence collection. Superseded identities remain
dated evidence and are not relabelled as the final candidate.
