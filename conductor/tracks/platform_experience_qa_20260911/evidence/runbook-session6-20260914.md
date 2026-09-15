---
type: qa-session-ledger
status: active
updated_on: 2026-09-14
---

# Runbook Session 6: isolated Linux Python comparison

## Purpose and scope

Continue the [operating runbook](../../../RUNBOOK.md) after the bounded desktop and mobile
journeys in [Session 5](runbook-session5-20260914.md). Candidate 11's full Windows Python
sweep remains failed on an access-denied directory rename. A Linux comparison checks the
same current service code on its deployment operating system without changing the tests,
weakening filesystem containment, or treating a different-platform pass as a Windows fix.

This session does not certify database integration, environmental source admissions,
scheduled advances, live layer rendering, model streams, human acceptance or a release.
All existing working changes remain uncommitted and production remains untouched.

## Reviewed preparation

The ignored packet is `.omc/research/runbook-20260914/session6/`. Its source collector,
QA Dockerfile, internal check runner and external container runner were authored separately
from independent review. Review corrected two preparation defects before execution:

- The collector must refuse any omitted file from the service's actual quality-digest inputs.
- Locked DuckDB extensions must exist in both `/opt/duckdb-extensions` and the runtime user's
  default extension directory. A build-time load check disables automatic installation.

Inventory precedes copying. Apply requires an unchanged manifest and current source/harness
hashes, copies into a new UUID directory, then verifies every copied file and re-inventories
the original. Source is copied into a Linux image; no Windows source bind mount or real `.git`
is used. Environment files, credential files, caches and private orchestration state are excluded.
Repository-relative fixtures and current untracked service tests remain included.

Image preparation pins the cached Python 3.12.12 and uv 0.11.29 bases, installs public locked
dependencies with a bounded build, and supplies no database or provider credentials. Tests run
as a non-root user with no network, ports, host mounts or capabilities. The complete existing
four-gate `scripts/check.py` runs once, with source hashes checked before and afterward.
No quality receipt is written. Database skip reasons remain part of any reported result.

## Capacity and custody

Root measured the existing Podman WSL guest on September 14 at 21:34:02 UTC:
30,082 MiB total memory, 29,098 MiB available, and 990,988,042,240 bytes free disk.
Earlier configured 2 GiB metadata did not describe the actual guest memory. The bounded QA
allocation is 4,096 MiB and two CPUs; no VM settings or existing workloads were changed.
The only running containers at that checkpoint were the three owned Session 5 database,
Redis and Martin services. Fresh capacity evidence is required before build and execution.

The runner checks exact image/container IDs and unique ownership labels. Runtime is bounded
server-side to 1,200 seconds; failure cleanup may stop only the revalidated owned container.
Stopped containers, logs and receipts remain for inspection. A remote build that does not
report completion is explicitly unconfirmed; no broad cleanup is authorized.

## Execution evidence

The first inventory, independently rehashed before copy, binds 2,113 files / 26,243,584 bytes:
`b8352b2b07fa0eb814ca04e8ab519ba46621179dd91c9aa23dc4550aa7aadddb`.
Its service quality digest is `e73ccd5b704f9009e45deed33d0dc2db60f072cf22d8992a5e3463a1030e0f05`
across 885 inputs. Copy verification found no missing, extra or changed files. The built image
`1f9c267c3527eda88b4187111b51665026d4b1ca392fbbb9816e4c05dd065041`
also passed independent identity, labels, user, entrypoint and extension-preparation review.

The first offline run used container
`ec4d6904db5d9ab060a7a5ea71044937faa8f8bb42b6fee7882178b970d16e94`
and completed at 21:48:13 UTC. Format, lint and mypy passed. Pytest recorded **four failed,
4,490 passed, 150 skipped, one expected failure and two warnings**. Source hashes matched
before and afterward; the container exited with code one, was not OOM-killed and remains
stopped for evidence. See the [Session 6 check packet](check-receipt-session6-20260914.json).

All four failures are capture omissions. Two strategy-label tests need the service's
`examples/strategy-label-source-mapping.incomplete.json`. Two western-plan regeneration tests
need `infra/local-warehouse/plans/nasa-power-na-sampling-20220430-20260430-asof-20260721.json`;
they stopped before any byte comparison. These dependencies are outside the service's formal
quality-digest directories. A matching digest therefore did not establish complete test-fixture
closure. The capture policy is being corrected to require these exact dependencies; no test
or application behavior is weakened. The failed inventory, image, container and logs stay intact.

The Windows interpreter was 3.12.10; Linux used 3.12.12. Any comparison preserves both the
operating-system and patch-version differences. Passing Windows-only drive/UNC regressions
retain their separate Candidate 11 evidence. Neither this failed Linux run nor a future Linux
pass resolves the Windows failure or certifies the skipped database integration surface.

## Corrected capture and verified Linux checkpoint

The corrected collector requires both missing fixtures explicitly. The intermediate inventory
`52f75c60a3733a51c3ab8347fedacefad1dc154984ce43318cfc7c2effd17626` was copied but never built;
it was superseded before execution to add complete JUnit evidence and include the independently
reviewed Candidate 14 drawing fix. No application test was changed to accept the omissions.

Final inventory `f2b2ca107f21393b7b663c2420c0234491c26a138167672c9c63925204833953` binds
2,118 files / 26,515,092 bytes, including the same 885-input Python service digest as the first
attempt. The image is `f4825cc79c1138be702d8b22bbb3c67c67740e3a5e45caa73e8c972017aa2158`;
the completed container is `b56dc415694a7c0c48ad4de8b17926f69803decf5ed64abdb832db1967d418ce`.
Independent review checked the source copy and actual image/container metadata. Runtime had no
network or mounts and used UID 10001, 4,096 MiB and two CPUs. It exited zero without OOM; inner
source hashes matched before and afterward. The runner finished at 22:12:59.479 UTC, retaining
the stopped container and complete evidence.

All four gates passed: format, lint, mypy and pytest. JUnit accounts for **4,645 cases: 4,494
passed, 150 skipped, one expected failure, zero failures and zero errors**. The XML's 151 skipped
nodes include the expected failure and must not be reported as 151 ordinary skips.

| Ordinary skip category | Cases |
| --- | ---: |
| Database URL unconfigured | 103 |
| Botanical registration pending integration/governance | 34 |
| Writers outside bbox-bounded contract | 5 |
| Windows-only characterizations | 2 |
| Live-model probes disabled | 2 |
| Frozen external GHISACONUS fixture missing | 1 |
| Offline wheel build could not resolve hatchling | 1 |
| Retired PostgreSQL evacuation-zone lane | 1 |
| Loader fixture intentionally read directly | 1 |

The separate expected failure pins the existing wave-C2 extraction violation. The per-case
ledger is retained beside the second run, SHA-256
`81ab9cec66e3692bf03da0d970d6659a15831e9a08fc0d994b0bb9039294a091`.
In particular, wheel packaging is not validated by this pass. No full Python quality receipt
was written, no skipped surface was promoted, and the Windows rename failure remains open.
The final [check packet](check-receipt-session6-20260914.json) retains both runs and all captures;
its SHA-256 is `fc6ff11e9bd2ef966b3411886774adf45f81950389592599f4ac3848cd661331`.
The [independent review](independent-review-session6-20260914.md) rehashes 23 bound artifacts
with zero mismatches and accepts this bounded offline result while retaining whole-platform RED.
