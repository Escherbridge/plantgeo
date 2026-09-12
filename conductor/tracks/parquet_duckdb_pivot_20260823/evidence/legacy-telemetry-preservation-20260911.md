---
type: historical-evidence-preservation
date: 2026-09-11
status: pending-independent-review
---

# Preserved September 10 recovery and telemetry record

This document recovers historical facts omitted from the integration comparison
commit `cc64e6e3e1de8dda164beeee0e7e22aa30088b76`. It is a proposed documentation
resolution for the [legacy reconciliation](legacy-candidate-reconciliation-20260911.md),
pending independent review. It records what the retained Git document reported;
no deployment, request, transfer, rollback or production check was repeated here.

The immutable source is commit `3a5f3902e6f56b0878eab1d72b34789f6653058f`, path
`conductor/tracks/environmental_postgres_retirement_20260904/evidence/repair-preparation-20260910.md`,
Git blob `9506a3748087cbbceef7a30bf2ef5ff3e3ca01ff`: 20,720 bytes with SHA-256
`aa6696f4fae0fffeeeebc37ef0fb8f8bf74cec5861d930142a0ce5a1258d5538`.
The source's added recovery and telemetry paragraphs are attributed to its
September 10 history. Their linked raw captures were not independently reread
or transferred by this reconciliation.

## Sensor preparation and its current successor

The historical record reports an approved transfer of the 2,289,729-byte sensor
archive, SHA-256 `eb3ca823a0a3319cc42e47c4066893cfd88efd58979bfb1e177949d9eb70537e`,
and reproduction of five candidate/audit files with matching hashes and manifest.
Only that archive and two reviewed scripts were transferred. An initial attempt
refused before creating its temporary directory because the final image omitted
operator scripts; the corrected driver checked deployed source identity and its
quality receipt before preparation.

Request `2e18b2c8ef7c8ed6a781d91b048c6d5080fc73f0a1b12e2e7d431d90c06b0421`
was prepared and independently reviewed in place against eight absence identities
and both days' four-rung candidate/ledger graph. That review emitted bounded
booleans, counts and dates without bucket/database requests or remote writes.
A broader metadata download had been rejected and was not retried. The record
states that no correction was applied or published: September 5's 3,228 rows and
September 6's 2,707 rows remained partial candidates, subject to fresh writer
quiescence, locked physical/generation checks and exact application scope.

The accepted [September 11 sensor revalidation](../../environmental_postgres_retirement_20260904/evidence/signal-sensor-candidate-revalidation-20260911.md#sensor-source-revalidation-and-retained-conflict)
is the current successor. It independently preserves the archive and separate
candidate files, reproduces all 5,935 rows, explicitly corrects the older
not-yet-prepared statement and keeps population completeness and admission open.
It is not superseded by this historical transcription.

## Recovery deployment reported in the historical source

The source reports that the reviewed Python recovery patch was integrated onto
`24515bf` and released as `0ae1528ed4655fcd198966877b91abdf9c472f31`, preserving
that production base's frontend, package files and root Dockerfile. It records:

| Service | Reported successful deployment |
| --- | --- |
| Main application | `b4c9eec8-03fc-4912-b0cb-1dbf61825e19` |
| Parquet API | `81d95bfb-f1b3-467d-9e12-61ebf092a631` |
| Executor | `90251911-636a-4836-9a49-6affc6f2fbd7` |

Both Python images reportedly verified the same 1,287-file quality receipt.
`recovery-app-build-gates-20260910.json` was cited for 138 passing test files,
two skipped files, 2,084 passing tests, 13 skipped tests, a 109.71-second test run
and successful compilation/static generation. These are historical counts for
that source, not validation of the current integration candidate.

The coordinating QA record reported four compatibility checks without retries:
eight exact Washington burn-history identifiers with matching scalars/geometry,
the weather source contract, and August 31 soil moisture 0.138 and temperature
17.6. It did not recertify every weather value or resolve cold-read reliability.
Deployment success did not apply the pending signal or sensor repair.

## Approved bounded telemetry diagnostic and deactivation

The source records opt-in serving telemetry deployed in `0ae1528`, with elapsed,
worker-thread CPU and whole-process CPU measurements. HTTP transfer counters
were unavailable. The 14-second route deadline, admission/memory bounds and
exact clipping remained unchanged; no cache, timeout or layout remedy was
accepted. A plan allowed four sequential requests in ten minutes, using API-only
activation and deactivation. An initial activation request was rejected before
mutation because logging side effects were outside that approval. The source
then records separate approval and completion of the bounded diagnostic.

Activation was recorded at 19:26:35 UTC on September 10, using the same tested
source `0ae1528`. API deployment `df02647b-6db6-456f-8626-7028bc51b45c` became
ready at 19:27:36.057 UTC. Four sequential release requests returned HTTP 200 in
one bounded northern Washington viewport: the first two selected cohort
2024-08-22, the last two cohort 2023-08-09. There was no retry or fifth request.

| Request | HTTP elapsed s | Rows | Worker elapsed s | Data scan elapsed s | Scan worker-thread CPU s |
| --- | ---: | ---: | ---: | ---: | ---: |
| 1 | 2.168168 | 0 | 1.965726 | 0.649398 | 0.102484 |
| 2 | 2.486373 | 0 | 2.482819 | 1.600209 | 1.357027 |
| 3 | 1.022195 | 4 | 1.005689 | 0.471662 | 0.198915 |
| 4 | 8.686929 | 4 | 8.672994 | 8.257521 | 0.035078 |

Four structured stage events reported completed workers without cancellation.
Their random read identifiers were not HTTP correlation IDs; non-overlapping
event windows supported the table's association. The slowest scan used 0.061950
seconds of whole-process CPU over 8.257521 seconds elapsed. This indicated time
spent waiting in that sample, without identifying the cause or proving a cold-cache
guarantee. The earlier 14-second failure was not reproduced and no reliability
or timeout fix was certified.

The flag was set back to `false` at 19:33:05 UTC. Replacement API deployment
`32382dc3-80f8-4ba6-b633-ba52e8fe72cf` was ready at 19:34:26.837 UTC on the same
source. The recorded configuration check verified `false`; an inspection with no
matching process flags did not inspect worker memory. All four enabled workers
had finished before deactivation, and no extra diagnostic request was made.

The unrelated pending patch `98e5fac7-404e-4894-85aa-9e6ace33ab5b` was neither
amended nor committed. Its computed difference grew from 156 to 157 because its
pre-existing staged snapshot lacked the newly created live `PARQUET_READ_TELEMETRY`
variable. The additional displayed difference was removal of that API variable
from the staged snapshot; the original 156 differences remained identical after
normalizing response ordering. The whole displayed payload was not byte-identical.

The source names `telemetry-lifecycle-completed-20260910.json`,
`telemetry-serving-stage-events-20260910.json`,
`telemetry-rollback-serving-process-proof-20260910.log`,
`telemetry-disabled-verification-20260910.log` and
`parquet-read-telemetry-rollout-plan-20260910.md` under its original
`.omc/research/` custody. Those names are provenance pointers, not newly verified
local-custody or current production-acceptance claims.
