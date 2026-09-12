---
type: implementation-evidence
track: weather_forecast_parquet_lane_20260911
recorded_on: 2026-09-12
status: locally_verified_fixture_only
review_base: 0ee4f5bd99666760a5881ed1427301944dc7b001
---

# Isolated fixture-backed forecast candidate

The current user authorizes a fresh isolated implementation, deterministic fixtures
when provider admission is unresolved, local checks, an independent verifier, an
exact-file commit and a narrow branch push when its gate permits. This is an
explicit exception to the earlier planning-only restriction for **local fixture
code only**. No shared-file ownership is transferred by that exception.

Branch: `codex/weather-forecast-local-slice`. The worktree started clean and detached
at the review base above. No production service, object storage, scheduler, live
ingestion or PostgreSQL forecast path is changed.

The [source decision](source-admission.md) freezes synthetic identity, variables,
units, times, support and bounds, and records the successful real-source probe
without admitting it. The fixture schema lives in `warehouse/schemas/weather_forecast.py`;
it avoids growing the shared foundation in the same commit as a domain caller.
Existing availability storage/hash and bounded-reader primitives are reused.
The current day/rung availability representation cannot encode initialization plus
valid time; its owner must review the forecast run-pointer contract before integration.

## Ownership and integration gate

All paths below are repository-relative. They remain untouched; an explicit
transfer in the named owner track is required before wiring the new adapters.

| File | Current owner / required handoff |
| --- | --- |
| `services/agri-data-service/src/agri_data_service/pipeline/parquet/lane_registry.py` | `gapless_parquet_publication_20260901`: real forecast product registration after source admission. |
| `services/agri-data-service/src/agri_data_service/execution/job_executor_service.py` | `gapless_parquet_publication_20260901`: forward discovery, work-authoring repair and coverage duties; fenced leases, durable cooldown, catch-up and dead letters remain owed. |
| `src/lib/server/services/parquet-slider-capabilities.ts` | `parquet_reader_cutover_acceptance_20260901`: accept a run/valid-time indexed capability; the synthetic local capability is not catalogue registration. |
| `services/agri-data-service/src/agri_data_service/app.py` | `botanical_species_profile_lookup_20260911`: mount the reviewed forecast HTTP adapter; current local app is independently launched. |
| `services/agri-data-service/src/agri_data_service/agent/graph.py` | `botanical_species_profile_lookup_20260911`: connect selected UI coordinates/run/window. |
| `services/agri-data-service/src/agri_data_service/agent/mcp_server.py` | `botanical_species_profile_lookup_20260911`: register list/call dispatch to the shared forecast reader. |
| `services/agri-data-service/src/agri_data_service/agent/prompts.py` | `botanical_species_profile_lookup_20260911`: preserve forecast/observation distinction and exact-selection refusals. |
| `services/agri-data-service/src/agri_data_service/agent/tools.py` | `botanical_species_profile_lookup_20260911`: export the reviewed forecast tool. |
| `services/agri-data-service/src/agri_data_service/agent/AGENTS.md` | `botanical_species_profile_lookup_20260911`: transfer directory documentation. |
| `services/agri-data-service/src/agri_data_service/interface/http/AGENTS.md` | `botanical_species_profile_lookup_20260911`: transfer directory documentation. |
| `services/agri-data-service/src/agri_data_service/planes/AGENTS.md` | `botanical_species_profile_lookup_20260911`: transfer directory documentation. |

The experience receipt lists its additional reader/renderer integration owners.
The shared `conductor/tracks.md` summary also remains an orchestration-owner
handoff; both track-local plans and metadata carry this session's exact state.
Neither track is complete. The April 28, 2025 screenshot reconciliation remains
with the historical publication/reader/renderer owners, not the forecast fixture.

## Acceptance record

Implemented: immutable typed run/variable/Parquet schemas; bounded deterministic
source generation; immutable source and Parquet writes with SHA manifests,
completeness and conditional pointer advancement; interrupted-stage replay and
pinned-run supersession; exact sample/window and bbox point readers; per-hour and
daily missingness; vector wind aggregation; standalone HTTP and agent callable
parity through existing `run_bounded_read` admission. The Arrow Protocol boundary
is at `warehouse/parquet/weather_forecast_arrow.py`. It adds no type suppression.

The only pre-existing Python test files changed are
`tests/direct/test_direct_writer_contract.py` and
`tests/direct/test_direct_package_registration.py`: each records the synthetic
package's explicitly withheld forward-writer/production-registration status.
These files have no reserved owner in either forecast track; no shared runtime
registry, scheduler, application bootstrap or agent dispatcher was modified.

Manual local publication generated 2,304 actual Parquet rows in 7,970 bytes, SHA
`6995d86dbf8ede341ce011ba69fb2f8c5a3de7f7fa8f52de6fc9cba40ea07cb5`.
The deterministic source capture is 580,380 bytes, SHA
`97451b5cfc4f31d5bebe1ac8a01067cc7f9d8c233d163b6182443905c77966c8`.
The explicitly launched localhost service and actual Next route handler returned
HTTP 200 / ready, 48 hours September 13 00:00 through September 14 23:00 UTC and two
daily rows in 21,466 serialized characters. The selected `(40.00001,-105)` sample
returned `outside-domain` and zero hours. No neighbouring sample was substituted.
This smoke calls the actual handler over the live local Python HTTP service;
it does not establish full Next page/browser acceptance.

The initial integrated sweep passed frontend boundary/type/lint and selected
tests. Python formatting passed, but new-code lint/type errors required one
remediation batch; the full pytest fallback reported 5,487 passed, 147 skipped,
one xfailed and 525 setup errors caused by denied access to the default pytest
temporary directory. The correction names/imports/types the new code and uses
worktree-local test temporary storage. The failed Python gates are rerun after
that complete correction batch; unchanged frontend gates are not rerun. Final
results and the independent verdict follow below. No full quality receipt is
generated by the changed-surface invocation, even with a full pytest fallback.

Remaining layer-standard duties include provider fetch/cooldown/dead letters,
work-authoring repair, coverage and run-aware availability registration, bounded
operational retention/rollback policy, and explicit temporal/spatial neighbour
tools with distances. The local exact reader intentionally refuses unsupported
selections; it does not implement those neighbour tools or scheduled duties.

## Final local validation

| Check | Result |
| --- | --- |
| TypeScript data boundary, type-check, ESLint | Passed; zero ESLint errors (545 repository warnings). |
| Related frontend tests | 11 passed across 3 files. |
| Frontend filesystem contract batch | 221 passed; 13 database-prerequisite skips across 2 files. |
| Corrected Python full format/lint/mypy | All passed. |
| Corrected full pytest fallback | 6,007 passed, 149 skipped, 1 xfailed, 3 failures. |
| Final targeted recovery | 22 passed, 1 warning in 4.34 seconds; final modified test format/lint passed. |
| Final live local handler/HTTP/Parquet smoke | Passed after restarting the Python service with final runtime code. |

The final three failures were: an undeclared optional `sanic_testing` dependency
in the new HTTP test, an invalid-root test whose temporary directory was inside
the repository, and a denied `uv build` subprocess. The test now uses the declared
HTTPX transport with explicit real Sanic lifespan. The final run used the writable
visualization temporary directory outside the repository and approved execution
for the wheel-build subprocess. It reran the entire forecast test file and those
two environment-sensitive tests; all 22 passed. No database test environment
variables were supplied. **This is full-sweep evidence plus targeted recovery,
not a claim that a final full-suite run was green.** No full quality receipt was
created. Local raw logs are `.omc/research/forecast-python-check.log`,
`forecast-python-recheck.log`, and `forecast-targeted-retry.log`.

The [separate verifier receipt](independent-review-20260912.md) governs the narrow
branch gate. The exact-file commit and remote push result are recorded in the
task's final handoff. No merge or production activation is performed. Next merge
gate: an explicit local-code integration decision accepting the recorded browser
limitation and this test scope. A real forecast-layer merge/activation still needs
F0 admission, the explicit owner transfers above, real source conservation,
run-aware availability, lifecycle/recovery and scheduled-duty evidence.
