---
type: session-evidence
status: active
date: 2026-09-14
---

# Session 8: targeted prerequisite checks and next defect intake

This supplements the [Session 6 Linux ledger](runbook-session6-20260914.md) and continues
the [runbook](../../../RUNBOOK.md). Two unchanged existing tests pass on Windows against
a sanitized copy of the frozen service source. They do not change the retained Linux result
of 4,494 passes, 150 skips and one expected failure, or resolve the earlier Windows rename failure.

## Frozen input hashes

Attempt `frozen-inputs-attempt-dc665149da184a3ab72d52e38bb70a15` runs only
`test_public_evaluation_rehash.py::test_real_frozen_inputs_rehash_matches_the_digests_pinned_in_spec_md`.
The existing 11,540,638-byte GHISACONUS CSV and 2,394-byte forecast manifest match the unchanged
pinned digests. JUnit records exactly one pass, no skip/failure/error; SHA-256
`c89f54742f612d2ef2f7041a03b855155b71b639fb55fe317cad6f63401c5e06`.
This verifies these two file hashes, not the forecast payloads or scientific evaluation.

The executed runner, SHA-256 `3292715196a065b7a870843c893a1810681117e7cbd8d49b86a15201c9e01b55`,
is preserved beside the attempt before subsequent harness corrections. Independent inspection
verifies all 573 copied source/config/test files against the frozen Session 6 inventory
`f2b2ca107f21393b7b663c2420c0234491c26a138167672c9c63925204833953`.

## Offline wheel resource check

The reviewed preparation uses 178 exact existing local cache files, 1,294,565 bytes, across
Hatchling 1.32.0 and five pinned dependencies. All declared RECORD hashes and sizes match.
It repacks those unchanged members into six deterministic local wheels, preserving metadata,
licenses and RECORDs. These new ZIP hashes identify local repacks; they are not independently
authenticated upstream distribution checksums. No download or global-cache mutation occurs.

Wheelhouse `wheelhouse-407004e8cf5848c3905f47b02939fe05` has receipt SHA-256
`0abe43370263a59ff50b7041c7790e7bcfa72cd008ff2c11a8f46adb107c9e06`.
Independent review verifies every wheel/member and the six exact version constraints before
execution. A separate reviewed harness revision removes an unsupported `UV_NO_INDEX` setting
and checks JUnit size before reading. Its SHA-256 is
`33cc4d6cd090cc63cbbbd7e3c781ae976208c57d1e0981237ebaabd11138da73`.

Attempt `wheel-attempt-4b87444d8e6c48d7af2a05a1d625b531` runs only
`test_sql_queries_loader.py::test_wheel_contains_sql_tree`. Normal isolated build dependencies
come from the verified local wheelhouse under `UV_OFFLINE`, exact build constraints and an empty
dedicated cache. The existing test builds the project wheel and verifies the required SQL member.
It passes with no skipped/failed/error case; JUnit SHA-256 is
`33b8063000022f547b6640d369e563fbdf658cfe92b431af6627146566a5d948`.
The retained wheel contains 572 members; SHA-256
`d6808a795727656c61cdd84a4c44de86d7e2bf9716bb337c95a158d21d3ff0a7`.

Both attempts scrub inherited credentials/configuration, disable pytest plugin autoload and
copy no dotenv files. The selected tests are disk-only; no OS-level Windows network isolation
is claimed. A kill-on-close Windows Job owns the launcher before test spawning, with 16-process,
2-GiB and explicit wall/output limits. Both launchers exit zero and their jobs close. The reused
service venv is a stated prerequisite, not fully pinned by its Python executable hash. The known
unknown-asyncio-config warning follows disabled plugin autoload; it is not a skipped test.

These are two targeted Windows checks, not Linux packaging certification, full Python gates or
a release certificate. The database, botanical registration, live-model and other skipped surfaces
remain unresolved. The [Session 8 check packet](check-receipt-session8-20260914.json) binds the
attempts, original/new runners, wheelhouse and inputs; SHA-256
`a2207e24191d34dc8240c1820e684ec6a7b42eae46420ae9af5cb5ca3415cb8b`.
The [independent review](independent-review-session8-20260914.md) verifies all 32 referenced
artifacts, both 573-file source capsules and the wheel's 568 packaged source members, including
127 SQL files. It accepts the two narrow outcomes and retains overall platform QA RED.

## Read-only intake for the next sessions

The relation-retention audit identifies four compact profiles that still lack grain/size proof,
five land-context tables requiring reconciliation with the Parquet contract, and a raster export
whose callable publisher/catalogue consumers preclude unproven deletion. No relation is removed,
no historical migration is rewritten and no production database is queried or changed here.

The social audit identifies stale canonical review, other-author denial and cross-client freshness
as the next bounded variants. Session 9 will diagnose them through the normal consented local
workflow. The proposed review contract is that a pending-queue action can decide a proposal only
while it remains pending; a stale second decision must preserve the first outcome and report a
conflict. No exposed or documented administrative override was found. That clarification and its
test expectations require independent review before execution; the source risk is not yet labeled
an observed browser failure. Session 10 separately prepares temperature/VPD time, depth, opacity
and mobile/rung checks. D260914-26's served-versus-selected date correction remains queued for
the next complete product batch. Candidate 14 is still frozen.
