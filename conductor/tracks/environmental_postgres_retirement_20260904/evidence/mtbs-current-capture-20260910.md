---
type: track-evidence
---

# Current MTBS capture and offline preparation

Status: captured and prepared locally; independent source review and final integrated
validation passed. Publication is pending. This evidence does not close burn-history
completeness or the retirement track.

## Cause and bounded source scope

The old forward job revisited five completed fire-year cohorts, 2018–2022. Its newest
historical release date was 2024-08-22, so successful scheduled runs could not admit
the newer partial fire years or replace old records with current source revisions.

The current-source acquisition uses the existing executor footprint
`[-125, 42, -111, 49]`, years 2018–2026, and the fixed Forest Service MTBS map service.
The source is mutable: this is a bounded capture interval with matching count and
attribute inventories before and after geometry acquisition, not an upstream
transactional snapshot. Source completeness means the captured query population only.
The separately inventoried 3,077 fires from 1984–2017 remain outside this preparation.

## Preserved capture

The successful third attempt ran from **2026-09-10T20:15:57.678058Z** through
**2026-09-10T20:17:58.186463Z**. It completed 70 HTTP requests and preserved
135,293,670 decoded HTTP entity bytes, including the original decoded response bodies
and request/status/timing/hash receipts. These are not compressed wire bytes.

| Fire year | Captured records |
| --- | ---: |
| 2018 | 150 |
| 2019 | 63 |
| 2020 | 100 |
| 2021 | 115 |
| 2022 | 113 |
| 2023 | 69 |
| 2024 | 130 |
| 2025 | 6 |
| 2026 | 1 |
| Total | 747 |

There are 206 records in the newer, explicitly partial 2023–2026 years. Counts do
not establish that upstream has finished mapping those years. Manifest SHA256 is
`4690af4629e617ffbde3f5e6ae99be3eb4c1a536fe47a931d7f0a8456c8f6468`;
canonical source-content SHA256 is
`b8652f3384493249d568f4092e78ab7716689b49097882efe8dcbc5ff4a6cb1e`.

The [saved source directory](../../../../.omc/research/mtbs-current-capture-20260910-v3)
holds the manifest, journal and content-addressed bodies. Two refused partial attempts
remain separately preserved: each completed 19 requests / 1,440,052 decoded bytes.
They exposed JSON/GeoJSON numeric serialization differences and produced no complete
manifest or publication. The final comparator accepts finite numerically equal JSON
numbers and a narrowly bounded latitude/longitude serialization tolerance; it still
requires identical property keys, values and ordered feature identities.

## Offline artifacts and time semantics

The ordinary geometry preparation produced the existing 23-column schema and four
resolutions, with **747 records at each resolution**. No environmental PostgreSQL
reader, local PostgreSQL server, bucket write or serving mutation was used.

| Resolution | Bytes | SHA256 |
| --- | ---: | --- |
| z13 | 47,081,269 | `9bcbeaf3d9afcc661a78a2fae06bb44ac057eee4149512981b9c4992101dc425` |
| z9 | 193,363 | `dee2d7d66c3fc18ffa380f237169eff3dda1bc53d2259aeb9d467f6e6ea98f29` |
| z5 | 108,578 | `d0c6c686f4dce1ca4094cbb16f3f3b4375a0f6969bd7b5461fba459b45014984` |
| z0 | 108,354 | `beb687772180d3d1fb627ff78fa013c335c7f317f68557c4bd7801e84fd61edf` |

Total prepared artifact bytes: 47,535,234. The
[preparation receipt](../../../../.omc/research/mtbs-current-prepared-20260910-v3/preparation.json)
retains `apply_authority: false`. The
[independent offline verification](../../../../.omc/research/mtbs-current-independent-verification-20260910-v3.json)
passed in 35.89 seconds. It verified all 70 raw response hashes and receipt roles,
counts, identities, attributes and capture times; all four complete 23-column Parquet
artifacts; and every derived value against an independent replay of the ordinary
geometry rules. All 747 geometries at every resolution are valid, nonempty polygons.
This was artifact verification, not the final source test suite or publication authority.

Availability is **2026-09-11**, the day after the actual UTC capture ended. It is not
backdated to an upstream announcement, a fire ignition date, or the old cohort release.
Once admitted, the snapshot replaces the entire covered 2018–2026 population, including
source corrections and withdrawals. Older selected days retain historical release
semantics. An empty valid replacement must suppress older records rather than fall back.

## Sustained operation and cleanup

The locally implemented replacement uses a bounded weekly source check and a cheap daily
eligible-stage publication check. Staging alone cannot authorize serving. Publication
must replay or equivalently verify the saved source, use the ordinary day locks,
publication barrier, four-resolution writer/finalizer, and availability generation,
and verify the complete result before acknowledging the staged work. Unchanged canonical
source content skips Parquet derivation and does not generate a duplicate publication
solely because time advanced. The daily schedule is 08:55 UTC. The complete worker
lifecycle has a hard time limit; current-source work precedes historical repair.

The supported `scripts/stage_mtbs_current_snapshot.py` command verifies an existing local
capture and its actual four artifact blobs by default. Explicit `--stage` archives and
enqueues them without publishing serving partitions. The publisher and stage loader
share the serving descriptor's ownership floor. Independent source review is CLEAR.
Focused interrupted-write and local-blob preflight regressions are authored. The first
integrated Python gate found fixture/expectation, lint and typing failures. The complete
fix batch passed independent review. The final Python gate passed formatting, lint,
type checking and the full test suite; pytest took 185.34 seconds. Its receipt binds
1,307 files with tree digest
`sha256:142a9dc64510af1e96e4c75c3afd5b7a78a7f68295d6cc11e6ce61149c6e2151`.
The [final gate log](../../../../.omc/research/mtbs-release-integrated-gate-green-20260910.log)
is retained alongside the earlier failures. The complete
application gate passed: 144 test files / 2,151 tests, with 2 files / 13 tests skipped,
plus boundary, TypeScript and lint checks. These results do not authorize publication.

The map and regional AI context now share the governed selected-day reader in the local
change batch. The reader labels publication availability separately from capture time
and ignition dates. Independent source review and final tests are complete; deployment
and publication are pending. Retired PostgreSQL exporter and TypeScript live-ArcGIS reader evidence is
preserved in the [cleanup proof](../../../retros/parquet_cutover_completed_slices_20260910/cleanup-proof.md).
Historical reconciliation and still-needed correction operators remain.
