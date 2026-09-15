---
type: qa-evidence
date: 2026-09-14
status: investigation-complete-recovery-blocked
author: /root/qa_inventory
coordinator: /root
base_commit: 0f16e40dae3cce1d3b6d4ac00138254a968d974f
---

# Sensor held checkpoint: session 3 investigation

The retained September 9 child logs establish a positive-reading/governed-absence conflict. The current adapter still refuses that conflict, while the scheduler correctly holds the lane after three unsuccessful buckets. This investigation establishes the original failure mechanism; it does not authorize removing current evidence, superseding the run, or claiming production recovery. No runtime source, tests, configuration, database or remote objects were changed in this lane.

## Exact evidence and limits

Primary historical capture: repository-local `.omc/state/freshness-fanout-20260913-1428/forward-recovery/events.jsonl`, SHA-256 `dda8f4f5d295a01abff9a08870787c2c32656799af4789422832b008dfa51510`. References below are JSONL line numbers. The successful command output and structured MCP result were read separately; command text and credentials were not copied into this report.

| Evidence | Established fact |
| --- | --- |
| Line 141, durable run/work-item/attempt query | Run `def58693-a0b6-4d97-90f2-3127bfc9b418`, lane `sensors-direct-forward`, scheduled `2026-09-09T14:20:00Z`, started `14:24:04.939417Z`, completed failed `14:57:26.690329Z`. Its one work item `7c021f22-e332-4cf5-adc0-ae843484b187` is `dead_letter`. Five attempts failed as `scheduled_command_exit`, each reporting command exit 1. The query has no output rows or supersession incidents. |
| Lines 159 and 161, retained Railway structured child logs | Child run `sensors-direct-forward-20260909T142406Z` fetched 66,767 records, rejected 0, selected 66,767, and polled 593 stations. September 9/8/7 published 2,432/2,588/2,812 base rows. September 6 carried 2,377 incoming rows and September 5 carried 2,850. Both failed with `DirectSensorsError: refusing to merge poll rows into sensors z13 <day> with status=absent`. |
| Current root capture `.omc/research/runbook-20260914/railway-session2-executor-raw.json` | The same run still holds the lane: three consecutive unsuccessful buckets and an explicit operator-supersession requirement for the current bucket. This is a scheduler bucket streak, distinct from the five attempts inside the retained run. |
| Root capture `.omc/research/runbook-20260914/railway-session3-sensor-logs.json` | Direct deployment logs continue reporting `plantgeo_job_executor_tick_unhealthy` with `sensors-direct-forward` through `2026-09-14T19:22:02Z`. They do not independently reproduce the September 9 child exception. |
| Root physical capture `.omc/research/runbook-20260914/environmental-session3-objects.json` | Root reports September 6 still has four `absent.json` markers and four indexed `governed_absence` rows, with latest published sensor day September 9. The capture retains marker bodies/hashes and indexed samples. This agrees with the historical refusal and exposes a conflict requiring source-evidence review. |

The second and third capture SHA-256 values respectively are `0774a0dcad006eae9a32bea624b6f3872a5fdc39de10c77157282c968c5914e4` and `f271aaa6b74387c8d8606eb7b1f95daf90384ade86d00fc447bb348675fe6aa6`. The physical capture is coordinator-owned and may receive additional samples; its final receipt belongs to that audit.

The retained structured logs inspected establish the cause for the named child invocation. The durable records establish five exit failures, but this report does not assume identical detailed exceptions for every attempt. Positive counts do not prove complete station coverage, source validity, or possession of reproducible row bytes. The original author of the false absence and its complete decision inputs remain unresolved here. No evidence supports describing this particular failure as a transport outage or an empty upstream response.

## Source ownership and existing recovery boundary

Current `services/agri-data-service/src/agri_data_service/pipeline/direct/sensors/adapter.py:227` discards the supplied run ID. Lines 238–264 accept data, missing and incomplete states and refuse the remaining absent state. `tests/direct/test_sensors_direct_adapter.py:201` explicitly pins that refusal. This is the concrete behavioral mismatch exposed by late positive observations; changing it is a governed publication-contract change, not merely suppressing an exception.

`execution/job_executor_service.py:518` owns the hourly `20 * * * *` direct command, polling the complete retained window newest first. Its `judge_failed_checkpoint` at line 982 releases transient failures by the clock until the policy threshold, then requires an operator. Supersession opens the current bucket and preserves the spent failed run and dead letter. `execution/job_run_supersession.py:417` exposes the explicit CLI and defaults to a dry run.

`ingest/sensors.py:100` declares six days of observation retention. `scripts/AGENTS.md:936` calls this a measured repository acquisition bound, not an official retention SLA. A September 14 rolling poll cannot be assumed to recover September 5/6. Existing rows or counters cannot be used to invent aged-out observations.

The prior isolated automatic-retraction proposal is retained in `.omc/state/freshness-fanout-round3-20260914/sensor-r3-f7/HANDOFF.md`. Its F7 revision validates and checkpoints a replacement before clearing all four markers; the earlier order could destroy evidence on malformed input. That handoff explicitly leaves multi-object transport interruption non-atomic and records verification not run in its lane. Neither historical proposal is sufficient evidence to promote generic automatic retraction now.

There is already a narrower, date-pinned recovery operator: `services/agri-data-service/scripts/correct_sensor_absences.py:229` prepares September 5/6 only; `:425` applies only a pinned request under externally proven quiescence. `scripts/AGENTS.md:24–73` defines its contract. It requires candidate manifest SHA-256 `e99bce200991ea1b57ef4c2e60a6c36598c992c4d7ad27e700962c528cf8dd40` and source archive SHA-256 `eb3ca823a0a3319cc42e47c4066893cfd88efd58979bfb1e177949d9eb70537e`. Exact original marker identities, source artifacts, request, journal, locks and final physical/index evidence are checked. It preserves `source_complete=false`; it performs no executor supersession or lane-wide retry sweep.

## Artifact custody and next bounded actions

The committed production packet `conductor/tracks/parquet_production_acceptance_20260901/evidence/operational-release-packet-20260912/README.md:236–243` records the sensor archive digest and says the underlying artifacts are absent. This is a historical custody finding, not proof that every current disk location is empty. A filename inventory of project-local `.omc` and `.tmp`, including ignored files and excluding test fixtures/dependencies, found no non-test `candidate-manifest.json`, `.tar.gz` rescue archive or rescue ZIP. Three inaccessible worktree pytest-cache directories made that inventory partial; no global user directories were searched. Synthetic pytest candidates are not recovery evidence. The expected archive is TAR.GZ, not ZIP. No exact recoverable source artifact was located in the inspected recorded paths.

Next actions are concrete and remain separated:

1. Recover the exact `candidate-manifest.json`, its named artifacts (including `sensors-2026-09-05-positive-candidate.parquet` and `sensors-2026-09-06-positive-candidate.parquet`), and the original rescue TAR.GZ from recorded custody paths. Verify with `Get-FileHash -Algorithm SHA256 -LiteralPath '<exact-file>'` against the pins above. A matching narrative or test fixture cannot substitute. If custody is lost, a newly reviewed evidence/version path is required; do not relax the pins.
2. Once exact evidence is available, the current operator can prepare a concrete request using `python scripts/correct_sensor_absences.py --candidate <candidate-manifest.json> --archive <rescue.tar.gz> --out <new-local-directory>` from the Python service directory. Preparation reads current object evidence and writes only local content-addressed artifacts. It makes no database connection or remote mutation. No preparation was run in this investigation.
3. Obtain review of the prepared request and an assigned operator's actual writer/retry-worker quiescence evidence before any apply. The existing contract requires a separately pinned, at-most-one-hour attestation covering both writers and in-flight retry workers. A held lane or acquired advisory lock alone is insufficient. Keep original evidence archived; verify all four rungs and availability after the exact correction.
4. Review forward recovery separately. The read-only command is `agri-service ops jobs-supersede-run --lane sensors-direct-forward --run-id def58693-a0b6-4d97-90f2-3127bfc9b418 --evidence <reviewed-cause-and-release-receipt> --operator <assigned-operator>`; omission of `--apply` is deliberate. Recheck the exact held run before any authorized mutation. Do not reopen/delete its dead letter or invoke a global executor tick to force unrelated work.
5. A later source-change proposal must specify positive-evidence admissibility, validation before mutation, durable all-rung crash recovery, stale-pointer/read behavior and forward regression coverage before changing the absent-state contract. No generic retraction patch is proposed for immediate application. After approved recovery, require new scheduled-run receipts, physical/index/selected-day parity, and three sustained scheduled advances before production acceptance.

The coordinator's open-ended Railway infrastructure-agent call was automatically rejected before execution because that capability could perform production mutations. Direct bounded log/deployment reads provided the current evidence above. This lane made no infrastructure calls and used no workaround. Operator identity, publication authority, exact artifact custody, quiescence and forward acceptance remain unresolved. Full UI QA and production acceptance remain open; this is not a PASS receipt.
