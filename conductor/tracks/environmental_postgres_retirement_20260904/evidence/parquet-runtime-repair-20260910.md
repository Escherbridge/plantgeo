---
type: evidence
---

# Parquet runtime repair — September 10, 2026

Scope: Aevani project `6faaf3ea-ac46-4c8b-bbfe-1351dbb9d990`, production
`b7cfa813-8a5c-4fcd-80f2-cab736d840a7`. Environmental data remains on Parquet;
PostgreSQL may be inspected for operational job metadata only.

## Observed production state

Read-only Railway status confirmed main deployment `c9719f3a-d159-4205-9567-a1e94009d08e`,
Parquet API `4a0f8ccb-437f-4b28-9673-42534e11cd97`, and executor
`7e851cf5-6174-43cd-8262-1d14bfbe7ac1` all successful. An existing staged patch
`98e5fac7-404e-4894-85aa-9e6ace33ab5b` contains 156 changes; this repair does not accept it.

Read-only executor SSH used `settings.require_object_store()` and the configured empty prefix.
For each `climate-field-air-temperature-{mean,min,max}/kind=observed` stream:

- `availability/_LATEST.json`: HEAD missing.
- `availability/bootstrap/_BOOTSTRAPPED.json`: HEAD missing.
- Full stream prefix listing: zero keys, not truncated.

Control listings for `climate-field-dew-point` and `sensors` in that same bucket returned
existing availability heads, bootstrap markers, receipts, and evidence. The temperature issue
therefore includes missing physical ladders, not merely missing availability metadata. Do not
publish an empty bootstrap as a substitute for data. Source-direct data publication and a verified
four-rung census must precede generation-zero bootstrap.

Drought's September 1 recorded release legitimately carries through September 10. The September 3
source ceiling bounds the recorded date; the carried readable edge is not a ceiling violation.

## Source quota and budget handling

NASA POWER HTTP 429, Open-Meteo quota refusal, and exhausted per-turn request budgets previously
became generic adapter failures. Repeated publication attempts reused the already exhausted cache.
The patch gives these conditions typed provider deferrals, propagates the existing source-unsettled
outcome, stops queued requests after quota refusal, and retains successful in-flight responses.
Request accounting charges started work rather than queued work that never began.

Verified, entirely non-fill source responses now have bounded persistent checkpoints outside the
serving prefixes. The checkpoint retains the original response bytes, digest, retrieval time,
request identity and complete ordered support fingerprint, and reparses those bytes when resumed.
Seven-day expiry, size limits and compare-and-swap prevent stale or conflicting reuse. Restoration
precedes request-budget checks. Mixed/fill-bearing responses remain process-local; cross-turn
provider cooldown and expired-object cleanup are still outstanding. No partial day or invented
absence is published. Transport/malformed-response/publication failures retain their existing
bounded failure behavior.

## Signal and archive blockers

Original signal attempt logs at `2026-09-09T13:27:09.001763358Z` establish that August 6 z13
lacks `cell_longitude` and `cell_latitude`, preventing derived rungs (`ladder_unrepairable=1`).
The same event reports August 31 availability not bootstrapped. Structured log attributes carry
these facts even where the event's message string is empty.

Existing signal rewrite tooling retracts stale-schema days and expects PostgreSQL re-export.
Do not execute it under the Parquet-only requirement. Canonical Parquet snapshots contain a possible
coordinate witness keyed by `cell_id`; any correction must pin that witness, prove unique coordinate
mapping and preservation of all other values, and publish the complete ladder with governed evidence.

Streamflow archive shard `streamflow-archive:2025-10-18..2025-11-17` failed with
`UpstreamHttpError` on attempt 9/9 at `2026-09-10T07:02:55.960807020Z`. A subsequent explicitly
read-only operational-ledger query established HTTP 503 on attempts 6, 7, 8, and 9, ending at
`2026-09-10 07:02:55.790369+00:00`. The endpoint is not in the stored error summary.
Its active archive implementation still writes environmental data to
PostgreSQL before projection, so resuming it is not a Parquet-only repair. Forward water gauges
are independently current. A source-direct archive path remains required.

## Recovery discipline

Sensors September 5/6 poll rows conflict with governed absence markers; the adapter correctly
refuses the merge. See `sensors-and-availability-repair-20260910.md` for evidence and correction
requirements. The availability extension patch preserves repair claims if a bootstrap marker
survives a lost or malformed head; it does not manufacture missing histories.

No raw ledger edits, blind requeues, absence retractions, bootstrap publications, or scheduler
supersessions have been performed as of the initial historical materialization below. A supported `ops jobs-supersede-run`
receipt requires a verified fix and exact latest failed run, evidence, and operator identity.
Validation and deployment results follow.

## Database-free historical source and first materialization

The user directed this task to avoid local databases, reuse existing Parquet, and retain provenance,
gap filling and data quality checks. The temporary local validation container was stopped and removed
immediately; its database-backed sweep was interrupted. Do not recreate a local database for this task.

The bucket contains `raw-canonical/signal-observation/snapshot=prod-20260826-full-signal-v1/`:
46,146,568 rows, 8,364 partitions, and completion manifest hash
`465abc4e813bf28c78acd7f97a4da9d19ad959e525de3eb1f422ca2f6e73e94f`.
The existing frozen monthly temperature products also exist with exactly the three hashes pinned
by `scripts/build_nasa_power_from_canonical_snapshot.py`. Thus no new PostgreSQL dump is needed for
their April 30, 2022 through August 6, 2026 historical window. Empty live day prefixes did not mean
the frozen historical source was lost.

The existing builder was transferred to executor temporary storage and checked against file SHA-256
`d99cb59f527960153969b0b286ba3ea9eca6663527231fd3281088808c5505f1`. Dry runs of `T2M`,
`T2M_MIN`, and `T2M_MAX` each verified a 397-cell base and the four required rungs with zero writes.
Mean-temperature April 30 was then published (eight objects) and independently audited on the next
bounded pass. That pass published another 120 days through August 28, 2022 (960 objects), verified
121 complete ladders, and found no physical-audit pollution at the serving prefix. Further bounded
passes completed the historical materialization described below; physical completion alone is not
a claim that the whole history is serving.

The builder performs no environmental PostgreSQL query. Availability bootstrap remains a separate
gate and uses the existing operational advisory publication lock, never an environmental table export.

## Verification and preserved evidence

Database-free focused verification: 188 tests passed across direct climate, direct soil, and
availability extension. Full-service format and mypy passed; Ruff found one long error-message line,
which was split and the full-service lint rerun passed. The initial database-configured sweep was
interrupted on the user's resource instruction. Subsequent inspection of `tests/conftest.py` confirmed
that the existing harness explicitly permits and reports database-dependent skips when
`AGRI_TEST_DATABASE_URL` is unset. A final full sweep can therefore run in that supported mode with
explicit skip disclosure; no test policy or Docker receipt gate needs to be changed. Until that sweep
and a verified deployment, do not describe the code patch as deployed.

The user explicitly approved preserving the sensor capture locally. It is stored as the ignored
`.omc/research/sensors-rescue-20260910.tar.gz` (2,289,729 compressed bytes; 113,142,634 original
bytes), containing publication receipts and 600 bounded station responses for September 5–6.
Pagination and roster limitations are retained; the capture does not assert source completeness or
prove an absence. The earlier automatic export rejection was resolved by the user's explicit approval.

## Completed historical materialization and serving gate

All three temperature builders exited successfully after bounded resumable passes. Each independently
verified 1,560 complete days from 2022-04-30 through 2026-08-06, zero days owed inside that pinned
window, 1,560 parts plus 1,560 completion markers at each z13/z09/z05/z00 rung, and zero physical-audit
objects at the live prefix. An existing first day was rederived and its base digest agreed.
The canonical source and frozen product hashes above remain unchanged.

Local final evidence is `.omc/research/temperature-mean-finalpass-20260910.log`,
`.omc/research/temperature-min-final-summary-20260910.log`, and
`.omc/research/temperature-max-final-verification-20260910.log`. No environmental database was read.

Availability compilation initially refused the trio because the serving snapshot catalogue still
excluded them from the time-bearing census. A durable graduation keeps the frozen descriptors as
provenance and moves serving to registered live streams. Availability must be compiled with the
current settled source ceiling and a 2,000-day digest window, preserving the unfilled forward interval
as owed gaps. Compilation and a successful bootstrap remain mandatory before release.

The first full database-free code sweep passed format and mypy but refused a receipt: one import-order
finding, three test failures, and 463 setup errors sharing an inaccessible Windows pytest temporary
directory. The failures were a request-URL fixture mismatch and two missing fake availability-storage
injections. Corrections retain strict assertions; the next sweep uses a task-owned temporary path.
No test requirement was removed and no database-dependent pass is claimed.

## Final source validation

The corrected full sweep completed successfully in the supported no-database mode on
2026-09-10 at 12:10:57 UTC. Format, lint, mypy and pytest all passed. Database-dependent tests retain
the harness's explicit unconfigured-database skips; no PostgreSQL validation is claimed.
The final invocation used a fresh task-owned Windows temporary directory outside the repository
and allowed the existing wheel-packaging subprocess. No test policy was weakened.

`QUALITY_RECEIPT.json` binds 1,275 files to
`sha256:fab78f8618fcb9974cfbec694e4b09922992564e64e17b5b3cd41b19570b8cbc`.
The complete summary is `.omc/research/python-final-nodb-sweep3-20260910.log`.
Provider checkpoints, availability recovery, gauge trends, temperature graduation, and the resulting
fixture updates received independent review before this sweep.

The isolated availability compiler catalogue was verified against
`daaa83ca728eab2d5481e16d260521971819c48f3cca29167d507b338ce3557b` on the existing executor.
The unchanged compiler hash is
`e7eca5e8ee5f8578083edf952bff7b6de474e6cef2fea5a2e31711c02d922016`.
An initial automatic-review refusal was resolved with verified Railway deployment identity and
proof that the operation copied existing source only inside that service's temporary directory.
The reviewed retry exited zero; installed runtime modules were not changed.
