---
type: evidence
recorded_on: 2026-09-15
status: recorded_checklist_rollup_not_acceptance
---

# Recorded runbook progress rollup

Captured 2026-09-15T00:06:24.810317+00:00; original audit author `/root/qa_inventory`. Root copied that ignored audit into this canonical evidence record. `/root/workspace_social_audit` corrected this attribution, separator encoding and artifact paths only; no metrics, checklist changes or status promotions.

**A defensible current plan-checkbox figure is 16.22% (54/333) for the expanded declared QA scope; this is not the percentage of the entire runbook completed.** The strict directly linked runbook scope is 15.14% (33/218). Formal whole-case acceptance remains separately reported by root as 0/220; bounded session passes are not whole-case promotions.

| Scope | Checked complete | In progress | Open | Total | Recorded completion |
| --- | ---: | ---: | ---: | ---: | ---: |
| Direct RUNBOOK owners plus linked freshness detail, current | 33 | 0 | 185 | 218 | 15.14% |
| Expanded declared workspace/social/land QA scope, current | 54 | 3 | 276 | 333 | 16.22% |
| Expanded including historical checkbox evidence (not current claim) | 95 | 3 | 288 | 386 | 24.61% |

## Scope and denominator

- Direct scope deduplicates the 12 owning track IDs explicitly linked anywhere in current `conductor/RUNBOOK.md` and reads each plan once. It additionally includes the exact freshness-resolution detail explicitly delegated by the environmental plan; the prose parent is not counted again.
- Expanded scope adds only five owners needed by the user's declared workspace/social/land QA scope: AI intervention workspace, unified intervention layer, public strategy requests, PNW land reference plane and PNW land contact experience. It does not silently include every unrelated planned fire-model/research/marketing track in the registry.
- Unit is a unique path:line checkbox deliverable. No phase heading, prose bullet, spec acceptance statement, case-matrix copy or evidence session is added as a second task. No effort weighting or credit for in-progress tasks.
- The only nested checkbox pair is public strategy requests plan lines154/161: separate backend regression and UI integration tasks. Both are independent deliverables and each appears once; there is no checkbox phase/rollup parent. Counting only syntactic leaves would wrongly delete the backend requirement.
- Literal markers determine status: `[x]` complete, `[~]` in progress, `[ ]` open. We did not infer completion from implementation presence, recent browser passes, or prose checkpoints.

## Historical exclusions and limitations

- Gapless plan explicitly calls its dated wave ledger historical (lines 24–25). All 38 wave items are excluded from the current tally; the three September12 reconciliation items remain. Future operational tasks must use the current source-direct recovery contract.
- Workspace plan explicitly preserves historical phase checkboxes (lines 11–12 and lifecycle intake). Its 15 completed Phases 2–4 items appear in the historical-inclusive metric only. Current lifecycle/browser/streaming obligations remain in prose and do not acquire a made-up denominator.
- Community plan explicitly identifies the August5 table and phase instructions as historical (lines 9–24), including subsequently shipped phases 2–4. None becomes a current checkbox or revived implementation task.
- In total 53 historical checkboxes are excluded: 41 complete, 12 open. Age alone never excludes an item; other active plan requirements remain even when originally dated earlier.
- Environmental plan has no checkboxes; its delegated freshness detail supplies29 but does not fully enumerate every prose repository/serving obligation. Community current moderation/owner gates have no checklist denominator. Those gaps prevent an honest full-runbook completion percentage.
- Some checked workspace verification tasks explicitly say the automated half passed while manual work remains. Historical-inclusive24.61% must not be presented as present acceptance. Conversely, public-request and drawing plans still show unchecked implementation tasks despite newer code/QA; this conservative literal tally cannot certify actual remaining engineering effort.
- The 333-item count mixes design, implementation and verification deliverables of unequal size. Calling16.22% actual code completion, release readiness, or percent of work/time finished would be inaccurate. A verified effort estimate requires a separately reviewed per-task reconciliation and sizing; this audit does not promote tasks.

## Per-plan current rows

| Plan | Current complete | In progress | Open | Current total | Historical items excluded |
| --- | ---: | ---: | ---: | ---: | ---: |
| `conductor/tracks/ai_intervention_workspace_20260913/plan.md` | 0 | 0 | 0 | 0 | 15 |
| `conductor/tracks/botanical_occurrence_experience_20260911/plan.md` | 0 | 0 | 13 | 13 | 0 |
| `conductor/tracks/botanical_species_profile_lookup_20260911/plan.md` | 6 | 0 | 23 | 29 | 0 |
| `conductor/tracks/community_engagement_completion_20260805/plan.md` | 0 | 0 | 0 | 0 | 0 |
| `conductor/tracks/environmental_parquet_serving_20260912/plan.md` | 0 | 0 | 0 | 0 | 0 |
| `conductor/tracks/gapless_parquet_publication_20260901/plan.md` | 1 | 0 | 2 | 3 | 38 |
| `conductor/tracks/intervention_drawing_visibility_20260912/plan.md` | 0 | 0 | 24 | 24 | 0 |
| `conductor/tracks/multiscale_polygon_surface_20260901/plan.md` | 8 | 0 | 7 | 15 | 0 |
| `conductor/tracks/parquet_production_acceptance_20260901/plan.md` | 0 | 0 | 18 | 18 | 0 |
| `conductor/tracks/platform_experience_qa_20260911/plan.md` | 1 | 0 | 26 | 27 | 0 |
| `conductor/tracks/pnw_herbaria_source_admission_20260911/plan.md` | 15 | 0 | 8 | 23 | 0 |
| `conductor/tracks/pnw_land_contact_experience_20260911/plan.md` | 1 | 0 | 45 | 46 | 0 |
| `conductor/tracks/pnw_land_context_reference_plane_20260911/plan.md` | 0 | 0 | 26 | 26 | 0 |
| `conductor/tracks/public_strategy_requests_20260913/plan.md` | 0 | 0 | 20 | 20 | 0 |
| `conductor/tracks/unified_intervention_layer_20260913/plan.md` | 20 | 3 | 0 | 23 | 0 |
| `conductor/tracks/weather_forecast_experience_20260911/plan.md` | 0 | 0 | 19 | 19 | 0 |
| `conductor/tracks/weather_forecast_parquet_lane_20260911/plan.md` | 0 | 0 | 18 | 18 | 0 |
| `conductor/tracks/environmental_parquet_serving_20260912/freshness-resolution-plan-20260913.md` | 2 | 0 | 27 | 29 | 0 |

## Reproduction and evidence

`.omc/research/runbook-20260914/progress-rollup/build.py` reads explicit UTF-8, hashes every input plan, RUNBOOK and registry, and writes machine-readable `.omc/research/runbook-20260914/progress-rollup/rows.json` and `.omc/research/runbook-20260914/progress-rollup/rows.csv`. Each row records path, exact line, heading, marker, status, historical exclusion and nested relationship. Source hashes bind this moment; a later rerun is a new snapshot, not a retroactive rewrite of the metric.

- Script SHA-256: `3e64c7e10b81e98b36a6eed58bad4f7f9399193139fabc5e89907cd3b8c0105b`.
- JSON SHA-256: `ce2c0f0c02d6d9244ca6a741622e57468f52e5de909cf94ea96fc1426492edf9`.
- CSV SHA-256: `adff4a04f6ee536779b377d39de0deaf56fe4ad9c1c82caea645c33d5384803c`.
- RUNBOOK SHA-256: `fbf63bc299d25de8cebab95f81b5093a34ca15d1bb9e75fb2dbef48ef2d64578`.
- Registry SHA-256: `451fb03e77eed3ee14eca8c06df4296134f7860d0efd0c6598c0def5e70cddb3`.

Authoring and independent review remain separate: this rollup is ready for root or another reviewer to check; no self-approval is asserted.
