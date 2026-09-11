---
type: track-evidence
status: preparation-only
recorded_on: 2026-09-11
---

# Older MTBS recovery preparation

The separately inventoried 1984–2017 population has been freshly captured and
prepared locally. This is a completed preparation slice, not admission, publication,
availability, serving, historical-completeness or relation-retirement evidence.
The [machine receipt](mtbs-older-preparation-20260911.json) records the exact inputs
and outputs. Independent source review, the integrated checks and complete local
artifact replay are recorded in the [final repair review](independent-repair-review-20260911.md).
The implementation lane itself ran no pytest, lint or type-check sweep.

## Fresh source evidence

The source capture queried the fixed Forest Service MTBS boundary service inside
[-125,42,-111,49], separately for every year from 1984 through 2017. It ran from
**2026-09-11T18:05:26.276965Z** through **18:07:58.441138Z**, preserving 276 decoded
HTTP entity responses, 129,060,321 referenced response bytes and 202 unique response
blobs. All **3,077 fires** and every yearly count match the saved September 11
04:56:18 UTC regional inventory. The matched counts do not prove that upstream has
finished mapping every older fire season.

The fresh manifest SHA-256 is
`254c92c3d6a1ec243431300a8f7f4fb2125deed92cb4ca347ab53cb737ca4f72`;
canonical source-content SHA-256 is
`ae349dbe745cf21f091bfa3014b0e3ab8bfd3182f5d58f251aaf5328b11139b6`.
The source directory is
[mtbs-older-capture-20260911-v2](../../../../.omc/research/mtbs-older-capture-20260911-v2).
The first attempt stopped at the sandbox socket boundary and retained a failed
journal without a complete manifest. The second attempt used the authorized
read-only public-source capture; neither attempt mutated a PlantGeo service.

Counts and full ordered attribute inventories bracket geometry acquisition. Replay
checks every saved response hash, query role/parameters, timestamp, source identity
and canonical source-content digest. Matching attributes on a mutable service cannot
exclude geometry-only revisions inside that interval. Captured bytes are decoded
entities, not compressed wire bytes. The manifest explicitly leaves upstream
population and fire-season completeness uncertified.

## Candidate and preservation packet

The ordinary MTBS normalizer, geometry repair and simplification prepared the
registered 23 columns at every rung, split by ignition year to bound each geometry
operation. All 136 parts retain the exact source fire-ID population and immutable
manifest binding. Original nullable measured values remain nullable.

| Rung | Fires | Year parts | Parquet bytes | Part-receipt list SHA-256 |
| --- | ---: | ---: | ---: | --- |
| z13 | 3,077 | 34 | 44,306,450 | `771dc770632b4b0641e310b39fc41ae334aafc56cab06dc7805fdfec75d08f81` |
| z9 | 3,077 | 34 | 994,403 | `e25db17e61b4a2b66d458105f09b6357b0c080d224bcfa11c41363b83b10a735` |
| z5 | 3,077 | 34 | 691,499 | `84b1728c8852d93eec1989472817738395b4237ab4c0d65ef9bdcfc25ff6bf78` |
| z0 | 3,077 | 34 | 690,713 | `3d16e0a741c1d2ad451e01602523d270089f8c3ac1f3bca923fa70c54d87ec29` |

List digests hash canonical JSON for the exact per-year part receipts in
[preparation.json](../../../../.omc/research/mtbs-older-prepared-20260911-v1/preparation.json).
That file has SHA-256
`58dbbbd0415cee8b33cd839bdfcefd90a857923b5cd97154990373d0eff003a4`.
The exact older fire-ID digest is
`101922e57f6f484423c0c4cebe464fd484432b37549eb57d9e5c0c735b73b016`,
using SHA-256 of canonical JSON of sorted fire IDs. Total artifact bytes including
the source manifest are 46,853,605. Hash/schema/count/year/identity/date checks ran
against the resulting local parts. The subsequent independent replay reproduced
the complete preparation receipt and all 115 unique stored blobs, 46,457,989
bytes, including every logical year/rung part. The referenced-byte total above
counts identical content-addressed blobs more than once. The original candidate
and recovery-packet bytes remain unchanged; the final review supplements their
historical preparation-only verification statement.

The [recovery packet](../../../../.omc/research/mtbs-older-prepared-20260911-v1/recovery-packet.json)
has SHA-256
`12201c7540d487fdd26ec73ceccb376857e6b4a17973f8ebba9fdfbfcb992424`.
It retains `apply_authority=false` and `candidate_prepared_admission_blocked`.
It also binds a fresh local reproduction of the preserved **747-fire** current
capture, manifest `4690af4629e617ffbde3f5e6ae99be3eb4c1a536fe47a931d7f0a8456c8f6468`.
Every original z13/z9/z5/z0 artifact hash matches the
[current rollout](mtbs-live-rollout-20260911.md). This verifies local preservation,
not today's production objects or availability head. Its 2018–2026 full replacement
scope and explicitly partial 2023–2026 seasons are unchanged.

The complete older raw capture, candidates, packet and inventory receipts are also
preserved in a local verified archive:
[mtbs-older-recovery-evidence-20260911.tar.gz](../../../../.omc/research/mtbs-older-recovery-evidence-20260911.tar.gz),
91,355,303 bytes, SHA-256
`a2ad36f0d9da1f76c50d1d58f5a592c4ec44359c7a7db76ce48ee7ffe26f35fb`.
All 324 regular members and their 174,028,580 uncompressed bytes were compared with
their original hashes after archival. The
[archive receipt](../../../../.omc/research/mtbs-older-archive-receipt-20260911.json)
records local preservation only. These ignored workspace artifacts are not bundled
by committing this evidence document and are not remote archival proof.

## Admission boundary and exact owner handoff

Prospective availability is **2026-09-12**, the UTC day after this fresh capture
closed. These source bytes cannot be backdated to ignition, an old announcement or
the September 11 current rollout. The distinct `mtbs-older-recovery/v1` manifest
and `mtbs-older-recovery:<manifest-sha256>` row identity are deliberately rejected
by current snapshot v1. Neither the existing publisher nor an annual-release writer
may be pointed at these candidate files as a shortcut.

The required next patch crosses the current product/serving boundary:

1. Define a separate older descriptor beside `warehouse/mtbs_snapshots.py`'s
   current descriptor. Retain exact v1 validation for 2018–2026; bind the older
   1984–2017 scope, actual capture interval, D+1 availability, 136 part receipts and
   unassessed fire-season completeness without inventing an annual release date.
2. Extend `parquet_ops/mtbs_snapshot_catalog.py::load_latest_mtbs_snapshot` and
   its serving integration to select the latest eligible capture **per disjoint
   year scope**, preserving the current component when an older component is
   admitted. Full replacement applies within each component: a valid empty older
   replacement suppresses older rows and cannot trigger fallback. Historic selected
   days retain their existing release/refusal/truncation semantics. The frontend
   descriptor contract needs the same explicit composition; it is outside this lane.
3. The gapless owner must choose and test the physical/day-index representation
   before staging: current and older populations cannot claim the same lane/day/rung
   independently or overwrite each other. A different day on this preparation is
   not a general collision policy. Bind every year part through the ordinary
   publication barrier, lane-day lock, terminal evidence and availability generation.
4. Re-read exact deployed revisions, active definitions/leases, target physical
   objects, completion/absence receipts and the availability head. Preserve those
   original bytes and pointers before requesting exact staging/publication authority.
   Rollback affects only the new older admission and its owned objects; it retains
   current and historical components and never restores PostgreSQL ingestion or a
   Railway cron. The local packet intentionally leaves those production pins open.
5. Production acceptance needs actual selected-day and adjacent-day replies at
   z0/z5/z9/z13, regional and temporal agent neighbours with explicit distances,
   current/older identity conservation, empty-replacement behavior, historical
   truncation and cold/warm evidence on the exact deployed revision. No such claim
   follows from these local artifacts.

The parent task's fresh runtime receipt at 18:02:11 UTC reports deployed `fa20223`
and a successful September 11 08:55 MTBS scheduled run ending 08:55:30.347203 UTC.
That is later schedule-execution evidence, not three publication advances and not
older recovery. See
[runtime readback](../../../../.omc/research/retirement-runtime-readonly-20260911.json).
The retained PostgreSQL reconciliation/parity paths in
`pipeline/validation/burn_severity.py`, `pipeline/lanes/burn_severity.py` and
`pipeline/direct/burn_severity/parity.py` still need their own current reader and
retirement proof. No relation or historical caller was removed by this preparation.
