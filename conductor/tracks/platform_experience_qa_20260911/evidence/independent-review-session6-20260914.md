---
type: independent-review
date: 2026-09-14
reviewer: /root/independent_verifier
status: accepted-with-scope-limits
check_receipt_sha256: fc6ff11e9bd2ef966b3411886774adf45f81950389592599f4ac3848cd661331
inventory_sha256: f2b2ca107f21393b7b663c2420c0234491c26a138167672c9c63925204833953
overall_platform_qa: RED
---

# Independent review: Session 6 Linux comparison

The completed second Linux run passes the repository's existing format, lint, mypy and pytest
gates within the reviewed offline, no-database scope. JUnit independently reconciles **4,494
passed, 150 skipped and one expected failure**, with zero failures or errors. This accepts the
bounded engineering evidence; it does not certify the skipped behaviors, resolve the retained
Windows failure, issue a Python quality receipt, or accept whole-platform QA.

The [final check packet](check-receipt-session6-20260914.json) is 17,026 bytes and has SHA-256
`fc6ff11e9bd2ef966b3411886774adf45f81950389592599f4ac3848cd661331`. The reviewer independently
rehashed all 23 referenced path/length/digest records: zero mismatches. The packet preserves
both executed runs, intervening inventories and captures, build logs, JUnit, and the independent
skip ledger and review notes. The [session record](runbook-session6-20260914.md) provides the
coordinator's execution account.

## Source and execution custody

The accepted inventory is
`f2b2ca107f21393b7b663c2420c0234491c26a138167672c9c63925204833953`: 2,118 files and
26,515,092 bytes. Independent verification found no missing, extra or mismatched files in
`session6/capture-6fe525746f194e48bc8d323454ba4fec/context`, including the three harness files
and inventory. The service's normalized quality digest remained
`e73ccd5b704f9009e45deed33d0dc2db60f072cf22d8992a5e3463a1030e0f05` across 885 inputs.
The captured product source includes Candidate 14; this Linux run exercises the Python service,
not its frontend changes. Later Conductor evidence updates are outside the immutable executed copy.

The reviewer inspected actual local Podman image and container metadata, in addition to the
retained receipts. Image
`f4825cc79c1138be702d8b22bbb3c67c67740e3a5e45caa73e8c972017aa2158` binds the reviewed
inventory and build owner, uses user `10001:10001`, and starts through an empty environment with
the explicit offline settings and JUnit destination. Build output records successful loading of
the installed DuckDB extensions without automatic installation, including the runtime user's
default extension directory.

Exact container `b56dc415694a7c0c48ad4de8b17926f69803decf5ed64abdb832db1967d418ce` binds that
image and owner `432edc6217d04dff970710ec1c1f7456`. It ran with network `none`, no mounts,
no-new-privileges, dropped capabilities, 4 GiB memory and two CPUs. It exited zero, was not
OOM-killed, and remains stopped for evidence. Fresh capacity was measured before execution.
The inner receipt records the same inventory hash before and after the checks, no interruption,
and the command `uv run --no-sync python scripts/check.py`. The four gate durations were
0.24 seconds for format, 0.34 for lint, 21.44 for mypy and 151.14 for the pytest gate.

## Complete skip reconciliation

JUnit contains 4,645 test cases. Its aggregate `skipped="151"` includes the one `pytest.xfail`;
it must not be described as 151 ordinary skips. The reviewer parsed each exclusion and retained
its class, name, type, exact reason and location in the ignored artifact
`.omc/research/runbook-20260914/session6/linux-attempt-432edc6217d04dff970710ec1c1f7456/independent-skip-ledger.json`.
That ledger's SHA-256 is `81ab9cec66e3692bf03da0d970d6659a15831e9a08fc0d994b0bb9039294a091`;
the originating JUnit SHA-256 is `648187b32bc2f032e2f0644e54cbf7e7b5022ca4678b87b18ae4e264234751fa`.
Both are bound by the check packet.

| Ordinary skip group | Cases |
| --- | ---: |
| Unconfigured disposable database URL | 103 |
| Botanical HTTP/tool registration pending integration and governance review | 34 |
| Writers explicitly not bbox-bounded | 5 |
| Windows-only native path characterization | 2 |
| Disabled live model probes | 2 |
| Missing frozen GHISACONUS/forecast fixture | 1 |
| Offline wheel build could not resolve `hatchling` | 1 |
| Retired PostgreSQL evacuation-zone lane | 1 |
| Loader smoke fixture intentionally read directly | 1 |
| **Total ordinary skips** | **150** |

The expected failure separately pins an existing wave-C2 extraction violation. In particular,
`tests.test_sql_queries_loader::test_wheel_contains_sql_tree` skipped after its isolated wheel
build could not obtain `hatchling` offline. Wheel packaging is therefore **not validated** by
this four-gate pass. Database behavior and the deferred botanical registration surface likewise
remain unvalidated; their skips are not passing behavior.

## Preserved failures and review limits

The first Linux run remains a failed result: four failed, 4,490 passed, 150 skipped and one
expected failure. Two failures required the omitted incomplete strategy-label example; two
western-plan regeneration cases stopped at the missing canonical NASA lattice guard before
any byte comparison. The reviewed correction admitted those exact two fixtures and required
their inclusion before inventory/copy. It did not change tests, product behavior or line endings.
A matching service quality digest had not established complete repository-relative fixture
closure. The original inventory, capture, image, container and logs remain intact.

The earlier Windows Candidate 11 suite failure in
`test_spools_sorted_bounded_chunks_and_a_recoverable_checkpoint` remains unresolved. Linux
used Python 3.12.12; Windows used 3.12.10, so this comparison changes both operating system and
interpreter patch version. Passing Linux behavior cannot establish the Windows failure's cause
or erase it. The two Windows-only native path cases retain their separate Windows evidence.

The independent lane reviewed preparation corrections before execution, inspected actual local
Podman metadata through read-only commands, rehashed retained files, parsed JUnit, and authored
the ignored skip ledger/review notes and this canonical review. It did not run application tests,
change runtime source, operate databases, or create/start/stop containers. No further test run was
needed for this review. Whole-platform QA and case acceptance remain **RED**.
