---
type: evidence
track: repository_conformity_hardening_20260901
slice: c2-c3-c4
status: inventory_only
observed_at: 2026-09-12
base_commit: 6c08907
---

# Remaining conformity candidate inventory

This is a bounded, read-only inventory from commit `6c08907`. It records the
current candidates and their proof state; it does not grant deletion or
refactor authority. The earlier TypeScript and Python proof packets remain the
source of truth for candidates already removed or for detailed consumer scans.

## Classification rules

| Classification | Meaning in this inventory |
| --- | --- |
| **confirmed** | The evidence packet proves the candidate is removable, but the required change is still separately gated. |
| **contingent** | Static evidence is insufficient; a named runtime, route, operator or production proof is required. |
| **refactor** | The candidate is reachable or contract-bearing and needs an ownership-preserving extraction, not deletion. |
| **enforcement gap** | A check or receipt correctly exposes unfinished structural work; the check must remain until the owning refactor lands. |
| **protected evidence** | Historical, migration or production-lineage material must remain even when it has no ordinary runtime import. |

## Canonical-core and CLI candidates — refactor / enforcement gap

| Surface | Classification | Evidence and blocker |
| --- | --- | --- |
| `services/agri-data-service/src/agri_data_service/interface/cli/commands.py` | **refactor** | The thin-adapter contract records 26 violations: 24 transaction-boundary sites plus `LaneChunkRunner` and `ChunkedLane`. The four related `Lane*` protocols are also part of the same framework even though the rule does not count them. The file is an active command surface, so extraction must preserve help, names, outputs, exit codes and writer ownership. The direct-writer handoff remains owned by the shrink track until its frozen s2a tree is explicitly transferred. |
| `services/agri-data-service/src/agri_data_service/parquet_ops/` and `warehouse/parquet/` | **refactor** | Snapshot registry/layout/coverage/row-read and receipt/finalization responsibilities are still split across public modules and product scripts. The required proof is byte-for-byte equivalence for golden fixtures, including manifests, checkpoints, SHA-256 receipts and `_COMPLETE` records. No deletion is authorized from the current static overlap alone. |
| `services/agri-data-service/scripts/build_precipitation_from_canonical_snapshot.py`, `build_relative_humidity_from_canonical_snapshot.py`, `build_shortwave_radiation_from_canonical_snapshot.py`, `build_soil_moisture_from_canonical_snapshot.py` | **refactor** | These four product scripts remain operator entry points in the c2 scope. A shared typed snapshot-breakdown/receipt core is planned, but they cannot be removed until command behavior and receipt bytes are proven equivalent and the owning shrink handoff is frozen. The matching `build_era5_land_from_canonical_snapshot.py` and `build_nasa_power_from_canonical_snapshot.py` files are intentionally excluded from this c2 inventory. |
| `src/components/map/MapView.tsx`, `src/hooks/useRegionalIntelligence.ts`, `src/hooks/useViewportProxiedLayers.ts`, `src/lib/server/services/hydrosheds.ts` | **refactor / contingent** | These are shared application or reader surfaces named by c2. They are not dead-code candidates. Ownership remains with the active reader, polygon and Parquet tracks where applicable; any conformity edit requires an explicit handoff and a final integrated sweep. |

The strict thin-CLI xfail is therefore an **enforcement gap**, not a passing
result. It must be removed only when the violation count reaches zero and the
adapter/help/exit-code evidence is independently reviewed.

## Dependency candidates — confirmed or contingent follow-up

| Dependency | Classification | Evidence and blocker |
| --- | --- | --- |
| `@deck.gl/mapbox`, `@deck.gl/react`, `jotai` | **confirmed** | The TypeScript packet records zero imports and a regenerated lock graph after removal. The current checkout retains the historical packet and the already-recorded removal; no second uninstall is authorized. |
| `s3fs`, `redis` in the agri-data service | **confirmed** | The Python packet records removal from `pyproject.toml` and `uv.lock`, with only their exclusive transitive closure removed. The required format/lint/type/test receipt and both image builds remain a separate final-sweep gate. |
| `preact` | **contingent — retained** | It has no direct application import, but `@auth/core` requires the exact pinned `10.11.3` version and next-auth renders sign-in pages through that graph. Removal requires an auth smoke test and lock-graph verification; it is not dead weight proven removable by import scans. |

## Deprecated endpoint and UI/service candidates — contingent

| Candidate | Classification | Evidence and blocker |
| --- | --- | --- |
| `teams.inviteMember` | **contingent — retained** | The repository has only a compatibility security test, but the procedure is a public tRPC route and may have external consumers. The packet records the `@deprecated` replacement and sunset `2026-10-01`. Before removal, inspect production request evidence for this exact procedure, then delete the procedure and only its compatibility case with a rollback. |
| `src/lib/server/services/geofence.ts` | **contingent — retained** | `checkGeofences` has no in-repository caller, but it contains real alert-writing logic and a point-in-polygon implementation. A route/import scan does not decide whether to wire it into tracking or delete it; an owner decision and runtime/route proof are required. |
| `src/lib/server/services/places.ts` | **protected from deletion** | The former zero-consumer finding was retracted: `places.ts` is imported by the mounted public `places` router. Its spatial behavior was repaired and documented as build-ahead code. It is not an orphan. |

## Python `planes/` and execution candidates — retained blockers

| Surface | Classification | Evidence and blocker |
| --- | --- | --- |
| `services/agri-data-service/src/agri_data_service/planes/` | **contingent — retained** | The generic `parquet_ops` reader supersedes several row-read functions, but the plane modules still provide uncovered point/containment, natural-key geometry lookup and severity-ranking behavior. Delete only after those behaviors have typed counterparts and the dedicated serving tests are reconciled. |
| `services/agri-data-service/src/agri_data_service/execution/hot_projection.py` | **contingent — retained** | Two current documentation surfaces still mandate the Railway hot projection it types. A static “no caller” result cannot override those operational instructions; reconcile the topology/runbook owner first. |
| `services/agri-data-service/src/agri_data_service/execution/public_evaluation_lineage.py` | **protected evidence / owner retained** | It is the executed loader behind a recorded production-lineage write. Its import graph is not deletion authority; preserve it until the owning lineage/publication track supplies a replacement and migration/rollback evidence. |
| `tests/parquet/test_*_serving.py` for the retained planes | **protected evidence** | These tests document uncovered serving behavior and must not be deleted merely because production callers are absent. They can be retired only with a typed replacement contract and independent review. |

## Final-gate blockers

This inventory does not close the track. The open gates are:

1. Freeze the shrink s2a CLI/core tree and obtain an explicit handoff before c2 edits.
2. Complete the canonical extraction with help/exit-code, byte-equivalence and receipt evidence.
3. Resolve each contingent candidate with the proof-before-delete contract and retain protected evidence.
4. Apply accepted changes together, then run the single frontend/Python/type/lint/test/build sweep on the exact integrated tree.
5. Obtain independent review and publish rollback notes before changing the track to complete.

No Railway, production database, PostgreSQL/pgt, object store, deployment,
writer, scheduler or data-plane system was accessed for this inventory.
