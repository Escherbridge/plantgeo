---
type: track-evidence
track: offline_export_service_20260908
audited_on: 2026-09-12
status: selected_builders_identified_phase_acceptance_open
source_commit: 6c08907ba392d8dcdff3e96c41fdb27c6a74fe9d
source_tree: 92f3baef253389b737f3e36fd47a96b52c43c312
---

# Local builder and phase reconciliation

This repository-only audit reconciles the selected builders with the historical
standalone-service phase ledger. It did not contact Railway, object storage or a
database, run a builder/compiler/writer/scheduler, publish availability, or
measure production performance.

## Selected implementation and bounded acceptance

The standalone `plantgeo-export` service remains rejected and is not a missing
deliverable. The selected implementation is the two in-repository builders:

| Family | Current repository identity | What checked-in evidence accepts | What it does not accept |
| --- | --- | --- | --- |
| Five ERA5-Land lanes | `services/agri-data-service/scripts/build_era5_land_from_canonical_snapshot.py`; file SHA-256 `1d1ceddd43569fd9579a1f03df00e667f2f177a1112fe0a18a4dbf9a0a934e1b`; introduced at `fb72d07706ee84b2f4e119cb81b391a1d74fadfb`. | The September 9 archive records the five day-grain builds and selection of this builder family. Current source declares five product specs, pinned source/product manifests, immutable conditional upload, real `PartitionCompletion` markers and resume audits. | No checked-in exact eight-lane manifest, accepted phase verdict or end-to-end timing packet binds the current population. Conflicting dated `soil-field-vpd` counts (448 sparse, 446 contiguous resume, 1,556 built and a 1,572-day serving span) remain unresolved by a current receipt. |
| Three NASA POWER temperature lanes | `services/agri-data-service/scripts/build_nasa_power_from_canonical_snapshot.py`; file SHA-256 `d99cb59f527960153969b0b286ba3ea9eca6663527231fd3281088808c5505f1`; introduced at `b5b29b39b9802d3c4e3155e633a6e7d4a4f908ff`. | The runtime receipt checked this exact file hash. The September 10 machine receipts accept the bounded output: mean/min/max each have 1,560 complete days from 2022-04-30 through 2026-08-06, 1,560 rows at each required rung and a verified generation. | This does not accept current forward health, the later source-ceiling gap, all original phases, a standalone staging service, or a general production-performance claim. |

Source inspection confirms that both selected builders read pinned canonical
snapshot and frozen-product manifests, derive day-grain ladders, write real
completion markers and gate uploads behind an explicit apply flag. They are
direct object-store builders, not implementations of Phase 1's reusable local
`stage pull` / `stage verify` service, and they do not emit the Phase 4
availability-bootstrap input from local staged state. Their existence therefore
cannot retroactively turn the discarded service's pending reviews into approvals.

## Original phase verdict reconciliation

| Original phase | September 12 disposition |
| --- | --- |
| 0 — staging premise | **Open.** The canonical root and aggregate counts are recorded historically, but the required current per-lane source/history/rung manifest is not checked in. |
| 1 — standalone staging layer | **Superseded, do not implement.** The owner rejected the standalone service. This is a scope disposition, not an approval of its failed implementation or reviews. |
| 2 — local transform | **Bounded delivery proven, phase verdict open.** The two selected parameterized builders and the receipt-backed outputs above exist; the original adversarial phase verdict was never replaced with an accepted selected-builder review. |
| 3 — bulk upload and verification | **Open.** Delivery receipts exist for bounded products, but the promised objects/bytes/wall-clock/effective-rate comparison for all eight lanes is absent. |
| 4 — availability | **Partially delivered, open.** Temperature generations are receipt-backed. Deferred relative-humidity history and the proposed local-state input/append path have no publication receipt. |
| 5 — close | **Open.** Historical warm capability observations cannot substitute for a fresh exact-candidate serving packet, performance comparison and handoff. |

## Deferred history and missing identities

The current governing deferred interval is relative-humidity
**1981-01-01 through 2017-12-31**, not only the 1981–1985 example retained in the
historical phase text. Checked-in history says those days were physically present
and correct but unindexed; this checkout contains no exact append/publication
request, bootstrap binding, generation identity or independent readback receipt.
No recovery can be inferred from later temperature publication.

The ignored source inventories and operational logs cited by the September 10–11
summaries are absent here. The canonical source manifest hash is recorded as
`465abc4e813bf28c78acd7f97a4da9d19ad959e525de3eb1f422ca2f6e73e94f`,
but the manifest and its 8,364-part object inventory are not checked in and were
not revalidated. The eight lane-specific input manifests, live-prefix object
identities and a reconciled `soil-field-vpd` population are likewise absent.

The next acceptable packet must bind those exact source and product identities,
the current eight-lane day/rung populations, an accepted independent review of
the selected builders for the claimed scope, and measured staging/build/upload/
verification cost. Relative-humidity recovery additionally needs a supported,
size-bounded append/publication request, its bootstrap binding, pointer/generation
identity and independent normal-reader readback. Until then the broad builder
acceptance and every still-pending phase remain open except the explicitly
superseded standalone staging deliverable.
