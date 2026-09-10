---
type: track-evidence
status: preparation-only
---

# Repair preparation evidence — September 10, 2026

This records completed investigation, candidate preparation and bounded source
validation. It does not mark signal or sensor repairs published, full-history gap
filling complete, or the active retirement track finished. Signal and sensor
preparation used existing bucket objects and saved source evidence, without a
local database or environmental-observation database queries.

## Signal: bounded schema census and verified coordinate candidates

The saved census covers April 30, 2022 through August 6, 2026. It identifies
1,338 current-schema and 222 legacy-schema base partitions; the legacy interval
is December 28, 2025 through August 6, 2026. This is a schema/footer census, not
all-value or all-rung publication verification. It records 25 absences outside
the inspected window. Measured cost: 3,120 GET requests, 8 LIST pages, 4,277,912
range bytes and 60.7 seconds.

The compressed census SHA-256 is
`5a99265c9ed29ad2d056b0dee9445844560fd0f4bb6fbcc63386a49cb9c7d64c`
(232,444 bytes). See the original
[transfer receipt](../../../../.omc/research/signal-evidence-transfer-receipt-20260910.json)
and [compressed census](../../../../.omc/research/signal-base-schema-census-20260910.json.gz).

The August 6 preview contains 3,970 base rows across 397 cells. The original ten
columns have identical logical Arrow IPC hashes before and after coordinate
enrichment:
`58565a87c06663c86f9a603f9edc44e2ebf0459fc5949fd237ca4c2626ee5131`.
The candidate adds coordinate witnesses; it does not replace original measured
values. Candidate z13 Parquet SHA-256:
`ed069feb1c1fcf74c2e3bf7266dd96287a0c00742932c7a5dca41c634ab5edb7`.
The preview also contains derived z9/z5/z0 candidates, not published replacements.

The [preview evidence archive](../../../../.omc/research/signal-coordinate-preview-evidence-20260910.tar.gz)
has SHA-256
`29731e682bf11f32ec794ccd0466b42773bce99d2780ef8068981862aaa76fa6`
(155,299 bytes). Its manifest is the content-addressed member
`objects/6d733f473effa3f5d7d4b9d3444a7b52e8adcba8d4358826f48dfbca1a9a9d37`.
It explicitly records apply_authorized=false, no publication locks and no bucket
writes. Its observed missing availability pointer/bootstrap require fresh locked
revalidation before any future repair; they are not current-state guarantees.

The subsequent bounded preparation completed all 222 legacy days, preserving
3,506,555 original rows and all ten original columns. It produced 888 candidate
files across the four zoom levels with zero invalid coordinates or original-column
preservation failures. It used 450 GETs, 444 HEADs and 1,776 LIST requests,
read 18,614,634 source bytes and retained 93,327,293 artifact bytes on the executor.
The task-local preparer SHA-256 is
`6d10b5783f5b95e8dea89c707cd473ddf65372ea5e523432a602712b8e896b3f`.

Independent verification read only those executor-local artifacts, with no bucket
or database requests. It compared every original column and IPC hash, checked all
888 artifact hashes, recomputed all four rungs with the ordinary deriver, and
matched coordinates to the independently decoded 1,965-cell EWKB witness. The
5,501,822-byte `batch-summary.json` has SHA-256
`26fd5219f481bd50d2ba8067b28cf2109e76e72dc43b2941f5905d8fdfea2e57`.
See the [preparation log](../../../../.omc/research/signal-coordinate-batch-run-20260910.log)
and [independent per-day receipt](../../../../.omc/research/signal-coordinate-artifact-verification-20260910.json).
These are prepared candidates, not published replacements or availability coverage.

The same executor now also holds a verified compressed archive of those artifacts:
`/tmp/plantgeo-signal-coordinate-batch-20260910-v1/signal-coordinate-artifacts-20260910.tar.gz`,
54,850,520 bytes with SHA-256
`4ca8a36083474d55426d8032275306198af5e30349c22407bc7f13bb6f768124`.
Its 1,555 regular members total 65,008,613 original bytes; the archive operation
checked every member and the final source inventory. The [archive receipt](../../../../.omc/research/signal-coordinate-archive-20260910.json)
records no bucket/database calls and no archive transfer. This remains temporary
executor storage, not durable preservation across a redeployment. Preserve it
before replacing that executor; the original bucket partitions remain unchanged.

## Sensors: independently verified positive candidates, incomplete source

The independent review matched every candidate row to captured raw JSON without
reusing the projection builder. All 5,935 rows across 1,030 positive station-day
blocks preserve the winning report's values, units, quality flags, timestamp,
publisher-named date, identity and full-precision pinned roster coordinates.
Missing measurements were not filled from older reports or defaults.

| Publisher day | Rows | Positive station-day blocks | Candidate Parquet SHA-256 |
| --- | ---: | ---: | --- |
| 2026-09-05 | 3,228 | 516 | `457eb9fe150cbd1e786b9b9b3124f4551760f283fee64137893d2b689840848f` |
| 2026-09-06 | 2,707 | 514 | `ecf53fcdf09e95174a23b76ad4e7488bc3af4b44b7fe93c0f7cbb46e98d06929` |

Original archive SHA-256:
`eb3ca823a0a3319cc42e47c4066893cfd88efd58979bfb1e177949d9eb70537e`.
Candidate manifest SHA-256:
`e99bce200991ea1b57ef4c2e60a6c36598c992c4d7ad27e700962c528cf8dd40`.

Of 600 saved response receipts, 524 were accepted partial positive pages and 76
were rejected capture responses; 516 next links were not followed. These are
captured-evidence counts, not proof of a complete station universe or day. The
capture preserved decoded response text re-encoded as UTF-8, not original wire
bytes; its fetched_at is original request start. Original QC codes remain
unchanged: 5,383 `V`, 479 `S` and 73 `C` rows, without interpreting those codes
or upgrading their quality status. Source and upstream-population completeness remain false.

See the original [independent review](../../../../.omc/research/sensors-candidate-independent-evidence-20260910.md),
[machine receipt](../../../../.omc/research/sensors-candidate-independent-evidence-20260910.json)
and [candidate manifest](../../../../.omc/research/sensors-positive-candidates-20260910/candidate-manifest.json).
Positive incremental publication is compatible with this limited source scope.
Replacing the false governed absences still requires a reviewed correction,
current-state pins, ownership, durable audit and final physical/availability
checks. The [orchestration review refinements](../../../../.omc/research/sensors-correction-review-refinements-20260910.md)
remain relevant; this note does not certify a correction implementation or apply.

The correction operator subsequently passed independent code review for its exact
September 5/6 scope. It requires a pinned prepared request and fresh external
writer/retry-worker quiescence evidence, archives originals before mutation, uses
the ordinary writer and finalizer, and verifies exact physical and availability
evidence during resume and completion. The coordinating validation run reported
format, lint and mypy passing. Full pytest recorded 5,880 passed, 149 skipped and
one expected failure; the existing wheel-packaging fixture failed with a sandbox
process `PermissionError`. That fixture then passed in an isolated escalated run
in 2.71 seconds, as recorded in the
[wheel environment check](../../../../.omc/research/repair-wheel-environment-check-20260910.log).
The isolated run also retained a pytest-cache permission warning. These are
combined validation results, not a single clean integrated run or database test
coverage. A subsequent full integrated run after telemetry implementation and
final lint corrections passed all four gates and wrote `QUALITY_RECEIPT.json`:
`sha256:8f8690903c5b19b19e6cf4e4e77e64fd1abc1af9bb9531ab3786516064a6f491`
over 1,287 files. The [final gate log](../../../../.omc/research/repair-release-integrated-gate-20260910.log)
records format 0.22 seconds, lint 0.19 seconds, mypy 1.70 seconds and pytest
168.75 seconds. Its summary records pytest passing without a case count. All
test database settings were absent; this does not certify database integration.

No prepare or apply has run against production. The local rescue
archive transfer back to the executor remains awaiting explicit user approval
following automatic approval review's rejection of that sensitive transfer.

## Soil reader: deployed and checked against source

Commit `5e265c79c4146a32707e952dcfada1e9571fbe82` repairs the reader's unconditional
frozen-manifest predicate. Historical soil rows retain their canonical pin;
direct writer rows are accepted only from their August 3 ownership boundary,
with the appropriate base/coarse lineage fields and existing product checks.
Independent review confirmed the producer and aggregation contracts. QA integrated
the fix as `24515bf99ffbf83487742be98dba1e22034add91`; Railway deployment
`d8a54788-1234-4d44-bac9-c7b5d2154729` reported success at 15:51:29 UTC.
QA subsequently compared all six August 31 soil responses against source and
accepted the decoder repair. The [saved live comparison receipt](../../../../.omc/research/soil-live-acceptance-24515bf-20260910.json)
records HTTP 200, exact source values, the selected day and one registered sample
per response for moisture and temperature at the three sampled regions.

The [related-test log](../../../../.omc/research/soil-reader-related-tests-20260910.log)
records 268 related tests passing followed by 207 contract tests passing and 13
database-dependent skips. Combined duration was 21.07 seconds. The coordinating
validation pass also reported types, data-boundary checks and touched-file lint
passing. Skips are not database coverage; no local database was created. This
source validation is distinct from QA's subsequent six live source comparisons. The original
[diagnosis](../../../../.omc/research/soil-direct-reader-contract-20260910.md)
remains preserved as the pre-fix account.

## MTBS serving: measured cold scan, reliability still open

The completed [bounded cold-read profile](../../../../.omc/research/parquet-mtbs-cold-read-profile-20260910.md)
examined the August 22, 2024 burn-severity release at z13 for the sampled northern
Washington viewport. Two sequential requests to the existing serving process
returned HTTP 200 and zero rows in 6.619 and 0.878 seconds. Neither reproduced the
earlier 14-second route failure. A separate process in the same container measured
the first data/geometry scan at 23.191672 seconds, compared with 0.251149 seconds
on repeat; session opening and listing were substantially smaller stages. The
separate profiler's longer process bound did not change the live route deadline.

The two exact-day parts contain 113 rows and total 7,449,077 bytes; compressed
geometry accounts for 7,425,265 bytes, about 99.68%. Both files lack GeoParquet
`geo` metadata and per-file geographic bounds. Exact intersection and clipping
can therefore scan the geometry column even when the viewport result is empty;
the inspected metadata does not justify a disjoint-file shortcut.

The measured cold bottleneck is the data/geometry scan. Its division between
network transfer/retries and decompression or geometry CPU remains unproven:
the slow scan did not collect CPU or transfer counters, and later CPU measurements
were warm. The profile did not implement a behavior, cache or timeout fix.
The opt-in serving-stage telemetry implementation passed independent review and
the full integrated gate. It remains off by default and has not been deployed or
enabled. It records elapsed, worker-thread CPU and whole-process CPU with explicit
scope labels; HTTP transfer counters are unavailable, not estimated. The existing
14-second route deadline, admission/memory limits and exact clipping remain
unchanged. Cold-request reliability remains open; neither candidate layout
changes nor a cache/timeout remedy is accepted here.

## Limited fidelity follow-ups

- [Weather original-response receipts](../../../../.omc/research/weather-original-receipts-proposal-20260910.md)
  remain a future design: retain pre-decoding bytes, actual response receipt time,
  source units/grid coordinates and winning normalized-row linkage within hard
  acquisition/storage budgets. No retrospective original-response provenance may
  be invented for existing rows.
- NDVI `release_count`, currently mapped to contributorCount in the reader, is
  a release/candidate registration count, not an observation or measured-pixel
  count. The direct producer records candidate count while selecting one clearest
  report (`pipeline/direct/vegetation/source.py::_select_clearest`). The inspected
  UI does not display contributorCount. Additive metadata distinguishing known
  release counts from unknown observation counts remains pending; do not invent
  a count or relabel releases as observations. This is not a claim that every
  historical registration count has the same semantics as direct candidates.

The original evidence remains in place. Local .omc artifacts are ignored working
evidence and are not implicitly bundled by this tracked summary; hashes above
pin the inspected artifacts. The separate
[completed-slices retrospective](../../../retros/parquet_cutover_completed_slices_20260910/README.md)
and its [preserved cleanup proof](../../../retros/parquet_cutover_completed_slices_20260910/cleanup-proof.md)
remain historical references. Neither archives nor closes this active track.
