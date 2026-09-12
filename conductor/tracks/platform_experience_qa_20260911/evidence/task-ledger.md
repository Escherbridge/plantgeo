---
type: evidence
recorded_on: 2026-09-11
observed_at: 2026-09-12T05:08:41Z
---

# Task and candidate ledger

This ledger binds the current QA and authoring tasks before their handoffs are
consumed. `Active working tree` is not an immutable candidate. A task can move
to `integrated` only after its committed tree is reconciled into the canonical
candidate; it can close only after the required independent verdict.

## Bound tasks

| Role | Codex task | Checkout and branch | Bound source or candidate | Current disposition |
| --- | --- | --- | --- | --- |
| QA and orchestration | `01a0904b-756b-7961-b991-cab666123be2` — PlantGeo QA and track orchestration | `C:/Users/atooz/Programming/plantgeo`, `main` | QA planning commit `820753d73543514f534c9c1386e1f84012152775`, tree `b3205373e373641059c3edf10ce01c7f5f61d994` | Active. Owns this ledger, requirement matrix, defect routing, shared Conductor registry/runbook and proof-gated task archival. Runtime integration is complete; populated-data, mobile, agent-parity and broader track verdicts remain open. |
| Canonical integration | `01a093c6-69e0-79d2-b1c7-bc5e907dfd1b` — Integrate weather and botanical candidates | `C:/Users/atooz/.codex/worktrees/40ae/plantgeo`, `codex/integrate-botanical-weather-evidence` | Final candidate `9284d52738dbb323bbcd1dfe8c22ab6290f2a4b1`, tree `a0797ce0b4a91b2ac051dfa423382fbf3db60104`; root `main` contains the identical candidate tree at `6d2ac6c` before the follow-up custody-ledger commit | Archived after serialized reconciliation, independent verification and root browser evidence intake. Candidate remains partial: populated data and narrow-mobile acceptance are open. No remote/data-writer action. |
| Botanical species-profile author | `01a092bd-c71d-7bb3-bc46-e0dac684f751` — Build botanical species profiles | Preserved implementation commit `edc6afdeb23f339b40049ec0828551c2fe1a4d45`, tree `01ad55b6220a13604e8fbf8a4ceda773e35d5658`; predecessor worktree archived after Git metadata disappeared | Local implementation complete and independently reviewed; task archived after custody was retained. The separate census owner `01a093be-a6b1-7670-bf41-5e999cb0a2c9` produced and integrated receipt commit `557c4c0` and is archived; Railway/production census is blocked before DB access and remains open. |
| Botanical profile and agent-wiring continuation | `01a093f2-7452-75d3-9fba-343d14758f71` — Resume botanical profile and agent wiring | `C:/Users/atooz/.codex/worktrees/a93f/plantgeo`, active continuation checkout | Preserved implementation `edc6afdeb23f339b40049ec0828551c2fe1a4d45` is under separation review; no new commit yet | Active. Safe scope is limited to read-only API/agent contracts with exact identity, per-field provenance/review state and explicit missingness. Railway/production DB access is conclusively blocked; no `pgt` or local database may substitute, and WCVP/local fixture publication assumptions must remain out of an admitted release. |
| Historical weather visual author | `01a093b8-e328-7b52-ae2a-a06257d6aed5` — Implement historical weather visual… | `C:/Users/atooz/.codex/worktrees/15a4/plantgeo`, isolated author checkout | Immutable owner commit `8e53b416ce5dc5287295a707dae2f9c121e1e993`, tree `23ba93ff46458c5d8bd399072ce667d152d11235`; integrated into candidate `9284d527` | Archived after independent review, focused checks, integration and root desktop unavailable-state evidence. Live labels/hover/day transitions remain unexercised because the governed data service is absent. |
| Superseded weather forecast author | `01a09323-6f59-7822-9e6f-146892776749` — Build PlantGeo weather forecast | Archived `C:/Users/atooz/.codex/worktrees/32be/plantgeo` after clean reversion chain | Reverted forecast commits retained in history and superseded shared diff retained in named stash | Archived as superseded custody. No stash was applied or deleted; the historical visual task owns the current repair. |
| Intervention boundary and publication author | `01a0932a-a08f-7712-b643-bfc67bc70d01` — Fix intervention boundaries and publishing | Archived after evidence-only reconciliation | Accepted runtime source `2fc6b30ac1b024e1c955dbf95552495608608a96`; evidence-only receipt `d9e4bd21f46d5d5caf8a0a90a4f39f4f222f1241` | Archived implementation task. Broader intervention boundary, production publication and human-contributor gates remain open in Conductor. |
| PNW land/contact planning author | `01a09326-7319-7930-bee3-ac91e6085776` — Plan PNW land contact layers | Shared main checkout; no implementation branch | Nine planning-only files under `pnw_land_context_reference_plane_20260911` and `pnw_land_contact_experience_20260911`; independent planning review and documentation sweep passed. | Archived after registration and evidence were reconciled. Parent tracks remain `planned`; no ingestion, outreach or implementation is authorized. Registration observed at `2026-09-12T01:48:27Z`. |
| PNW Herbaria source-admission continuation | `01a093f3-0097-7971-b2fb-179de546d643` — Resume PNW Herbaria source admission | `C:/Users/atooz/.codex/worktrees/bfe6/plantgeo`, detached evidence checkout | Metadata refresh commit `47715dd984a4c15afe174bc6e6d9bef8ce1ed704`, tree `888d404c1b404f6af641ab469810f69cf068bb9b`; integrated into root `57ef4fc` | Archived after metadata-only evidence was independently verified and integrated. Seven bounded metadata GETs are retained with URL, timestamp, headers, byte count and SHA-256 receipts; UBC v16.43 EML is byte-identical to the prior capture. WTU still lacks a standalone release-bound EML/field map and UBC lacks an applicable coordinate-withholding statement, so archive acquisition and admission remain blocked. No archive, image, specimen, RTF, script, iframe or media resource was acquired. |
| Multiscale visual-layer continuation | `01a093f3-575a-7e02-bc0d-bc2c7ce7d562` — Finish multiscale visual layer updates | `C:/Users/atooz/.codex/worktrees/4a6e/plantgeo`, detached clean checkout | Reviewed commit `2b29d9f1ad475358e96fc6c0e48aabd9a3e7d29b`, tree `878a071801f472a9cbcd3cb02eb06c2845d5d3c4`; integrated into root `ac4ce70` | Archived after independent review and local integration. Climate and soil scalar surfaces now show unit-bearing numeric labels with `avg` qualifiers for aggregated features, preserve missingness/legends/opacity/style reloads, and leave climate isobands unlabeled. Twenty-six synthetic desktop/mobile captures support the mechanics; live selected-day, dense-basemap, hover, production-performance and full mobile gates remain open. |

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
- The completed botanical implementation and census tasks are archived after
  their commits and receipts were retained; blocked production/source gates keep
  their owning Conductor tracks open. The weather visual, integration, PNW
  planning and intervention author tasks are likewise archived with evidence
  retained.

## Next ledger update

The weather owner, botanical census, integration, PNW planning, PNW Herbaria
refresh and multiscale visual continuation entries are bound to immutable
evidence and archived custody. The botanical profile/agent-wiring continuation
remains active. The root browser receipt is recorded at `e5eaab6`, weather and
botanical runtime integration at `9284d527`, Herbaria evidence at `57ef4fc`, and
the scalar-label integration at `ac4ce70`; keep the platform QA task active for
the remaining populated-data, mobile, agent-parity and broader track gates.
Superseded identities remain dated evidence and are not relabeled as the final
candidate.
