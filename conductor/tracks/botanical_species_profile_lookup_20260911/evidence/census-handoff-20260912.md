---
type: handoff
track: botanical_species_profile_lookup_20260911
status: implementation-retained-census-and-source-admission-blocked
---

# Botanical census and source-admission handoff

The implementation remains commit `edc6afdeb23f339b40049ec0828551c2fe1a4d45`,
tree `01ad55b6220a13604e8fbf8a4ceda773e35d5658`, on
`codex/botanical-species-profile-lookup`. Its original clean handoff is historical
evidence. On 2026-09-12 at 07:28:53 UTC, the branch still pointed to that commit,
but the original `0caf/plantgeo` directory had no Git metadata and was no longer
a registered worktree. No botanical/WCVP process matched the bounded local
process inventory. This update uses an isolated documentation branch and changes
no API, agent, database, source descriptor or Parquet data.

## Evidence retained

[The reconciliation receipt](census-reconciliation-20260912.json) records the
earlier 03:50:36 UTC inspection, including the exact original receipt and portable
object hashes. The implementation, five portable Parquet objects (55,323 bytes),
and original receipts survive in Git and were rehashed from the implementation
commit. Tests were not rerun. The original full Python result remains 6,086 passed,
150 skipped and one expected failure on its recorded service digest.

The canonical local intake and verification are separately retained in commits
`32a13c604e6da4331051e6a584f7942e7165af1e` and
`2317ee3a2ac35de489d27503dfec91ea3285bd66`. That integrated checkpoint certifies
its stated tested commit `416342fa5bea836e6a062549b87c2706f53ce45e`; it does not
certify later integration trees or production.

## Census blocker

The authenticated Railway metadata read identified production service
`plantgeo-spatiotemporal-db`
(`1e166530-9c8a-4d4a-b685-a70c801fc449`) in project
`6faaf3ea-ac46-4c8b-bbfe-1351dbb9d990`, environment
`b7cfa813-8a5c-4fcd-80f2-cab736d840a7`. Its public proxy metadata was active.
This establishes a scoped platform target, not a database connection or census.

Automatic approval review rejected the attempted private environment DSN
inspection before execution, stating that the trusted transcript did not
authorize private environment or production credential access. No alternate
credential path was attempted. The latest continuation explicitly prohibits
private credential access; this documentation pass makes no further attempt.

No database connection or query ran. Usable DSN, database read-only privileges,
reachability, actual database name, deployed schemas, table presence, counts,
review-state columns and review-state distributions remain unknown.
[The original failed census receipt](railway-census.json) remains unchanged.

A future authorized census is limited to metadata/counts/review-state evidence
for `agri.species` and `agri.companion_relationships`, using a verified PlantGeo
target and bounded read-only transaction controls. The proposed controls in the
new receipt were not executed. A successful count census alone cannot establish
full reviewed-row preservation or source admission.

## Source acceptance and custody

The four-taxon WCVP bundle remains an **unaccepted local candidate**. Local
code/API/agent acceptance cannot supply an independent source-admission verdict.

The original ZIP's recorded SHA-256 is
`d32ea2b3a85e489b14e83bcc9eae7274532e1d113753f7be290d4b2dfde573fa`
and recorded length is 88,179,649 bytes. It was absent at the four locations
listed in the reconciliation receipt. Custody elsewhere is unknown; no claim of
global loss is made. Its current bytes were not reverified or downloaded again.
Locate an existing retained copy and verify its recorded hash before claiming
raw-source custody. Original local gate-log/JUnit custody must likewise be
resolved before cleanup; committed summaries and the separately archived
canonical integration evidence remain available.

## Remaining gates and integration instructions

1. Obtain explicit authorization and usable, correctly scoped credentials for the
   bounded read-only production census; append a new dated result without
   rewriting either historical census receipt.
2. Preserve existing relational UUIDs and reviewed evidence, with reviewed
   canonical crosswalks, source versions, licences and measurement context.
3. Obtain an independent source-admission verdict bound to the original WCVP
   source and candidate hashes. Keep absent traits and unsupported effects unknown.
4. Resolve raw ZIP and original local verification-evidence custody without
   duplicate download, ingestion or data loading.
5. Keep data publication, production readback, deployed HTTP/MCP acceptance and
   scientific recommendation gates with their respective owners.

The implementation can be consumed from its existing commit without repeating
its intake. This documentation update does not close the broader profile or
source-admission tracks and does not set production integration true. The source
track's original `next_gate` still asks for local verification/commit; that step
is complete, but its hash-bound historical metadata is preserved. This handoff
records the current disposition without rewriting the original source evidence.

No source ingestion, database write, migration, object-store publication,
deployment or new implementation commit is part of this update.
