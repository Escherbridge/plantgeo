---
type: independent-review
date: 2026-09-14
reviewer: /root/qa_inventory
status: accepted-with-scope-limits
overall_platform_qa: RED
---

# Session8 independent result review

Reviewer: `/root/qa_inventory`, separate from Session8 harness author `/root/workspace_social_audit`. The primary verifier previously reviewed prerequisite and runner contracts. This pass independently reads and rehashes the final artifacts; it does not rerun tests, build wheels, execute dependencies or change the source/receipt packets.

**Decision: accept the two narrowly stated Windows prerequisite outcomes. Overall platform QA remains RED.** The [Session8 ledger](runbook-session8-20260914.md) correctly preserves the limits of these checks. The [check packet](check-receipt-session8-20260914.json) is exactly 54,764 bytes, SHA256 `a2207e24191d34dc8240c1820e684ec6a7b42eae46420ae9af5cb5ca3415cb8b`. No blocking artifact discrepancy was found.

## Artifact and source reconciliation

All 32 unique filesystem artifacts referenced by the packet with path/hash descriptors match their recorded hashes and, where supplied, byte counts. These include both original fixture files, attempt receipts/logs/JUnit/control files, the retained old runner, six dependency wheels and their wheelhouse metadata, the built project wheel and supporting artifacts. The additional nested project-wheel path is relative to its attempt directory; resolving it in that documented scope reaches the same retained wheel, with the same hash. It is not a missing artifact.

The frozen Session6 inventory at `.omc/research/runbook-20260914/session6/capture-6fe525746f194e48bc8d323454ba4fec/context/inventory.json` rehashes to `f2b2ca107f21393b7b663c2420c0234491c26a138167672c9c63925204833953`. Independently applying the runner's declared source selection identifies 573 files. Every selected frozen source file and every corresponding file in **both** Session8 attempt service copies matches its inventory size and SHA256. The selection includes all service `src/` members, `pyproject.toml`, `uv.lock`, `tests/conftest.py` and the two existing test modules. Neither copied service tree contains dotenv files. This verifies unchanged selected tests and source, not the entire reused virtualenv.

The first attempt preserves its executed runner as `executed-runner.py.txt`, SHA256 `3292715196a065b7a870843c893a1810681117e7cbd8d49b86a15201c9e01b55`. Comparing it with the second runner, SHA256 `33cc4d6cd090cc63cbbbd7e3c781ae976208c57d1e0981237ebaabd11138da73`, finds only the recorded removal of `UV_NO_INDEX` and the added JUnit size check before reading. The historical runner was not overwritten.

## Exact test outcomes

| Retained attempt | Existing selected node | Independent JUnit result |
| --- | --- | --- |
| `frozen-inputs-attempt-dc665149da184a3ab72d52e38bb70a15` | `tests/test_public_evaluation_rehash.py::test_real_frozen_inputs_rehash_matches_the_digests_pinned_in_spec_md` | Exactly 1 testcase, 1 pass, 0 skips/failures/errors; suite 0.166s |
| `wheel-attempt-4b87444d8e6c48d7af2a05a1d625b531` | `tests/test_sql_queries_loader.py::test_wheel_contains_sql_tree` | Exactly 1 testcase, 1 pass, 0 skips/failures/errors; suite 1.947s |

JUnit hashes respectively are `c89f54742f612d2ef2f7041a03b855155b71b639fb55fe317cad6f63401c5e06` and `33b8063000022f547b6640d369e563fbdf658cfe92b431af6627146566a5d948`. The control files select these exact nodes without a skip override or alternate test implementation. Both pytest logs independently report one pass and one `asyncio_mode` unknown-configuration warning. Disabled plugin autoload explains the retained warning; it is not a skipped test. The wheel test's original skip-on-build-failure branches were not taken.

The 11,540,638-byte GHISACONUS CSV matches `e2f5a21b24fac00e930520ba959ab54cc8a3f8c56368f8e0a1868bbf3e3377d5`; the 2,394-byte forecast manifest matches `1bb6a6a707b432f2036edba86a426a32c1c04304b350af4caaec14a48cb20d09`. This establishes the pinned file hashes only. It does not validate the forecast payload tree or scientific evaluation.

## Wheel and dependency bytes

The built project wheel matches SHA256 `d6808a795727656c61cdd84a4c44de86d7e2bf9716bb337c95a158d21d3ff0a7`. Its ZIP has 572 members, all unique. Independently comparing its 568 packaged source members against the frozen inventory finds byte-identical content, including all 127 SQL files. The required `agri_data_service/sql/db/_loader_smoke.sql` member matches SHA256 `2811092a5f8e583d1b7871f9cd211ae06f60852a30629b54d1d76d9abbf8affe`. The original test asserts that member's presence; this review additionally verifies its bytes and the other packaged source members. It does not execute those SQL statements or certify their semantics.

The local wheelhouse receipt matches `0abe43370263a59ff50b7041c7790e7bcfa72cd008ff2c11a8f46adb107c9e06`. All 178 declared cache files, totaling 1,294,565 bytes, match their manifest hashes and the corresponding repacked ZIP members. All 172 declared RECORD content hashes and sizes verify; the six RECORD self-entries have no self-hash. Member sets contain no undeclared additions or duplicates. Exact constraints match Hatchling 1.32.0, packaging 26.3, pathspec 1.1.1, pluggy 1.6.0, tomlkit 0.15.1 and trove-classifiers 2026.6.1.19. This is local repack integrity, not independently authenticated upstream wheel provenance.

## Preserved limits

The inspected launcher gates test spawning on successful Windows Job assignment, with kill-on-close, 16-process and 2 GiB limits. Receipts record both launcher exits 0 and job closure. This is retained custody evidence, not a fresh operating-system process census. The wheel run uses a dedicated cache, local wheelhouse, exact constraints and `UV_OFFLINE`; it does not establish OS-enforced network isolation on Windows. The reused service virtualenv remains a prerequisite whose entire dependency set is not pinned by the interpreter hash.

These two targeted Windows results do not alter the retained Session6 Linux totals of 4,494 passes, 150 skips and one expected failure. They are not a full Python sweep, Linux packaging certification, release approval or a fix for the earlier Windows filesystem rename failure. Database-dependent tests, botanical registration, live-model/provider runs, scheduled advancement, governance and real-human acceptance remain open. No broader layer, slider, social or agent-workspace case is promoted by this review.
