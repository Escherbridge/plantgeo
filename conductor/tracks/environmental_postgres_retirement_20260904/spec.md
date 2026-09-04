---
type: track-spec
slug: environmental_postgres_retirement_20260904
status: active
---

# Environmental Postgres retirement — backfill, cut over, drop

## Purpose

PostgreSQL becomes a database of feed and social features only. Every environmental relation, every
matview over one, and every job that fills one is removed once its Parquet equivalent is proven. In
the same lane the time slider stops paying a whole-stream coverage census at startup.

This track is the dedicated execution lane for the owner direction of 2026-09-04 ("PostgreSQL is for
the feed and social features only; remove the objects for the environmental data and drop support for
any job fills entirely"), refined by the second grill the same night. It supersedes the
`postgres_shrink_ingest_repoint_20260825` P5/P6 scope and absorbs the retirement charter that track's
plan carries. It does **not** supersede `gapless_parquet_publication_20260901` (forward publication
ownership) or `parquet_production_acceptance_20260901` (the acceptance verdict), both of which remain
the sources of the evidence this track consumes.

## Starting state (observed, not assumed)

- Executor `plantgeo-job-executor` runs `152feca` as the sole scheduler, `active_lane_count=26` after
  step 1b. The ten `postgres-*` lanes and `soilgrids-cache-warm` are `shadow`.
- Because of that stop, **vegetation NDVI, weather-observations and drought no longer advance**, and
  fire-perimeters, sensors, watersheds and evacuation-zones are frozen. Their freeze days are owed to
  `gapless_parquet_publication_20260901/evidence/post-deploy-tick-2026-09-03.md`.
- No lane has an availability receipt. `PARQUET_COVERAGE_AUTHORITY` is still `census_until_bootstrap`,
  so the first coverage request after an API deploy takes ~28 s against an 8 s app timeout and every
  lane is withheld until the memoized census warms.
- Martin's tile functions and the agent signal SQL still read PostgreSQL.

## Owner decisions — 2026-09-04, second grill. Settled; implement, do not re-open.

### D1 — Per-layer drop on a three-part proof
A relation may be dropped as soon as, and only as soon as, all three hold and are recorded in a proof
packet under `evidence/drop-packets/<relation>.md`:

1. **Parity receipt** — a counted comparison showing the Parquet twin covers at least every day and
   row the PostgreSQL relation holds. Under-coverage is a blocker, not a note.
2. **Zero readers** — a repository-wide proof of no remaining reference from the Next.js app, the
   agent SQL, Martin's tile functions, the CLI, or tests. Follow the c2 removal-packet form already
   used by `repository_conformity_hardening_20260901`.
3. **Archived snapshot** — a `pg_dump` of the relation written to R2 under a retirement prefix, with
   its key and sha256 in the packet, so each drop is individually reversible.

Drops land as several small Alembic migrations, one per layer or per coherent object group, each
rehearsed on the disposable `agri_sweep` database before production. This replaces the recorded
"one Alembic migration after a GREEN verdict" plan; the acceptance verdict still governs whether the
product is called done, but it no longer gates an individually proven drop.

### D2 — Backfill bar is parity with PostgreSQL, plus a governed gap census
A layer's backfill is complete when Parquet covers at least what PostgreSQL currently holds for it,
proven by a counted comparison, and forward writes are live. Days upstream never served are recorded
as a governed gap census — they are evidence, not blockers. Full declared history horizons are **not**
a precondition for a drop; where a horizon is longer than the PostgreSQL holding, the remainder is
recorded as owed and continues under the gapless track.

Rationale: parity is exactly the bar that makes the drop lossless. A stricter bar blocks the shrink on
upstream availability; a looser one silently shortens the time slider.

### D3 — Availability bootstrap: manifest-trust for history, real digests forward
`scripts/compile_availability_bootstrap.py` hashes parts for a recent window and **trusts recorded
manifests and completion markers for older days**, recording the weaker provenance explicitly in the
receipt. `foundation/parquet/completion.py` gains real per-part digests so newly written days carry
full proof.

**Corrected 2026-09-04 by the wave-A adversarial review — the original sentence "and the trusted region
stops growing" was an overclaim and is withdrawn.** It holds for DERIVED rungs only. The base rung —
the rung that holds the rows — still writes v1 markers from `gap_fill.py::_finalize_written_day`,
because `LaneRunResult` folds each adapter's receipts into three integers and discards
`relative_path`/`sha256` before they reach the marker. Two consequences, both recorded rather than
hidden: forward publication is unaffected (`_rung_objects_from_ledger` builds `data_receipts` from the
open written-object ledger, which carries real digests for every rung including the base, so any day
published forward is fully digested regardless of its marker version); but for days written while a
lane is un-bootstrapped — which is every lane today — and on any future re-compile or
disaster-recovery re-bootstrap, the base rung outside the digest window is manifest-trusted
**permanently**, because no artifact will ever exist that could prove it cheaply. Unlock condition is
track lane A1c. A promise the code does not keep is what makes the next reviewer stop checking.

**The honest boundary of a manifest-trusted row**, so nobody has to re-derive it: it proves the
completion marker exists, hashes to its cited sha256, re-serializes byte-identically, agrees with the
row on `row_count`, and — at compile time only — that the listing held exactly `part_count` parseable
part objects. It does NOT prove the content of any part, that the parts still exist at `--apply` time
(the receipt loop iterates an empty tuple and passes vacuously), or that the parts hold the rows the
marker claims. Write access to a single ~200-byte marker controls a day's advertised row count with no
cross-check against the megabytes it speaks for. That is narrower than the tripwire forbids — no
fabricated digest is emitted and the class is labelled in the receipt — but "only the megabyte parts
are skipped" undersells it: what is skipped is all evidence about the parts.

The bootstrap-input contract
(`pipeline/parquet/availability_index.py:937-985`, `:2409-2466`) is extended, not bypassed: a
manifest-trusted row is a declared, distinguishable provenance class, never a fabricated digest.

Rationale: the contract as written requires downloading and hashing every part of every lane-day —
for `fire-detections` that is every day since 2000-11-01 at every rung. That cost would push the
startup fix behind the whole cutover.

### D4 — Scope is the full cutover
All eight ingest-first layers get direct-to-Parquet writers (drought, weather-observations, vegetation
NDVI, fire-perimeters, sensors, watersheds, evacuation-zones, burn-severity); Martin's four tile
functions move to PMTiles or the Parquet API; the agent signal queries move to the Parquet API. This
is the only scope under which a full environmental drop is possible, so it is the scope of this track.

## Definition of done (owner goal, 2026-09-04)

Verbatim: *full cut over to parquet and only social features data and objects in the postgres db; full
coverage in parquet set up and performant by having the right rungs in place; all layers should be
serving from parquet; legacy code used in the postgres environment should be removed.*

Four acceptance criteria, each needing evidence before this track closes:

1. **Postgres holds feed and social objects only.** No environmental relation, matview, plane or fill
   ledger survives except the executor's `agri.job_*` checkpoint family (until an object-store
   scheduler ledger replaces it). Proven by the A3 inventory re-run at the end, showing every row in
   the "drop now" and "drop after Parquet proof" classes discharged.
2. **Every layer serves from Parquet.** No environmental read path touches Postgres: not the app, not
   the agent tools, not Martin's five tile functions. Proven by browser and agent parity evidence per
   layer, plus a zero-reference grep.
3. **Coverage is complete AND the rungs are right.** Each layer's Parquet ladder carries every rung its
   renderer asks for at every zoom it is asked at, with no lane-day holes hidden by a census that walks
   only one tier. "Performant" is measured, not asserted: coverage answers without a whole-stream LIST
   (the A4 tripwire) and tiles render at the default camera within budget at coarse zooms.
4. **The legacy code is deleted, not merely unused.** Every Postgres fill command, its SQL, its lane
   spec and its tests are removed with a c2-style removal packet proving zero imports. An orphaned
   module that nothing calls is not done; it is the next reader's trap.

## Non-goals

- The feed and social tables on the Drizzle side. Untouched throughout, permanently.
- The executor's own `agri.job_*` checkpoint ledger. It is the one job-table family that stays until an
  object-store scheduler ledger replaces it — out of scope here.
- Re-litigating the pivot, the bridge-then-cut pacing, or the lane stop. All settled.

## Tripwires

- No relation is dropped without its three-part packet. A missing archived snapshot is a hard stop.
- A parity comparison that under-covers is a blocker; it is never waived by a note.
- A manifest-trusted availability row is labelled as such in the receipt. Never emit a digest that was
  not computed from the object it describes.
- A source handoff pauses and proves the old owner inactive before activating the new owner
  (inherited from the gapless track; the `postgres-*` lanes are already `shadow`).
- Railway variable edits, `--apply` operator verbs and any production migration are owner-confirmed
  actions. Agents prepare and dry-run them; they do not fire them unattended.
- Never run PlantGeo locally. Prod DSN and live Martin only.
