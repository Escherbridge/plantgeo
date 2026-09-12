---
type: independent-review
track: weather_forecast_parquet_lane_20260911
recorded_on: 2026-09-12
status: approved_local_fixture_only
review_base: 0ee4f5bd99666760a5881ed1427301944dc7b001
---

# Independent local forecast review

The verifier reviewed this candidate in a separate agent context from both
runtime writers. The acceptance scope is the user-authorized synthetic local
slice only. Real provider admission, shared-owner transfers, scheduled duties,
production availability and F0–F4 completion are excluded from local acceptance.

The source receipt accurately records the bounded Single Runs temperature probe.
The verifier independently recomputed its SHA-256 as
`099d00cf256302968ba9461323a3448d9e6a027134572e143072ae935ca0d8f5` and inspected
the 24 hourly values, Celsius units and returned sample coordinates. The captured
provider documentation distinguishes initialization selection from public release.
No echoed model version or issue time is asserted. Probe values are not fixture
inputs. Network access succeeded; unresolved source-contract evidence is the gate.

Initial review confirmed that the forecast plane's import of its own direct
adapter is permitted by the existing dependency-lattice test. No shared-owner
file was modified at that review point. Storage-port reuse does not establish
shared availability-index or executor integration.

The independent pre-sweep review is complete with no remaining static blocker.
The writers resolved the findings before final verification: explicit `Any` was
removed, the 72-hour artifact horizon is distinct from the 48-hour request cap,
malformed manifest fields are validated as objects, actual decoded Parquet rows
are returned only after full deterministic reconciliation, and daily nulls have
partial-day, missing-variable or undefined-calm-direction reasons.

The existing direct-package and writer-contract inventory tests now name the
fixture exception and its removal gate without changing production registries
or weakening their assertions. Review also caught the reserved pytest parameter
name and a test-clock substitution that would break timestamp type checks; both
were corrected before the sweep. The HTTP/agent parity test now freezes time at
the reader seam and asserts a populated `ready` response through the actual ASGI
transport. Cardinal wind, 350/10-degree wrap and full-circle cancellation cases
are covered in the candidate tests.

Static security review found a bounded filesystem root, exact run-path
validation, immutable-key refusal and conditional pointer replacement. Correctness
review found no prior-day/run substitution, sampled-point support rather than
invented cell footprints, and reasoned nulls distinct from numeric zero. Memory
and performance acceptance remains limited to declared caps and the recorded
bounded smoke response; peak RSS and concurrent load are unmeasured.

## Final verification and verdict

The verifier read the local check logs and the final HTTP test after remediation;
the orchestrator reported successful process exits for the passing gates below.
The verifier did not independently rerun tests or alter runtime code.

| Evidence | Result and limit |
| --- | --- |
| `npm run check:data-boundary` | Passed all three checks: provider URL, client/server import and fabricated-observation boundaries. |
| TypeScript type-check and ESLint | Passed; ESLint reported zero errors and 545 existing warnings. |
| Affected frontend selection | 11 forecast tests passed; the associated contract batch had 221 passed and 13 database-dependent skips. This is a scoped pass. |
| Initial Python sweep | Exposed new lint/type findings and 525 temporary-directory errors; it was not accepted. |
| Corrected Python broad sweep | Format, Ruff and mypy passed. Pytest had 6,007 passed, 149 skipped, one expected failure and three failures. |
| Final failed-case recovery | Format and Ruff passed; 22 tests passed, including the complete forecast test file and the two formerly failing existing tests. One cache-directory warning remained. |
| Local live transport smoke | The orchestrator restarted the actual Python fixture service after runtime edits and exercised the actual Next handler against it: 48 hours, two UTC days, an exact outside-domain refusal, and a 21,466-character response. No prior-place or prior-time substitute was returned. |

The three failures in the corrected broad sweep were the new test's undeclared
`sanic-testing` dependency, a repository-root negative test whose temporary
directory was inside the repository, and a denied wheel-build subprocess. The
test now uses the already-declared `httpx` ASGI transport with real Sanic lifespan
messages. The two environment failures were retried under the authorized external
temporary root/process permissions. This is a broad sweep plus explicit targeted
recovery, **not one wholly green full-suite run**. No full Python quality receipt
or database coverage is certified by this review.

Reviewed local logs: `.omc/research/forecast-boundary.log`,
`forecast-typecheck.log`, `forecast-lint.log`, `forecast-frontend-tests.log`,
`forecast-python-check.log`, `forecast-python-recheck.log`, and
`forecast-targeted-retry.log`. The companion implementation receipt records the
commands, environment and retained evidence for the branch.

**Verdict: approved for the isolated local fixture slice and an exact-file commit
and push to `codex/weather-forecast-local-slice`.** No remaining actionable defect
was found in that bounded scope. Approval covers the reviewed runtime, tests and
their narrow inventory exceptions; runtime changes after this receipt require
another review of the changed behavior. No shared-owner path is transferred.

Main merge, real Open-Meteo admission, production run publication, shared
availability/capability/agent registration, scheduler duties, run-retention
policy, operational rollback, measured peak-memory/concurrency budgets and
production readiness remain open gates. This local approval closes none of
F0–F4's production acceptance checkboxes.
