---
type: evidence-receipt
track: platform_experience_qa_20260911
recorded_on: 2026-09-12
status: audit_only_acceptance_open
review_base: 64f4f892bd2b744cc097c7f76a1f239997b80f52
source_commit: 64f4f892bd2b744cc097c7f76a1f239997b80f52
source_tree: f8697da694a3f9fbbf28e70ff7581bd59546bfd6
source_task: 01a0945e-2bc6-7902-a4f6-22d347964c68
---

# Remaining service-backed and mobile acceptance matrix

This September 12 repository-only audit freezes the current local `main` at the
commit/tree above. It enumerates the remaining weather, multiscale, reader/gapless,
botanical, agent/MCP and role/accessibility gates. **No runtime case was executed
or newly passed. The historical weather approval remains conditional.** The
platform QA track remains active; production acceptance remains blocked.

The checkout is `C:/Users/atooz/.codex/worktrees/ff33/plantgeo`, initially clean
and detached at that local main commit. No remote fetch was performed, so
"current main" means the locally available revision at intake. The only authored
file is this dated evidence receipt. The shared task ledger, original cases,
track plans/statuses and runtime code are unchanged. No Railway, PostgreSQL,
`pgt`, writer, scheduler, object-storage, deployment or live service was accessed.
The operational prerequisites below are handoffs to their owners, not actions
performed or authorized by this audit.

## Source authority and custody

The [QA specification](../spec.md), [matrix contract](../matrix.md), original
[case inventory](cases.md) and latest [task ledger](task-ledger.md) govern this
receipt. The inventory was authored against `26b69d386946308830bc202cd6e773f03a4d23a4`;
its 206 cases and 165 `not_run` / 41 `blocked` counts are historical inventory
accounting, not a current execution verdict. This additive matrix keeps those
stable case IDs, adds audit-row IDs, and records later integrated evidence without
rewriting the inventory or resurrecting archived author tasks.

The source task above requested this isolated audit. The canonical QA and shared
ledger owner remains the owner recorded in `task-ledger.md`; feature owners keep
their own unresolved gates. Local subagents `/root/reader_gapless_audit` and
`/root/botanical_agent_audit` performed bounded, read-only source research against
this base. They did not acquire track ownership or write files. A separate
`/root/acceptance_matrix_verifier` reviews the authored receipt; its verdict and
the enclosing commit/tree are retained in the task handoff. No new lifecycle or
ownership claim is made in the shared ledger.

## Frozen-tree rules

| Key | Required identity and use |
| --- | --- |
| **F0** | Audit source / current local application candidate: commit `64f4f892bd2b744cc097c7f76a1f239997b80f52`, tree `f8697da694a3f9fbbf28e70ff7581bd59546bfd6`. Existing implemented journeys must start here or at an explicitly reconciled successor. This is not a deployed-tree or test-pass claim. |
| **F1** | Future owner correction integrated on F0 or its reconciled successor. Full base, commit, tree, changed paths, review and affected-case receipts are **unbound** until supplied. Required for the coverage caption and any unresolved day-policy or interaction implementation. An evidence-only commit cannot substitute for the correction. |
| **FB** | Future immutable botanical-profile candidate, incorporating admitted source/assertion/schema and release-reader changes. Exact application/service commit and tree plus manifest/release hashes are **unbound**. Transitional authoring lookup evidence cannot fill this slot. |
| **FW** | Future governed forecast-plane plus forecast-experience candidate. Exact integrated tree and pinned provider/model/run/valid-interval artifacts are **unbound**. The historical-weather tree cannot fill this slot. |
| **FO** | Future occurrence candidate after source admission, occurrence release, reader and multiscale intake. Exact integrated tree and release-set/QC/taxonomy/support hashes are **unbound**. Profile or Herbaria metadata receipts cannot fill this slot. |
| **FD** | Separately supplied operational/production packet: full deployed application, data-service and executor revisions/configuration, effective lane definitions, immutable releases and rollback revision. **No current complete packet is bound by this audit.** It must be reconciled with F0 or the later integrated candidate, never inferred from a local Git hash. |

Every row below names its freeze key. Where a row needs both F0 and FD, source
inspection is possible on F0, but the service-backed verdict waits for the exact
operational packet. A new code candidate requires impact review and new affected
evidence; earlier captures retain their original scope and identities. The commit
containing this document will have a different full tree from F0 solely because
it adds this receipt. Its identity is reported after commit, avoiding a circular
self-hash inside the document.

## Completed slices and approval limits to preserve

These are retained historical outcomes, not new passes in the matrix below.
Full identities were resolved from local Git objects during this audit.

| Retained slice | Exact owner/capture commit and tree | Scope that remains accepted or completed |
| --- | --- | --- |
| Historical weather visual repair | `8e53b416ce5dc5287295a707dae2f9c121e1e993` / `23ba93ff46458c5d8bd399072ce667d152d11235` | [Conditional approval](weather-approval-20260912.md): fixed desktop unavailable-state repair, report/source/SI-unit presentation, empty typed feed and no fabricated or silently substituted frame. No populated-data or forecast approval. |
| Root weather browser capture | `599f3e4ff66bc62049360aef430723563c0bf1fb` / `5b2251f6bb76061696ef40b70bb303e04b96d339` | [1280x720 local browser observation](browser-weather-20260911.md), with unavailable governed service. Preserve the capture identity separately from final integrated candidate `9284d52738dbb323bbcd1dfe8c22ab6290f2a4b1` / `a0797ce0b4a91b2ac051dfa423382fbf3db60104`. |
| Weather style-readiness recovery | `5cf7f59b23d61d8291c05ff9915e523710606ca1` / `f5eed7708673c4ccd11af6d3ac385071a8f8e5b5` | Independently reviewed missed-`style.load` recovery, integrated at `5bbe3dc`. F0's `WeatherLayer.tsx` has no diff from this candidate. The focused regression is completed. |
| Ready-to-outage report regression | `994760e02d0e40008f8c54a9280d749901f16e93` / `36feec73eeddf6af086845d8fe57092f50851d89` | Independently reviewed fixture regression, integrated at `37eb50a`: typed `upstream_unavailable` removes readings and map-marking action. F0's report and report test have no diff from this candidate. |
| Scalar annotation implementation | `2b29d9f1ad475358e96fc6c0e48aabd9a3e7d29b` / `878a071801f472a9cbcd3cb02eb06c2845d5d3c4` | [26 synthetic captures](../../multiscale_polygon_surface_20260901/evidence/scalar-labels-20260912/README.md), 1280x720 and 390x844, blank basemap, z3/7/10/13. Numeric labels, zero/missingness, aggregate `avg`, opacity and style mechanics remain completed. F0's label helper and climate/soil components match. Direct WebGL `readPixels` was unreliable; only the documented screenshot-derived probes count within their narrow scope. |
| Botanical local release-lookup construction | `edc6afdeb23f339b40049ec0828551c2fe1a4d45` / `01ad55b6220a13604e8fbf8a4ceda773e35d5658` | Local implementation and review retained by the [integration packet](integrated-weather-botanical-candidate-2026-09-11.md). Does not establish an admitted published release. |
| Botanical transitional UUID wiring | `f122069fd3f635d2f39f55970a3e9b57f925a167` / `61d88bd500579d108e5a004cb5dbfb48fc632e5a` | [Bounded authoring lookup](../../botanical_species_profile_lookup_20260911/evidence/transitional-authoring-lookup-2026-09-11.md), integrated `3e35971`. HTTP/agent/MCP wiring and exact-UUID refusal mechanics are completed. F0's plane, HTTP module, tool registry, MCP and graph files match this source; no immutable-release or production census acceptance follows. |
| Botanical source-contract audit | `2777778813b36a962a8ed9c4505934034098e482` / `91723969b291df1ed17c88709550b7c81dbd9cd0` | [P0/P1 audit](../../botanical_species_profile_lookup_20260911/evidence/non-herbaria-profile-contract-audit-2026-09-12.md), integrated `4072fb0`, is complete as an audit. Admission and assertion/schema prerequisites remain blocked. |
| Reader/availability and recovery definitions | `6213b303da54e91204c3da1ff4f9a7def896dbdf` / `11be3de73b218a008233f1053598e5248f7f37f8` | Integrated `267e197`. Local R0 contract definition, derived-empty support and ownership reconciliation remain completed; historical fire hard cuts, legacy removal, cancellation and support tests are not reopened. |
| Reader/gapless blocker receipt | `de6486242ef057bc8bc77334c8b21049dbcbad10` / `e7dca1d02e3e18578731ce986d84bed9a6eede5e` | Integrated `0669adb`; completed documentation continuation. Its day, caption, trace, ownership and recovery blockers remain the current handoff. |

The [operational checkpoint retrospective](../../../retros/parquet_operational_checkpoints_20260911/README.md)
also preserves completed temperature history and bounded MTBS publication/browser
subsets. Temperature covers the recorded mean/min/max historical interval, not
later forward health. MTBS reader correction `fa202230958fb55521963e886eb031be5fc266c4`,
tree `b356db4849b488f09a2cbdf5476c6e143cc60980`, has bounded 2018–2026 evidence;
the documented 746-versus-747 viewport distinction is not a new discrepancy.
Neither closes all-product renderer/reader acceptance or future scheduled runs.
Completed intervention boundary implementation remains retained under its
[ledger entry](task-ledger.md) and [intake specification](intervention-boundary-spec.md);
remaining end-to-end role/touch evidence does not reopen that local code slice.

## Evidence required for every remaining execution cell

Rows are audit groups over stable PGQA requirements, not replacements for the
full 206-case inventory. `blocked` means a named prerequisite or owner packet is
missing. `not_run` means the journey remains to be executed; it does not claim
the environment is currently provisioned. No row is `pass`, `fail` or
`not_applicable` on source inspection alone.

Each eventual record needs: exact requirement and variant ID; owning track and
live author/verifier task; review base, candidate/tree and dirty diff; service
revision/configuration and immutable data/fixture identity; role and organization
fixture without credentials; expected result, steps and observed result; UTC
timestamp; original captures and SHA-256 hashes; request/response and console
traces; blocker/defect and independent verdict. Freeze browser/version, viewport,
portrait/landscape, input and emulation versus physical device. Physical-device
coverage remains separate and cannot be inferred from viewport emulation.

For visual rows, preserve camera/bbox, zoom and actual rung, layer, opacity,
basemap, selected geometry, requested/served/painted day, release and capture
timing. Pixel evidence needs region, baseline, tolerance and result. Include
source support, unit-bearing text and non-colour missingness. Name cold/warm
browser, application and data-cache states separately; measure catalogue latency,
TTFB, request-to-paint, requests/bytes and interaction/frame budgets frozen before
execution. A browser cache reset proves nothing about upstream cache state.

For every applicable UI row, run desktop mouse/keyboard and mobile touch/keyboard,
focus order/return, Escape, screen-reader announcements/text alternatives,
accessible names, 200% zoom/reflow, contrast/non-colour cues, reduced motion and
touch-target checks. Expand materially different normal/loading/empty/missing/
stale/error/retry/cancel/refresh states and temporal/spatial cases. Reduced
combinations require a written scope rationale accepted by the separate verifier.

## Weather

Owner keys in this table are [QA](../plan.md), [reader](../../parquet_reader_cutover_acceptance_20260901/plan.md),
[forecast experience](../../weather_forecast_experience_20260911/plan.md) and
[forecast plane](../../weather_forecast_parquet_lane_20260911/plan.md).

| Audit ID / stable requirements | Owning track and gate | Frozen tree | Data/service prerequisite | Evidence needed to close the remaining scope | State |
| --- | --- | --- | --- | --- | --- |
| WX-01 — PGQA-L05, M11, D13 | QA; historical-weather visual intake, reader support contract | F0 + FD | Governed populated raw samples and aggregate cells for exact selected days, source/product, support and units | Real painted temperature labels, `avg` versus raw values, zero/missing distinction, selected feature and distance/support disclosures; match reader, legend, report and answer at coarse/middle/detail. | blocked |
| WX-02 — PGQA-L05, M06/M11, D12 | QA; historical-weather dense-basemap and picking gates | F0; F1 if interaction correction needed; FD | Populated wind/precipitation feed and dense admitted basemap on fixed day/camera | Wind-label collision/readability, precipitation hover/selection, equivalent persistent touch/text detail, unit agreement, overlapping feature identity and style/opacity recovery. Synthetic raw-sample smoke is insufficient. | blocked |
| WX-03 — PGQA-T02/T04/T08, W06 | Reader with QA; requested/served-day follow-up in conditional approval | F0 for contract intake; F1 for required policy/caption changes; FD | Owner-approved allowed relationship for populated current-day `requestedDay` and `servedDay`, plus exact catalogue/reader evidence for 2025-04-28 and real day transitions | Show notice or refusal for differing days; never relabel retained or neighbouring data. Capture request, settled response, painted frame and report across rapid day/place changes and delayed replies. Ready-to-outage fixture remains completed. | blocked |
| WX-04 — PGQA-G02/G04/G09, M10, D12, L05 | QA; historical-weather mobile and accessibility acceptance | F0; FD for populated variants | Mobile browser/input profile, current historical report in unavailable and governed-populated states; frozen device and accessibility scope | Narrow portrait/landscape reflow, sheet/report reachability, touch targets, scroll versus map gestures, keyboard focus/return, announcements, units and non-colour states. Desktop 1280x720 evidence does not cover these variants. | not_run |
| WX-05 — PGQA-W01/W04/W05/W07 | Forecast plane F0–F3; forecast experience X0/X1/X4 | FW + FD | Admitted deterministic provider/model with rights, horizon, variables, time/support schema, immutable complete run and bounded location reader | Distinct sampled Now/History/Forecast identity; picked/searched-location current/hourly/daily cards only over supported horizons; run/issue/valid/interval/timezone/units agree with map/tool/answer. Preserve explicit unavailable states and no cross-product fallback. | blocked |
| WX-06 — PGQA-W02/W03/W08 | Forecast plane F1/F3/F4; forecast experience X2–X4 | FW + FD | Governed scalar support/domain masks and vector u/v fields; admitted interpolation if any; frozen byte/texture/particle/frame budgets | Real-data desktop/mobile continuity; vector wrap/cancellation and direction semantics; no animation across no-data; pause/reduced-motion static/text equivalent; cold/warm performance and GPU/style/unmount cleanup; independent scientific/design/accessibility/agent review. | blocked |

## Multiscale scalar surfaces

The [multiscale plan](../../multiscale_polygon_surface_20260901/plan.md) and
[render/support specification](../../multiscale_polygon_surface_20260901/spec.md)
own M0 baseline completion and M3 integrated verification. The scalar-label
implementation is a completed subset of this active track.

| Audit ID / stable requirements | Owning track and gate | Frozen tree | Data/service prerequisite | Evidence needed to close the remaining scope | State |
| --- | --- | --- | --- | --- | --- |
| MS-01 — PGQA-L11–L22, M05, D09/D10 | Multiscale M3; reader; QA | F0 + FD | Populated climate/soil products with required depth/statistic intersections, stable support IDs, aggregation and real source units | Live zero, tiny nonzero, missing/non-numeric and `avg` labels at all rungs; labels/legend/details/tool agree; isobands retain range meaning without measurement labels. Do not treat repeated/collision-omitted labels as observation counts. | blocked |
| MS-02 — PGQA-M08/M11, L01–L08/L10–L22 | Multiscale M0/M3; reader support and production acceptance | F0 + FD | Published cross-product detail/aggregate pairs and production visual baselines for MTBS, fire, air temperature and soil moisture | Counts/sums conserve detail under declared aggregation; one physical rung at a time; adjoining boundaries and source-part seams show no cracks/nested blocks. Preserve native polygons, detection-density meaning, discrete vegetation 0.25-degree support and the recorded soil-survey coarse-summary deviation. | blocked |
| MS-03 — PGQA-M06/M11/M12, D12/D13 | Multiscale M3 with QA; feature owner for hover | F0; F1 if missing interaction needs implementation; FD | Governed populated signals, dense basemap and real selection support; climate/soil shared hover contract supplied by owner | Hover/touch equivalent selects the painted support and value; labels stay readable through style reloads, opacity and rapid day changes. Existing climate/soil lack of shared hover registration remains open, not newly delivered by labels. | blocked |
| MS-04 — PGQA-M10, L11–L22, T10 | Multiscale M3; production acceptance; QA mobile | F0 + FD | Dense real payloads, named cold/warm states and predeclared request/byte/feature/frame budgets; full mobile application | Desktop/mobile canvas captures and pixel probes with basemap, reflow/touch journeys, response feature/byte counts and measured request-to-paint/frame behavior. Fixture settle waits of roughly one second are not live performance results. Submit exact renderer packet. | blocked |

## Reader availability and gapless prerequisites

The latest [reader blockers](../../parquet_reader_cutover_acceptance_20260901/evidence/local-acceptance-blockers-20260912.md),
[availability contract](../../parquet_reader_cutover_acceptance_20260901/evidence/local-availability-contract-20260912.md),
[publication blockers](../../gapless_parquet_publication_20260901/evidence/local-publication-blockers-20260912.md)
and [ownership audit](../../gapless_parquet_publication_20260901/evidence/local-ownership-audit-20260912.md)
distinguish implemented contracts from missing operational proof. These rows
consume future owner receipts; this lane cannot fill their gaps by accessing data
systems or activating work.

| Audit ID / stable requirements | Owning track and gate | Frozen tree | Data/service prerequisite | Evidence needed to close the remaining scope | State |
| --- | --- | --- | --- | --- | --- |
| RD-01 — PGQA-L01–L27, T03/T05–T08 | Reader R0/R2 and QA; production A1/A2 | F0 + FD | Per-product latest eligible, populated historical, governed-empty, missing/unwritten, outside-coverage and withheld fixtures; required rungs/depths/statistics; pinned reference releases | Catalogue, exact reader, painted state, legend/details/answer reconcile. Prove common-lane intersections, checksum/stale/bootstrap/rung failure behavior, recorded versus carried days, static/null axes and honest source ceilings. Unsupported soil/soil-survey claims remain withheld. | blocked |
| RD-02 — PGQA-T03/T06/T08, G09/D12 | Reader R2 coverage/source-ceiling caption; QA accessibility | F1 + FD | Caption implementation plus capability fixtures with `coverageAuthority` and `sourceCeilingDay`; owner reconciliation of recorded-versus-carried rule | Visible accessible text distinguishes index authority from census/object-walk authority and legitimate source ceiling from delayed publication. Current layer-panel source consumes neither field; this is an implementation prerequisite, not just missing screenshots. | blocked |
| RD-03 — PGQA-T10, M12 | Reader R0 timings/R2 traces, gates 7/9; production A1/A2 | F0 + FD | Exact effective coverage policy, pointer/generation/rollup/bootstrap identities and named cache states per lane | Coarse/middle/detail cold/warm catalogue time, day-row TTFB, request-to-paint; pointer/generation/rollup/bootstrap GET counts and zero historical LIST/data-part reads for admitted time-bearing availability scope. Reconcile literal two-GET gate with actual request classes; census-policy/static exceptions cannot be silently called zero-LIST. | blocked |
| RD-04 — PGQA-T01/T02/T04/T08–T12, M08/D13 | Reader production halves of gates 1/4; QA | F0 + FD; F1 for corrections | Settled selected days, carried snapshots, delayed/reordered responses, timezone/date boundaries, temporal/spatial neighbours and saved-device day fixtures | Pan changes bbox while day stays settled; scrub requests bounded viewport and no live `/api/fires`; zoom chooses one rung; pin/step controls work by touch/keyboard. Requested/served/painted dates, neighbour offsets, saved-versus-published bands and `truncated=false` agree. Preserve already-proven cancellation scope and batching asymmetry. | blocked |
| GP-01 — PGQA-T03/T06/T08 prerequisite | Gapless P0 census/P1 registration/P3 tails; environmental retirement acquisition ownership | FD | Current source-direct or preserved-Parquet owner for every required soil-wetness, precipitation, dew-point, drought and burn-severity interval outside scheduled windows; exact requested floors and already-satisfied intervals | Source inventories, provider floor/ceiling/lag, unresolved interval list and receipt-backed governed absences; terminal/all-rung and availability reconciliation. Generic repair definitions do not prove source acquisition or complete history. | blocked |
| GP-02 — PGQA-T10 prerequisite | Gapless P3; environmental retirement effective executor boundary | FD | Exact deployed command/definition, active/required lanes, checkpoints/leases and effective cutoff, supplied by authorized owner | Readback proves exclusive ownership and no overlapping writer. Historical `configured_pending_deployment` pause is not an effective cutoff. Bind definitions and runtime configuration, not only a release SHA. | blocked |
| GP-03 — PGQA-T06/T10 prerequisite | Gapless P3/P4; production A3 | FD | GP-02 proof, settled source input, exact run/work-item/attempt/output identities | Transient failure → retry → terminal publication; restart retains cursor/definition with bounded catch-up; expired lease reclaim fences stale worker. Preserve implemented derived-empty completion while checking actual marked rungs and legacy repair needs. Tests/registry presence alone cannot prove observed recovery. | blocked |
| GP-04 — PGQA-T10 prerequisite | Gapless P4; production A3 | FD | Activated product/lane list, valid owner/cutoff and sufficient source advances | At least three consecutive scheduled advances per activated lane with run/time/output/receipt identities, coverage and all-rung reconciliation. Completed temperature historical generations and configured MTBS daily/weekly duties do not count as future burn-in. | blocked |

## Botanical lookup

The [profile plan](../../botanical_species_profile_lookup_20260911/plan.md) and
[P0/P1 contract audit](../../botanical_species_profile_lookup_20260911/evidence/non-herbaria-profile-contract-audit-2026-09-12.md)
keep the transitional `species_information` authoring lookup separate from the
intended immutable `botanical-species-profile` lookup. The former accepts an
internal UUID and marks legacy values unpublished/unverified; it is not a
taxon-concept or admitted-release lookup.

| Audit ID / stable requirements | Owning track and gate | Frozen tree | Data/service prerequisite | Evidence needed to close the remaining scope | State |
| --- | --- | --- | --- | --- | --- |
| BT-01 — PGQA-B01/B04 prerequisite | Botanical profile P0/P1 | FB | Admitted non-Herbaria source field rights/version/units/context, authority/version/taxon-concept mapping, per-assertion review/provenance and conflict/missingness schema | Field-by-field admission and reviewed assertion packet; separate measured traits, curated requirements, categorical summaries and effects. Wide Species rows, false defaults and companion citations cannot establish provenance for species-level growth/fuel/agricultural assertions. | blocked |
| BT-02 — PGQA-B01–B05 | Botanical profile P2/P3/P4; QA | FB + FD | Approved immutable profile/assertion/decision/manifest release, stable taxon mapping and registered API/agent/MCP readers | Exact taxon+release lookup retains per-value licence/source/unit/method/review, synonyms and conflicts, field missingness and section separation; empty/unavailable release refuses without editable-database fallback. UI/tool/final answer uses same release and refuses unsupported planting/fuel/effect/ranking claims. | blocked |
| BT-03 — PGQA-B04/B05, A15/A16 | Botanical profile transitional checkpoint; QA agent parity | F0; future authorized isolated service packet required | Explicitly synthetic permitted UUID/absent/invalid-ID and approved-companion fixtures, local endpoint/blueprint and role identities | Service-backed HTTP/agent/MCP and final-answer consistency for the narrower unpublished/unverified authoring response, bounds, missingness and refusals; measure no silent conversion into release evidence. Existing focused wiring tests remain completed. No database or census is accessed for this audit. | blocked |
| BT-04 — PGQA-B02/B03/B06 | Botanical profile P2/P4; source/release owner | FB + FD | Two admitted release identities, bounded pagination, competing/withdrawn assertions, conditional publication and rollback receipts | Pin/cache identity survives source update; continuation is bounded; original assertions/conflicts retained; authoring-to-artifact conservation, replay/interruption/rollback and per-value evidence remain exact. A second source is separately admitted enrichment. | blocked |
| BT-05 — PGQA-B07–B11 | Occurrence plane/experience, Herbaria admission; recommendation validation separate | FO + FD | Exact source rights and withheld-coordinate rules, governed release-set/QC/taxonomy, uncertainty-aware support, multiscale reader; objective-effect source for any later recommendation gate | Occurrence detail, exact-concept richness, effort, filters and neighbours retain uncertainty/offsets and release identity. Refuse abundance, current occupancy, surveyed absence and suitability. Profile data and metadata-only Herbaria admission work cannot unlock occurrence or ranking. | blocked |

The [blocked botanical census receipt](../../botanical_species_profile_lookup_20260911/evidence/railway-production-botanical-census-2026-09-11.md)
remains historical evidence of a gate stopped before database access. This audit
does not infer any production row counts or use `pgt` as a substitute.

## Agent and MCP parity

Current source declares **11 tools**, not the ten in the older PGQA-A15 text:
`signals_near_point`, `drought_history_at_point`, `fire_history_near_point`,
`forecast_summary_for_cell`, `signal_value_on_day`, `signal_neighbors_in_time`,
`nearest_signal_cells`, `observation_coverage_on_day`,
`observation_temporal_neighbors`, `feature_value_near_point`, and
`species_information`. The [common registry](../../../../services/agri-data-service/src/agri_data_service/agent/tools.py)
and [MCP descriptor construction](../../../../services/agri-data-service/src/agri_data_service/agent/mcp_server.py)
are the source authority; actual transport discovery remains to be captured.
The [observation surface catalogue](../../../../services/agri-data-service/src/agri_data_service/agent/surfaces.py)
still declares 24 surfaces. The botanical reference tool is not a 25th observation
surface. Retain PGQA-A15 and expand it against the current frozen manifest instead
of treating the historical count as a passing assertion.

| Audit ID / stable requirements | Owning track and gate | Frozen tree | Data/service prerequisite | Evidence needed to close the remaining scope | State |
| --- | --- | --- | --- | --- | --- |
| AP-01 — PGQA-A15 | QA; botanical owner supplies added tool contract | F0; FB/FW/FO when integrated manifests change | Frozen service blueprint/provider/tool schemas and isolated MCP transport | Actual initialize/ping/tools-list/tools-call packet reconciles all 11 current names and schemas; bind later additions explicitly. Descriptor presence/import tests do not prove runtime discovery or execution. | not_run |
| AP-02 — PGQA-A05–A14/A17, T09 | Reader and QA; community exception; production parity | F0 + FD | Admitted populated/historical/empty/missing/outside-domain fixtures for all 24 observation surfaces; bounded point/window/filter inputs | Same role/place/day/window/filter/units/release across UI, tool and final answer. Compare exact values, source support, spatial distances/time offsets, coverage and refusals; preserve protected intervention publication and explicitly unsupported soil/demand/strategy scope. | blocked |
| AP-03 — PGQA-A02/A03/A08/A17, B05, W07 | QA; botanical, weather and recommendation owners | F0 for current tools; FB/FW/FO for future claims; FD | Truthful current-product/forecast-summary provenance, pinned evidence and missing/unavailable tool variants | Final generated answer distinguishes evidence from inference, sampled weather from forecast runs, growth compatibility from objective effects and documented occurrence from abundance. Refuse unsupported recommendations; successful tool calls alone do not close parity. | blocked |
| AP-04 — PGQA-A15/A16 | QA protocol/refusal acceptance | F0 | Isolated MCP/provider transport with malformed, unknown, unavailable and failure fixtures | Protocol negotiation, unknown method/tool, schema rejection, typed refusal and transport error are distinct; recovery works after error; diagnostics do not corrupt stdio and unavailable tools are named. No invented replacement answers. | not_run |
| AP-05 — PGQA-A01–A04, G05–G07 | QA; community/organization ownership for protected data | F0; authorized role/service fixtures required | Separate synthetic identities/organizations and local conversation storage; approximate/exact location consent variants | Cancel sends nothing; only chosen precision is used; stream/cancel/retry and save/reopen/feedback preserve ownership. Session/role changes clear private context in UI and service; accessible mobile controls and final answers remain coherent. | blocked |

## Roles, mobile/touch and accessibility

The [QA plan Q1](../plan.md), [community plan](../../community_engagement_completion_20260805/plan.md)
and [boundary intake](intervention-boundary-spec.md) retain these gates. Platform
roles and organization roles are separate axes. Historical implementation
completion is not end-to-end role or assistive-technology acceptance.

| Audit ID / stable requirements | Owning track and gate | Frozen tree | Data/service prerequisite | Evidence needed to close the remaining scope | State |
| --- | --- | --- | --- | --- | --- |
| RA-01 — PGQA-R01–R23, G01/G03/G05–G08/G10, O01–O09, P04–P06/P08 | QA role/server-boundary acceptance; integration owns shared auth fixes | F0; F1 only if defects require changes | Anonymous plus synthetic viewer/contributor/expert/admin, separate org memberships/owners, expired/foreign/malformed fixtures; permitted isolated endpoints and disposable fixture DB supplied in a later authorized task | Allowed and denied navigation/direct URL/API, refresh/deep-link, session expiry/sign-out/role or org change and private-cache invalidation agree. Include developer/admin API boundaries without activating jobs in this audit. Actual sessions prove server denial and UI behavior; hidden buttons alone do not. | blocked |
| RA-02 — PGQA-C01–C16, I01–I05, M10 | Community completion and retained boundary owner; QA | F0 for integrated boundary source; F1 if corrections; authorized local fixture packet | Disposable synthetic workflow environment, identities, consent and disabled external transports; separate real human contributor prerequisite for C16 | Touch/keyboard draw, undo/edit/cancel/validation/style cleanup and restored map handlers; local submit → expert publish/reject → contributor outcome → map visibility with geometry preserved. Synthetic normal-flow rows prove mechanics only; they cannot satisfy C16 or production/training acceptance. | blocked |
| RA-03 — PGQA-G02/G04/G09, M02/M09/M10/M11, D12, T02/T11/T12; U variants throughout | QA accessibility/mobile; each feature track owns fixes | F0; corresponding FB/FW/FO for future surfaces | Frozen browser/device/assistive-tech scope, portrait/landscape and 200% zoom; admitted service data for populated visual variants | Every relevant nav/dock/sheet/report/map control has names, focus/order/return, Escape, keyboard and touch operation, announcements, text equivalents, contrast/non-colour states, reduced motion and usable target size/reflow. Include scroll/drag versus map gestures; emulation and physical-device findings remain separate. | not_run |
| RA-04 — PGQA-G06/G09/G10, M12, D13, A02/A04 | QA recovery and cache acceptance; owner of affected service | F0 + exact isolated service/configuration packet | Controlled slow/failing/unavailable/cancelled responses, role transitions and named cache states | Mobile/desktop loading, retry, outage, stale report and focus recovery stay truthful; no duplicate writes, stale privileged action or private data from prior identity. Capture announcements and request-to-paint with final tool/answer context where applicable. | blocked |
| RA-05 — PGQA-C15/O10 | Community legacy moderation and QA UserPanel reachability | F0 for source/reachability reconciliation; F1 if owner supplies integration | Owner-reconciled mount or independently accepted scope exclusion for the historical source-only `ModerationPanel` and `UserPanel` questions | Retain distinct requirements until actual reachability is proved or exclusion accepted. Canonical contribution-review evidence cannot silently close a separate legacy moderation case; source presence alone does not prove a mounted user journey. | blocked |

## Integration and release gate

| Audit ID / stable requirements | Owning track and gate | Frozen tree | Data/service prerequisite | Evidence needed to close the remaining scope | State |
| --- | --- | --- | --- | --- | --- |
| QA-01 — cross-cutting Q2/Q3; production A0–A4 | QA integration and independent verifier; [production acceptance](../../parquet_production_acceptance_20260901/spec.md) owns operational fan-in | Reconciled F0/F1/FB/FW/FO as applicable + FD | Complete owner reader/renderer/publication packets and every required remaining execution variant; exact service/deployment/release and rollback matrix supplied by its owner | Reconcile task/base/commit/tree/diff/receipt hashes before verdict. Per product: newest eligible terminal, populated historical, governed-empty day × coarse/middle/detail × named cold/warm coverage/day/window/release/refusal routes; bounds/truncation, canvas, source/absence and scheduled/recovery evidence. After all fixes, one final integrated quality sweep plus separate scientific/design/accessibility/agent review. No blocked/unrun required cell can become GREEN. | blocked |

The [root integrated checks](root-integrated-checks-20260912.md) remain partial:
boundary check passed; frontend type/lint/changed-tests were not run because the
required toolchain was unavailable in that checkout. Ruff format/lint and mypy
passed, but the combined Python attempt recorded `5482 passed, 147 skipped,
1 xfailed, 510 errors`. Focused botanical evidence recorded `66 passed, 2 skipped`.
These are dated owner/root results, not fresh checks of F0 and not a full-suite
pass. This documentation-only audit neither reruns them nor closes that gate.

The operational matrix requires source/absence receipts, manifests, bounds,
availability pointer/generation/bootstrap identities, exact deployed revisions
and rollback as defined by the production track. They remain owner-supplied
prerequisites under the explicit no-access boundary of this task. Historical
rollback ranges must not be interpreted as instructions to restore retired
PostgreSQL readers.

## Receipt interpretation and handoff

This receipt contains **34 remaining audit groups: 30 blocked and 4 not_run**.
Those are group counts, not executed-test counts or an updated count of the
206-case source inventory. Overlapping PGQA IDs express shared journeys, not
duplicate passes. The preserved completed-slice table contributes no new pass
count. Planned PNW/contact cases and other inventory families outside this audit's
six requested domains retain their existing obligations and are not excluded or
marked complete here.

Independent review should check each row's requirement, owner, freeze key,
prerequisite and evidence against the linked local sources; verify the 11-tool /
24-surface correction and conditional weather scope; and confirm the diff is
limited to this file. The final documentation sweep checks OKF frontmatter,
relative-link targets, unique audit IDs, matrix/count consistency, source Git
identities, whitespace and unchanged runtime/shared-ledger paths. Application
and Python tests are outside this documentation-only change surface under
[the testing policy](../../../../docs/testing.md); no application, service or
release-quality receipt is claimed.

The enclosing commit and tree, receipt SHA-256, documentation-check outcome and
separate reviewer verdict belong in the task handoff for exact-tree independent
review. The canonical coordinator may consume this evidence and assign the
remaining owner work. This audit does not edit its ledger or grant permission to
run services, mutate data, deploy, close parent tracks or archive unresolved work.
