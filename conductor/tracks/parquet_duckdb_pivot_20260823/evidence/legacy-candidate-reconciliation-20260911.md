---
type: integration-reconciliation
date: 2026-09-11
status: hold
---

# Legacy candidate reconciliation against the preserved integration commit

**Verdict at the comparison commit: HOLD.** Every one of the legacy candidate's
139 net changed paths is classified in the [per-file receipt](legacy-candidate-reconciliation-20260911.json):
90 present, five explicitly superseded and 44 with at least one missing change.
The missing set contains 43 paths from the final cooldown/climate work and one
historical evidence document. A missing disposition does not mean that the
entire file is absent; several files also contain preserved MTBS or advisor work
and later accepted lane changes.

This comparison is fixed to immutable Git objects:

| Coordinate | Commit |
| --- | --- |
| Actual merge base | `0ae1528ed4655fcd198966877b91abdf9c472f31` |
| Legacy candidate | `3a5f3902e6f56b0878eab1d72b34789f6653058f` |
| Preserved integration candidate | `cc64e6e3e1de8dda164beeee0e7e22aa30088b76` |

The legacy candidate is not an ancestor of the integration candidate. The JSON
records each path's mode and blob at all three coordinates, its contributing
legacy commits, mechanical comparison, behavior and disposition. It also pins
the supporting accepted-lane intake receipts. The union of paths touched by all
five legacy-only commits is also exactly these 139 paths; no changed-then-reverted
path is omitted by using the net manifest. Working-tree repairs made after
this comparison are excluded and require a separate reviewed resolution receipt.
No tests, runtime edits, Git commit, legacy merge, production action or task
archiving were performed by this reconciliation lane.

## Complete legacy-only history and equivalent current work

| Legacy-only commit | Reconciliation |
| --- | --- |
| `c342f1060af74d8c5b8b3d9ac0ac77600d6b5634` | Sensor preparation provenance is superseded by the accepted September 11 revalidation where it overlaps. The net old recovery document also omits deployment/telemetry history, recovered separately below. |
| `b840c6a2c48eb896678deec0c7a0c04aa33e6f3b` | Private advisor history, sharing, feedback and saved-report replay are present via `ac25a9d`. Both commits have stable patch ID `e15997ed3cb9ddcdcdb311b60474ca3e342f3340`. Later reader changes preserve those behaviors. |
| `2a4333716a7018e6393c41a190d5f7254fbd2404` | Current MTBS snapshot maintenance and retired reader removal are present via `3632d61`. Comparing the complete trees of those commits finds exactly one differing path: the old recovery evidence document. All runtime, test, migration and other files match. |
| `35624ddbaf93670b839333ae51d7ef15e1f5a85e` | The provisional PNW source/occurrence proposal is superseded by the accepted parent botanical planning and source-admission split, integrated through `bc7b5e1`. |
| `3a5f3902e6f56b0878eab1d72b34789f6653058f` | Of its 45 changed paths, 43 still have missing changes at the comparison commit. Its weighted-mean test and source-specific quality receipt have explicit successors. |

Seventy-two file blobs and their modes match the legacy tree exactly; both
legacy deletions remain deleted. Of the other 65 paths, eight are absent,
23 remain at merge-base bytes and 34 differ from both old snapshots. The
behavioral classification below separates accepted evolution from omissions.

## Preserved and superseded behaviors

The private advisor slice preserves the feedback migration and matching migration
contract, ownership-scoped history, persistence, sharing/copy controls, feedback,
faithful saved report replay and associated tests. The current reader adds request
cancellation and more precise observation language without replacing that slice.

The MTBS slice preserves bounded source capture and staging, snapshot descriptor
and evidence validation, ordinary four-rung publication, D+1 snapshot ownership,
release eligibility, cumulative snapshot reads, executor definitions and pause
controls, web wire metadata and deleted unused readers. Accepted changes are:

- `fa20223` permits the canonical base-z13 schema-v1 completion shape without
  embedded part digests while retaining typed indexed receipts, counts and run
  identity. Derived or explicit-part markers still require exact digests. Its
  catalog tests supersede the older fixture and enforce malformed marker refusal.
- [Gapless intake](gapless-intake-review-20260911.json), integrated as `b6eb3ae`,
  retains MTBS scheduling and fatal pinned-database handling while requiring
  confirmed child exit before cleanup/retry and propagating fatal aborts.
- [Retirement intake](environmental-intake-review-20260911.md), integrated as
  `a166a8f`, retains current MTBS preparation while allowing its existing DuckDB
  connection to flow through ordinary derivation. Its older-capture preparation
  remains separate from current-snapshot publication.
- [Reader intake](reader-intake-review-20260911.json), integrated as `4e9a731`
  and `0f31262`, preserves snapshot metadata and requested/as-of semantics while
  strengthening calendar validation, governed-absence handling, cancellation,
  truncation disclosure and separately labelled bounded temporal neighbours.
- Accepted conformity, retirement and offline documentation is additive to the
  legacy operator/MTBS guidance. The CLI correction from four to five groups
  documents the already-existing `agent` group; it does not remove an operator.

The legacy four-ULP weighted-mean allowance in
`services/agri-data-service/tests/test_agent_parquet_reads.py` is superseded by
the reader's eight-ULP allowance for ten weighted contributions, with a dedicated
control rejecting changed means, observation counts and extrema. Restoring the
older assertion would undo accepted evidence.

The legacy `QUALITY_RECEIPT.json` binds a different source domain. It is not a
combined-tree proof and must not be copied into the final candidate. The accepted
gapless receipt is also scoped to its source; integration still owes its own full
Python receipt after the final source freeze.

The missing preliminary `conductor/research/pnw-herbaria-source-viability-20260910.md`
is superseded as a plan by the accepted [source-admission verdict](../../pnw_herbaria_source_admission_20260911/evidence/independent-review.md),
[exact collection decisions](../../pnw_herbaria_source_admission_20260911/evidence/admission-decisions.json),
[occurrence data-plane contract](../../botanical_occurrence_parquet_lane_20260911/spec.md),
[occurrence experience contract](../../botanical_occurrence_experience_20260911/spec.md)
and [separate profile contract](../../botanical_species_profile_lookup_20260911/spec.md).
These retain collection/release identity, raw revisions, rights, event-time and
spatial-uncertainty semantics, bounded aggregate products and agent honesty while
replacing provisional source counts and transport assumptions with the newer
metadata findings. WTU and UBC occurrence releases remain blocked; no botanical
runtime is admitted into this environmental candidate.

## Missing behavior requiring selective owner repair

| Behavior | Missing contract at `cc64e6e` | Exact surface is in the JSON |
| --- | --- | --- |
| NASA POWER provider backpressure | Bounded `Retry-After`; one charged transport attempt; paced request starts; durable monotonic cooldown; queue stop after provider refusal; separate 401/403, 429, request/time budget and unsettled outcomes; truthful partial/day counts. | `ingest/http.py`, direct outcome vocabulary, `direct/climate/{adapter,cooldown,forward,source}.py`, their guidance and regression tests. |
| MTBS provider refusal | Access/throttle errors must not trigger page shrinking or nested source retries. Long valid provider cooldowns must not be shortened. Preserve bounded failure HTTP diagnostics and typed terminal reports. | `ingest/mtbs.py`, `direct/burn_severity/{adapter,capture,forward,source}.py`, guidance and provider-refusal tests. |
| Climate viewport coverage | Read adjacent support/contour samples, count footprints overlapping the actual viewport, exclude the halo from displayed counts, and distinguish published empty viewport from insufficient contour neighbours or unpublished data. | `climate-viewport.ts`, climate reader/composer, details panel and their tests. |
| Climate legend and hover | Share climate shape IDs; expose cell values versus interpolated color ranges honestly; make active-row legends available to pointer, keyboard and touch users; label selected display settings. | Shared legend block, layer row/legend, climate layer IDs, hover formatters, captions and tests. |
| Throttle audit provenance | Retain the legacy dated audit and its limits; update current ownership/validation statements rather than claiming its old checks validate the combined candidate. | Retirement audit evidence and plan addition from the legacy tip. |

The repair must preserve the reader's selected-day, abort, truncation and response
identity checks; the renderer's invalid-form refusal and retired point-layer
cleanup; and retirement's broadened source-capture/connection seams. Copying
whole legacy versions of shared files would regress those accepted changes.
The old quality receipt and superseded weighted-mean test are excluded from the
repair. The root has assigned one executor to compose only the missing behavior,
with independent review and the final combined verification still pending.

## Recovered historical document and closeout gate

The legacy recovery evidence document is unchanged from the merge base at
`cc64e6e`, despite the old branch's newer sensor, deployment and telemetry record.
The accepted [sensor successor](../../environmental_postgres_retirement_20260904/evidence/signal-sensor-candidate-revalidation-20260911.md#sensor-source-revalidation-and-retained-conflict)
already corrects preparation and custody. The remaining dated deployment and
diagnostic lifecycle is preserved in
[legacy telemetry preservation](legacy-telemetry-preservation-20260911.md),
bound to the original commit, complete source blob hash and original measurement
limits. This is a proposed documentation resolution pending independent review;
the original comparison disposition stays `missing` for auditability.

Pinned QA/repair tasks are not safe to archive on the strength of this receipt.
The missing implementation, its tests and documentation must be selectively
restored or otherwise resolved with evidence, independently reviewed and included
in the frozen combined tree before the final quality sweep and any scoped track
closure. The receipt does not authorize or certify deployment, data publication,
service or relation removal, or current production recovery.
