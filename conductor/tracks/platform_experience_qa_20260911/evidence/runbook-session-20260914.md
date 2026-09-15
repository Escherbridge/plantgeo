---
type: evidence-receipt
track: platform_experience_qa_20260911
recorded_on: 2026-09-14
status: engineering_checkpoint_browser_acceptance_open
review_base: 0f16e40dae3cce1d3b6d4ac00138254a968d974f
source_tree: 399ff61a9bf20023bcdfbc4ba8b8c3190050fff9
coordinator: /root
---

# September 14 long-horizon runbook session

The user requested a continuing run through the declared runbook, including full QA for layers, time sliders, intervention/social journeys and the agent workspace. Root coordinates bounded sessions toward that outcome and retains the persistent goal across handoffs. This receipt starts the current execution record; it does not close the runbook, the platform QA track, or any data/production acceptance gate.

## Candidate and ownership intake

The initial base is `0f16e40dae3cce1d3b6d4ac00138254a968d974f`, committed tree `399ff61a9bf20023bcdfbc4ba8b8c3190050fff9`. The shared checkout was already dirty. This base/tree is a source observation, not the final integrated candidate. Root must bind the final candidate, dirty diff, service revisions, fixture/release identities and review/check receipts before acceptance.

| Lane | Bound task | Scope |
| --- | --- | --- |
| Coordinator and integration | `/root` | Persistent goal, session ordering, shared-file reconciliation, registry/RUNBOOK/metadata ownership, final sweep and independent verdict intake. |
| Map/time source audit and this evidence author | `/root/qa_inventory` | Read-only source census, then authorized edits only to `evidence/cases.md` and this session receipt. No application changes or tests. |
| Workspace/social audit and bounded source fixes | `/root/workspace_social_audit` | Audit handoff identifying workspace pane/draft/close/resize cases and unsupported feed telemetry; any authored changes require their own exact path/diff receipt and separate verification. |
| GBIF feedback author | `/root/gbif_feedback_executor` | Bounded explicit GBIF feedback correction assigned by root; implementation and final check evidence remain pending intake. |
| Independent verifier | `/root/independent_verifier` | Reviews frozen source hashes and scoped check receipts independently. Authors do not approve their own corrections; complete browser/data acceptance remains open. |

Root captured initial status and diff under `.omc/research/runbook-20260914/`:

- `base-identity.txt`: full base commit and committed tree.
- `initial-status.txt`: pre-existing modified and untracked paths.
- `initial-working-tree.patch`: initial tracked-file diff; a Git diff does not contain untracked file bodies.
- `botanical-current.json`: captured current-pointer response identifying botanical release `956c0be71910469005fb494d92aac035223be49d1f5c895c0b1206a716b16ac4`. A pointer alone does not verify physical objects, completion, coverage, or the reader.

The following initial changes belong to existing work and must be preserved:

| Initial status | Path |
| --- | --- |
| Modified | `conductor/RUNBOOK.md` |
| Modified | `conductor/tracks/environmental_parquet_serving_20260912/metadata.json` |
| Modified | `conductor/tracks/environmental_parquet_serving_20260912/plan.md` |
| Modified | `services/agri-data-service/src/agri_data_service/interface/cli/data.py` |
| Modified | `services/agri-data-service/tests/parquet/test_availability_provenance.py` |
| Modified | `src/app/about/page.tsx` |
| Untracked | `.agentgraph/` |
| Untracked | `services/agri-data-service/src/agri_data_service/interface/cli/availability.py` |
| Untracked | `services/agri-data-service/tests/interface/test_availability_cli.py` |
| Untracked | `services/agri-data-service/tests/parquet/availability_documents.py` |

No ownership transfer over these changes follows from discovering them. The documentation author did not modify those files. Root serializes any authorized shared-file integration; no cleanup, reset or stash operation is part of this intake.

## Findings and current limits

### First engineering checkpoint

The [frontend source snapshot](source-candidate-20260914.json) binds 1,665 files under aggregate
SHA-256 `3fd1fa8c4720d769bab4e0631c463c53589b85e34b9ff0482e4d9c203f69e60c`.
Independent review rehashed every entry with zero mismatches. The complete frontend suite passed
190 files / 2,497 tests. After removal of a redundant discriminant check, type checking passed
and the final affected LayerManager suite passed 87 tests. Data-boundary checks passed. Full lint
exited zero with 9,887 warnings, including copied historical worktree/vendor code; it was not a
zero-warning pass. These checks do not establish browser or release acceptance.

The backend sweep exposed a test that accidentally selected configured object storage and a
one-ULP weighted-mean assertion mismatch after temporary-path failures were resolved. The
subsequent bounded Python batch isolates the test, normalizes botanical metadata transport
failures to typed unavailable results, and limits rounding tolerance to the mean assertion.
The [final source snapshot](source-candidate-20260914-4.json) binds aggregate SHA-256
`d1c4d79ef4c85691a82f51281b98cdab2177814c9017f15d8733856ebf7b728b`.
The reviewed Python sweep passed formatting, lint and the full selected pytest scope. Its only
remaining failure was an unused suppression comment; removing that comment changed no runtime
behavior, and a targeted final mypy run passed 436 source files. Candidate 3 retains the tested
runtime bytes; candidate 4 differs only by that comment. The [check receipt](check-receipt-20260914.json)
retains all command exits and log hashes, including failed environment attempts and the nonzero
combined runner exit before the final mypy correction. This is reconciled check evidence, not a
claim that one all-zero integrated command or a production quality receipt was issued.

The Python selector conservatively chose full pytest because of shared/unmapped files, but its
changed-mode invocation cannot issue a full quality receipt. Database integration variables were
removed; migration/bootstrap, relation census, build/deployment, browser and production acceptance
were not performed. The documentation scan found two missing references in the pre-existing
environmental plan; these remain D260914-11 rather than being silently replaced.

Fresh [public request receipts](public-read-requests-20260914.json) bind exact URLs, timestamps,
HTTP statuses and response hashes. [Botanical object evidence](botanical-read-20260914.json)
binds the source bucket/keys, reader-script hash, object hashes and counts. The wide botanical
request remains refused. The [slider catalogue](slider-capabilities-20260914.json) reports soil
streams through September 5, most climate streams through September 9, and shortwave radiation
through May 31. These are catalogue claims requiring object/marker/reader reconciliation, not
new acceptance of those horizons. Public readiness reported database/Redis/configuration ready;
the public responses did not expose the deployed code revision or prove upstream cold-cache state.

The [dated case delta](cases.md#september-14-current-surface-and-case-delta) records **31 registry layers plus four separately mounted land-context groups**, replacing use of the historical 27-layer count as a current census. It adds 13 stable cases for botanical/GBIF toggles, collecting-event intervals, agent workspace interactions and unsupported feed telemetry. It corrects SSURGO's current expected reader to Parquet while retaining the original PostgreSQL wording as historical context. Existing case statuses and historical receipts are not silently promoted to passes.

Mounted botanical and land-context UI is now source-confirmed. Source presence does not establish admitted data or painted results. The botanical reader still has zoom-specific bbox ceilings at `services/agri-data-service/src/agri_data_service/planes/botanical_occurrences.py:60`; the RUNBOOK records the unresolved load/viewport tradeoff. Land-context still has a placeholder reader at `src/lib/server/services/land-context/parquet-reader.ts:1`, discarded geometry at `src/components/map/land-context/useLandContextQuery.ts:63`, and selection-driven point/area queries at line 87. A viewport-query redesign must not be inferred from a toggle that was specified for explicit selection.

The workspace audit identified missing concrete checks for requested pane changes while mounted, preservation of pre-geometry form fields, canceled versus confirmed discard of proposal and AI state, resizing a previously hidden map, and repeated reveal of live drawings. Its source handoff also identified fixed unsupported feed benefit copy. Corrections do not establish behavioral passes; the new A18–A23/C17 cases name the required evidence. Proposal-to-AI entry without an existing analysis retains an unresolved consent-entry journey; the fix batch must not bypass consent to make that path appear complete.

**Browser execution is blocked in this session environment.** Root's browser discovery reported no browser from `cua.getBrowser`; `cua.getState` reported empty apps and browsers. No browser tab, desktop/mobile canvas, keyboard/touch, screen-reader, reflow or browser performance acceptance is claimed. Source review, evidence reconciliation and authorized implementation work can continue while browser provisioning remains unresolved. Screenshots or test assertions cannot be invented to substitute for that missing runtime.

All social write journeys remain **isolated local synthetic mechanics**. Before any write, bind a disposable local database, local endpoints and role accounts; disable outbound email/notifications and external side effects. Create recommendations through the normal contributor consent/review flow rather than seeding intervention records. No outreach or production mutation is authorized by this QA receipt. Synthetic execution cannot close C16's real-human requirement or production/training acceptance.

Live data readiness remains separately unresolved: independently read physical Parquet objects, completion markers and availability; bind exact release/source/reader identity; distinguish populated, governed absence, unavailable and source ceiling. Forecast admission, botanical profile publication, land-context source/reader admission, schedule burn-in and production acceptance retain their owning tracks and gates. Historical weather-unavailable and synthetic scalar-renderer receipts retain their original narrow scope.

## Ordered bounded sessions

1. **Intake and immediate correction batch:** reconcile current cases and ownership, preserve initial changes, apply bounded workspace/feed/GBIF feedback corrections, bind exact diff, then perform the appropriate integrated engineering sweep and independent source review. Browser-dependent verdicts remain pending.
2. **Capability and publication truth:** one layer at a time, freeze source/day/rungs/generation/schedule; compare objects, markers, availability and exact reader across populated/absence/missing/source-ceiling fixtures. Return bounded repair needs to the environmental/gapless owners.
3. **Weather, climate and soil selected-day rendering:** exercise sampled weather, nine climate signals and three soil signals, depth/statistic intersections, rungs, units, rapid day/place changes, cold/warm desktop/mobile and agent parity once executable.
4. **Other environmental layers:** fire/perimeters/MTBS/evacuation, gauges/drought/stations/watersheds, SSURGO, withheld soil raster and NDVI; verify cumulative/reference meaning, styles, opacity, support and neighbours.
5. **Botanical/GBIF:** verify immutable release/QC/taxonomy/support identity, resolve bbox tradeoff, then interval filters, source split, aggregate/detail handoffs, explicit empty/refusal states and per-record provenance.
6. **Land-context and forecast prerequisites:** return incomplete reference readers/admission to owners; accept point/parcel/area geometry and related routes only against admitted evidence. Forecast run/valid-time data and its mounted experience require their own completed intake before QA can establish populated behavior.
7. **Intervention/social and agent workspace:** provision isolated synthetic actors; exercise draft/drawing/consent/publish/reject/outcome, authorization, workspace state/recovery and UI/tool/final-answer parity; preserve the real-human and consent-entry gates.
8. **Integrated cross-layer QA and verdict:** combine heavy layers, mixed time, camera/style changes, offline saved dates, role transitions, accessibility and mobile; freeze the complete candidate, apply all fixes before the final integrated sweep, then obtain an independent evidence/browser verdict. Close only requirements actually proved and retain unresolved ownership and production gates.

Each session records its candidate and evidence before handing off. These are dependency-ordered batches, not a claim that a new runtime conversation or service has already been scheduled. Root may run independent bounded lanes in parallel while preserving shared-file ownership. A new candidate requires impact reconciliation of older receipts; repeated checks are justified by a changed candidate or failed gate, not by a per-fix test loop.

## Verification status

The [third bounded session](runbook-session3-20260914.md) adds navigation/drawing recovery,
the sensor incident evidence, remaining-stream physical samples and selected-day HTTP readbacks.
It preserves the distinction between indexed absence and source-proven absence and clarifies
the dormant voting contract. Candidate 6 and its separate receipts bind this changed surface.

The [second bounded session](runbook-session2-20260914.md) advances consent entry, historical
evidence recovery and fresh physical publication reconciliation. Its candidate 5 and separate
check/runtime/object receipts supersede the earlier candidate only for the newly changed surface;
all historical checks and failures below retain their original scope.

The initial documentation-author handoff preceded implementation checks and claimed no passes.
Root has since added the engineering checkpoint and exact check receipts above, with independent
source/hash verification. The [independent verdict](independent-review-20260914.md) distinguishes
bounded code approval from overall platform QA, which remains unaccepted. Browser, database
integration, production and the 219 case-ledger requirements retain their open gates; this session
does not authorize GREEN, archive or runbook closure.
