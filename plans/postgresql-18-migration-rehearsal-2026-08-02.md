---
type: rehearsal-report
---

# PostgreSQL 18 migration rehearsal and cross-major schema parity

Authored 2026-08-02. Prerequisite engineering for a later Railway deployment.
**No Railway resource was touched, no deployment was attempted, and no database
proxy was created.** Everything below ran against a throwaway local container.

## 0. Summary

| question | answer |
| --- | --- |
| Do all four required extensions exist on `timescale/timescaledb-ha:pg18`? | **Yes**, all four, at newer versions (§1) |
| Do migrations `0001 → 0016` apply on PostgreSQL 18? | **Yes**, clean, no error, no schema change needed (§2) |
| Does the behavioural contract suite pass on PostgreSQL 18? | **Yes**, 297 passed / 5 skipped (§3) |
| Is the schema byte-identical across the two majors? | **Yes**, after two enumerated, individually justified normalisations (§4) |
| Does a populated pg16 dump restore into pg18? | **Only after two blockers are cleared** — one cross-major, one latent and major-independent (§10) |
| Once restored, is the data identical across majors? | **Yes**, every compared probe byte-identical, including recomputed checksums (§10.3) |
| Do the `AS MATERIALIZED` CTE paths survive the major jump? | **Yes** — the hoist holds; the timing comparison is directional only, the two servers are not configured alike (§11) |
| Did the rehearsal find anything unrelated to PostgreSQL 18? | **Yes, and it outranks the migration** — an undocumented manual step in the restore path, and a checksum-determinism defect (§10.2, §10.2.1) |
| Is a Railway migration safe on this evidence? | **Not yet — but for a different reason than before.** (§7) |

## 1. Extension parity (measured, not assumed)

Both rows read from `pg_available_extensions` on the actual images.

| extension | local pg16 (`plantgeo-spatiotemporal:pg16`) | pg18 (`timescale/timescaledb-ha:pg18`) | delta |
| --- | --- | --- | --- |
| `postgis` | 3.6.3 | **3.6.4** | patch |
| `timescaledb` | 2.27.0 | **2.29.0** | two minors |
| `vector` | 0.8.2 | **0.8.5** | patch |
| `pgcrypto` | 1.3 | **1.4** | one minor |

Server: PostgreSQL 16.14 vs **18.4** (the coordinator's brief said 18.3; the
image ships 18.4 — stated because it is a difference from the premise, not
because it changed any result).

All four are present, so `timescale/timescaledb-ha:pg18` is a viable warehouse
host. This was checked because Railway's other Postgres service (plain
`postgres-ssl:18`) offers neither `postgis` nor `timescaledb`, making the
Timescale image the only candidate. The pg18 image additionally offers
`postgis_raster`, `postgis_topology`, `postgis_sfcgal`,
`postgis_tiger_geocoder` and `timescaledb_toolkit`; none is required and none
was enabled.

**Risk assessment of the deltas.** The `timescaledb` 2.27 → 2.29 jump is the
largest on paper and the smallest in practice: the `agri` schema contains
**zero hypertables** on either server (`timescaledb_information.hypertables` =
0). TimescaleDB is installed because the reviewed extension gate installs it,
but nothing in the schema depends on it today. `pgcrypto` 1.3 → 1.4 is the
delta that actually mattered to check, because every receipt checksum in the
warehouse is a `digest(..., 'sha256')` call; it was verified byte-identical
(§5). PostGIS and pgvector moved by a patch level only.

## 2. Migration rehearsal: `0001 → 0016` on PostgreSQL 18.4

Setup: a dedicated container on `127.0.0.1:5445` (the pg16 warehouse on 5442
was not touched), the eight pre-migration roles recreated to mirror the local
warehouse so the role-guarded `GRANT` paths in `0010`/`0012`/`0015` actually
fire instead of being skipped, then `infra/local-warehouse/enable-extensions.sql`,
then `alembic upgrade head`.

**Result: all 16 revisions applied with zero errors.** Nothing broke — not
syntax, not extension versions, not removed or changed built-ins, not
`SECURITY DEFINER` or role handling, not generated columns, not the partition
attach, not the `WITH NO DATA` materialized view.

There is therefore no `0017` to propose **on DDL-portability grounds**. The
schema is portable as shipped.

> **Superseded in part by §10.2.** This paragraph was written from a DDL-only
> rehearsal against an empty database, and a later data-restore rehearsal found
> a defect that a DDL rehearsal structurally cannot see: the generated-column
> checksum functions resolve `digest()` through `search_path`, and `pg_restore`
> runs with `search_path = ''`. A `0017` *is* warranted — not because the DDL
> fails to apply, but because the resulting database cannot be restored from its
> own dump. The failure is not specific to PostgreSQL 18; it reproduces on
> pg16 → pg16.

## 3. Behavioural contract suite on PostgreSQL 18.4

Pointing `AGRI_TEST_DATABASE_URL` at the pg18 database (deselecting only the
canonical-major parity test, which by design refuses a non-canonical server):

```
297 passed, 5 skipped, 1 deselected in 45.79s
```

The 5 skips are the pre-existing structural exemptions plus the new
cross-major marker. This exercises checksum determinism, immutability and
finalization triggers, the `SECURITY DEFINER` lockdown, leakage gates, the
0016 covariate layer and the strategy-selection refusal paths **on pg18** —
substantially stronger evidence than "the DDL applied".

## 4. Cross-major schema parity: what was built

`pg_dump` output is stable only within a major, and `pg_dump` cannot dump a
server newer than itself. So:

- **PostgreSQL 16 stays canonical and byte-exact.** The existing guarantee is
  untouched. `test_declarative_tree_matches_migrations` still compares
  byte-for-byte and now *additionally* asserts the raw dump requires no
  normalisation at all, plus that the dumper is not older than the server.
  `regenerate.py` now refuses to run against a non-canonical major, so a
  foreign dump dialect can never be written into the reviewed tree.
- **Cross-major is a separate, opt-in test.**
  `test_cross_major_tree_matches_migrations`
  (`AGRI_CROSS_MAJOR_DATABASE_URL`, marker `agri_db_cross_major`) rebuilds the
  tree from the foreign server and requires the same byte-exact match after
  exactly two rules.

**Byte-exactness across majors turned out to be achievable**, which is why it
was implemented rather than a weaker semantic comparison. Measured: the raw
pg16-server and pg18-server dumps differ by **33 schema lines** (19 pg18-only,
14 pg16-only) across **three** classes, and after handling them the pg18-built
tree is **byte-identical to all 265 committed files**.

Both files are pure LF after banner-stripping, so none of this is a
line-ending artifact. An earlier draft of this report said "52 diff lines, all
in two classes"; 52 was `diff | wc -l`, which counts `NNcNN` hunk headers and
`---` separators as well as the differing lines themselves. Re-measured:

| class | lines | handled by |
| --- | --- | --- |
| `\restrict` / `\unrestrict` markers | 2 | rule 1 |
| inline named NOT NULL | 14 + 14 | rule 2 |
| `SET transaction_timeout = 0;` (+2 blank-line churn) | 3 | neither -- inert |

The third class needs **no rule**: pg18 emits `SET transaction_timeout = 0;` in
the dump preamble, and `split_schema.parse_blocks` discards everything before
the first object header, so it can never reach an object file. It is recorded
here so that anyone auditing the two rules against a raw diff finds the third
difference already accounted for rather than unexplained. The same line also
appears when a **pg17** client dumps the **pg16** server (§9.2).

### Rule 1 — `\restrict` / `\unrestrict` markers

pg18's `pg_dump` brackets its output with psql meta-commands carrying a random
token (`\restrict NCBMoeTj3...`). Not DDL, and non-deterministic, so they can
never be compared. The closing marker lands *after* the final object header and
would otherwise be swept into the last object's file — this is a splitter
correctness fix, not a cosmetic choice.

### Rule 2 — inline NOT NULL constraint names

PostgreSQL 18 stores NOT NULL as real `pg_constraint` rows; PostgreSQL 16 does
not. Measured: **722** such rows on pg18, **0** on pg16. pg18's `pg_dump`
prints an explicit `CONSTRAINT <name>` only when the stored name is not the one
a restore would derive (`<table>_<column>_not_null`). Measured: exactly **14**
cases, and the dump's 14 inline names match the catalogue's 14 non-derivable
rows one-for-one. Two causes:

- **6 truncated at 63 characters** (`NAMEDATALEN`), e.g.
  `forecast_hindcast_run_uncertainty_calibration_cutoff_t_not_null` from the
  66-character natural name. Note that a name landing on *exactly* 63 without
  truncation (`strategy_outcome_definition_smallest_meaningful_effect_not_null`)
  is still derivable and is *not* emitted — the criterion is derivability, not
  length.
- **8 on `job_event_default`**, the partition child, which inherits its
  parent's constraint identity (`job_event_id_not_null`) rather than deriving
  its own (`job_event_default_id_not_null`).

**Why this is not a blanket regex.** The pattern is anchored to a column-level
`NOT NULL` and can only ever delete a *name*. It cannot hide a NOT NULL
appearing or disappearing, a type change, a default change, or a table-level
constraint — every one of those must still match byte-for-byte afterwards. And
the test asserts the set of names it removed **equals** the set of
non-derivable `contype = 'n'` rows read from that server's catalogue, so if the
rule ever removed something that is not a pg18 catalogue artifact, or missed
one, the test fails. The names it drops do not exist on pg16 in any form, so
nothing verifiable is lost.

### Running it

`pg_dump` 16 and 17 both refuse a pg18 server (verified: the test fails with a
clear message rather than a confusing libpq error). Since no pg18 client is
installed on the host, the dump is delegated to the client inside the server's
own container:

```
AGRI_CROSS_MAJOR_DATABASE_URL=postgresql://plantgeo_owner:***@127.0.0.1:5445/plantgeo_pg18_rehearsal
AGRI_CROSS_MAJOR_DUMP_DSN=postgresql://plantgeo_owner:***@127.0.0.1:5432/plantgeo_pg18_rehearsal
AGRI_CROSS_MAJOR_PG_DUMP_ARGV=["podman","exec","-i","plantgeo-pg18-rehearsal","pg_dump"]
```

A useful incidental finding: a **pg17** client dumping the **pg16** server
differs from the pg16 client by exactly one preamble line
(`SET transaction_timeout = 0;`), which the splitter already discards, so that
combination produces a byte-identical tree with no rules at all.

## 5. Semantic fingerprints, pg16.14 vs pg18.4

Every one of these was identical on both servers:

| probe | value (both) |
| --- | --- |
| `digest('plantgeo|checksum|probe','sha256')` | `f2faaaf1bf1c377cef987d550cfbd0c44c69e4f9e1aff27fd61611f2d7b88b5f` |
| pinned float/date/interval rendering | `0.3333333333333333\|0.5675000000000001\|2026-06-24T00:00:00.000000Z\|1 day\|2026-06-24` |
| `covariate_feature_schema` name digest | `f017d065506f92b2d42c7f29d89b8fc0` (40 features, lookback 28) |
| routine digest (name+args+`prosecdef`+`proconfig`+`md5(prosrc)`) | `811dafee7bebd845fcd504bd7745f953` |
| inventory | 68 tables, 1 partitioned, 4 views, 1 matview, 197 indexes, 93 routines, 12 `SECURITY DEFINER`, 82 triggers, 288 CHECK, 128 FK, 6 generated columns |

The pgcrypto and rendering rows are the load-bearing ones: they mean a receipt
checksum computed on pg16 re-verifies on pg18. Had they differed, every
finalized receipt would have become unverifiable after a migration.

## 6. What this does **not** prove

1. ~~**No data restore was rehearsed.**~~ **Discharged by §10** (2026-08-03).
   As originally written: this is a *migration* (DDL) rehearsal against an empty
   database, and restoring a populated pg16 dump into pg18 was not attempted, so
   only the first half of the pilot's "extension parity + restore rehearsal"
   blocker was discharged. §10 completes the second half — and found two
   restore blockers that this DDL-only rehearsal structurally could not see.
2. **No Railway resource was touched.** Railway runs 18.3, this ran 18.4, and
   Railway's networking, TLS, connection limits, and role/permission model were
   not exercised at all.
3. ~~**No performance or planner comparison.**~~ **Discharged by §11**
   (2026-08-03). Identical schema does not imply identical plans, and nothing in
   this section measured them; §11 does, on the restored data, and finds the
   `AS MATERIALIZED` hoist intact with pg18 marginally faster.
4. **Extension *runtime* behaviour was spot-checked, not swept.** PostGIS
   3.6.4 and pgvector 0.8.5 were exercised only to the extent the contract
   suite touches them.
5. **The cross-major parity test is opt-in and unmarked in CI.** It needs a
   second server of a different major; it skips (exempt, announced) otherwise.
6. **A single pg18 build.** One image, one day. Not a matrix.

## 7. Judgement: is a Railway migration safe yet?

**No — but the schema half of the risk is now closed, and closed strongly.**

What is genuinely settled: the migrations apply cleanly on PostgreSQL 18, the
resulting schema is byte-identical to the reviewed pg16 tree under two narrow
and individually verified rules, all four extensions exist on the Timescale
pg18 image, checksums and pinned rendering are stable across the majors so
existing receipts stay verifiable, and the whole behavioural contract suite
passes on pg18. On this evidence I would not expect a schema-level surprise,
and no `0017` is warranted.

§10 and §11 (added 2026-08-03) discharge the two items that used to head this
list. They also moved the blocking risk somewhere new: the highest-priority
item is no longer about PostgreSQL 18 at all.

What still blocks a deployment decision, in priority order:

1. **`0017`: pin `search_path`, and pin the float rendering.** Two defects, one
   revision.
   - The warehouse cannot be restored from its own dump by an unattended
     `pg_restore`, on *any* major (§10.2). Recovery works but requires a manual
     step documented nowhere. It outranks the migration itself: the "keep pg16
     alive as the rollback" plan in item 5 rests on a recovery procedure nobody
     has written down. Scope: **1** function blocks a restore
     (`forecast_iteration_value_checksum`); **21** share the latent pattern and
     should be fixed together.
   - `forecast_hindcast_value_checksum` pins only `TimeZone` while rendering six
     floats through `::text`, so session `extra_float_digits` changes the
     checksum for identical inputs (§10.2.1). Harmless only because the table is
     empty. This is a receipt-integrity defect and must not be left behind by a
     revision that appears to have hardened these functions.
2. **Decide how TimescaleDB is handled at cutover.** A stock dump/restore is
   blocked by the extension's own catalogue, not by our schema (§10.1). Either
   pin the extension version on both ends, or exclude the four `_timescaledb_*`
   schemas at dump time, or — since the `agri` schema has zero hypertables and
   depends on nothing TimescaleDB provides — reconsider whether the reviewed
   extension gate should install it at all. That last option is the only one
   that removes the failure mode rather than routing around it, but it changes
   a reviewed gate and belongs in its own change.
3. **Version alignment.** Rehearsed on 18.4, Railway is 18.3. Re-run against
   the exact target build, or accept the gap explicitly. Unchanged, and now the
   only untested axis of the pg18 side.
4. **Railway-specific surface.** Role/permission model, TLS, connection limits,
   and whether the reviewed manual extension gate can even be run there — the
   foundation revision *refuses to create extensions itself* by design, so an
   operator must run that gate first, and it is unverified that Railway permits
   it. The gate itself is now known to run clean on a stock pg18 image (§10),
   which narrows this to Railway's permission model specifically.
5. **The rollback plan** is written below rather than deferred. See §7.1.
6. **Wire the cross-major parity test into the migration runbook as a named
   precondition.** It is opt-in (`AGRI_CROSS_MAJOR_DATABASE_URL`, marker
   `agri_db_cross_major`) and correctly announced as skipped in a normal sweep,
   which is acceptable only while this is future work. At the moment it matters
   most it must not depend on someone remembering three environment variables.
   Add the §10 restore probe to the same runbook step for the same reason.
7. **A restore-time check that the 722 pg18 NOT NULL constraint rows carry
   acceptable auto-generated names**, since 6 of them are truncated. Cosmetic
   today; worth a look before it becomes load-bearing in a later diff.

## 7.1 Rollback plan

There is no downgrade path: `pg_dump` 16 refuses to dump an 18 server, `pg_dump`
18's output may use syntax pg16 cannot parse, and the on-disk format is not
backward compatible. There is therefore no "undo" once traffic has written to
pg18. The only real rollback is to keep the pg16 instance alive and unmutated,
and to treat the pg18 side as provisional until it is verified.

**Dual-running window.** From cutover, keep the pg16 warehouse running and
frozen. The enforcing control is **revoking write grants** from the loader and
writer roles; `ALTER DATABASE ... SET default_transaction_read_only = on` is
advisory only — any session can `SET` it back, and it does not bind superusers —
so it is a guard rail, not the lock. It must not be a warm standby that drifts;
it must be a frozen point-in-time copy. Verify that it still matches what seeded
pg18 by re-running the §10 fingerprint against it and diffing against the
baseline captured at cutover; that check is the definition of "unmutated" here.

**Rolling back** during the window means pointing the service back at pg16 and
discarding everything written to pg18 since cutover. Ingestion is replayable
from `source_release` provenance, so "re-ingest" is a bounded operation rather
than data loss — but only for data that arrived through the governed ingestion
path. Anything written by hand, or by a path that does not record a
`source_release`, is lost on rollback and must be enumerated before the window
opens. The window must close once the outstanding re-ingest exceeds what the
team is willing to replay; state that budget as a concrete number of
`source_release` rows at cutover time rather than leaving it to judgement.

**Criteria that end the window** (all must hold; until then, do not decommission
pg16):

1. The §10 fingerprint re-run against the live pg18 instance matches the pg16
   source on every probe — not the rehearsal copy, the live instance.
2. The full behavioural contract suite passes against the live pg18 instance,
   as it did against the rehearsal container in §3.
3. `0017` has landed and a dump taken *from the live pg18 instance* has been
   restored into a scratch pg18 database and fingerprinted clean. This is the
   step that proves pg18 is independently backup-recoverable, which is what
   makes pg16 safe to retire.
4. At least one full ingestion cycle has completed on pg18 with receipts
   finalizing normally.
5. The `AS MATERIALIZED` paths have been re-measured on the live instance
   against the **pg16 instance running the same query on the same data**, and
   pg18 is no worse. §11 deliberately provides no capacity envelope to compare
   against — it is a single-cell, differently-configured pair of containers — so
   the criterion is a same-data A/B against the outgoing server, not a threshold.

Criterion 3 is the one that actually retires the rollback, and it cannot be met
before item 1 of the blocker list. Keeping pg16 alive is cheap; retiring it
early is unrecoverable.

## 8. Cleanup

The rehearsal container `plantgeo-pg18-rehearsal` and the
`docker.io/timescale/timescaledb-ha:pg18` image are left in place so a reviewer
can re-run §3 and §4 without a 1.85 GB re-pull. Remove with
`podman rm -f plantgeo-pg18-rehearsal` (and optionally
`podman rmi docker.io/timescale/timescaledb-ha:pg18`). The pg16 warehouse on
5442 was never modified: `plantgeo` remains read-only at 0007 and
`plantgeo_boise_completion_20260725` was not opened.

> Both the container and the image were in fact removed before the §10 work
> began, and the image was re-pulled. Kept here as written because the §10 run
> re-created both; the removal commands above still apply.

## 9. Data restore rehearsal — corrigendum on scope

Sections 10 and 11 were added on 2026-08-03 and discharge §6.1 and §6.3.
Everything below again ran entirely against local throwaway containers. **No
Railway resource was touched.** `plantgeo_boise_completion_20260725` was opened
read-only to take a baseline fingerprint and was never written to; every dump
was taken from `plantgeo_boise_restore_src`, a `CREATE DATABASE ... TEMPLATE`
copy of it.

## 10. Data restore rehearsal: populated pg16 → pg18

Source: a copy of the Boise evidence database at `20260802_0016`, 928 MB,
923,812,330-byte custom-format dump. Target: `timescale/timescaledb-ha:pg18`
(server 18.4) on `127.0.0.1:5445`, with the 13 warehouse roles pre-created and
`infra/local-warehouse/enable-extensions.sql` run as the manual gate. The gate
ran clean and reported `pgcrypto 1.4`, `postgis 3.6.4`, `timescaledb 2.29.0`,
`vector 0.8.5` — matching §1 exactly.

A stock `pg_restore` **fails twice**. Both failures are recorded here because
neither is visible to a DDL-only rehearsal, and they have very different
consequences.

### 10.1 Blocker A — TimescaleDB's own catalogue (cross-major)

```
pg_restore: error: could not execute query: ERROR:  column "schema_name" of relation "chunk" does not exist
Command was: COPY _timescaledb_catalog.chunk (id, hypertable_id, schema_name, table_name,
             compressed_chunk_id, status, osm_chunk, creation_time) FROM stdin;
```

TimescaleDB marks its internal catalogue as dumpable user data, and the
catalogue's shape changed between the two extension versions:

| | columns of `_timescaledb_catalog.chunk` |
| --- | --- |
| 2.27.0 (pg16) | `id, hypertable_id, schema_name, table_name, compressed_chunk_id, status, osm_chunk, creation_time` |
| 2.29.0 (pg18) | `id, relid, hypertable_id, status, osm_chunk, creation_time` |

**The table is empty on both sides.** Measured on the source: 0 hypertables,
0 chunks, 0 continuous aggregates. The restore is blocked by the *column list*
of a `COPY` that would have transferred zero rows. This is the §1 observation —
"TimescaleDB is installed but nothing in the schema depends on it" — turning
into a concrete migration blocker rather than a benign note.

Worked around for this rehearsal by excluding the four `_timescaledb_*` schemas
at dump time (`--exclude-schema=_timescaledb_catalog`, `_timescaledb_internal`,
`_timescaledb_config`, `_timescaledb_cache`). Because the `agri` schema owns
nothing in those schemas, the exclusion removes no warehouse data — but this is
a workaround, not a decision. See blocker-list item 2.

### 10.2 Blocker B — `search_path` in the checksum functions (**not** cross-major)

With the TimescaleDB catalogue excluded, the restore gets as far as the data
load and fails again:

```
pg_restore: error: COPY failed for table "forecast_iteration_value":
  ERROR:  function digest(text, unknown) does not exist
CONTEXT:  SQL function "forecast_iteration_value_checksum" statement 1
```

`value_checksum` is a stored generated column, so `pg_dump` omits its values and
the restoring server **recomputes** them during `COPY`. That calls
`agri.forecast_iteration_value_checksum`, which calls `digest()` unqualified.
`pgcrypto` is installed into `public`, and `pg_restore` deliberately runs with
`search_path = ''`. The call cannot resolve.

`forecast_iteration_value_checksum` pins `TimeZone`, `DateStyle`,
`IntervalStyle` and `extra_float_digits` in `proconfig` — deliberately, for
checksum determinism — but not `search_path`.

**This is not a PostgreSQL 18 problem.** The identical dump was restored into a
freshly created database on the *pg16* server and failed at the same table with
the same error. It also reproduces without any restore at all, in one statement
on pg16, under the setting `pg_restore` uses:

```
SET search_path = ''; SELECT agri.forecast_iteration_value_checksum(...);
ERROR:  function digest(text, unknown) does not exist
```

The consequence:

> The agri warehouse cannot be restored from its own `pg_dump` output by an
> unattended stock `pg_restore`, on any PostgreSQL major. Recovery is possible —
> §10.3 does it twice — but only via a manual `ALTER FUNCTION` applied between
> the `pre-data` and `data` sections, a step that appears in no runbook and
> which nobody knew was required until this rehearsal.

That wording is deliberate. An earlier draft said the backups were "not known to
be recoverable", which this report's own §10.3 disproves 20 lines later: the
dump restores byte-identically once the step is applied. The defect is an
undocumented manual step in the recovery path, not unrecoverable data. It is
still blocker #1 — a recovery procedure nobody has written down is one nobody
can execute under pressure — but it is not a backup-integrity incident.

Scope, re-derived from the catalogue:

- **1** function blocks a restore, not two. `forecast_iteration_value_checksum`
  calls `digest(...)` unqualified. The other generated-column checksum function,
  `forecast_hindcast_value_checksum`, calls `public.digest(...)` **schema-
  qualified** and resolves fine under an empty `search_path` — verified
  directly. The rehearsal altered it too, defensively; that was a no-op for
  restorability.
- **21** functions in `agri` call a `public`-schema extension function
  (`digest`, `crypt`, `gen_salt`, `hmac`, `ST_*`) unqualified with no
  `search_path` in `proconfig`; exactly **1**
  (`enforce_intervention_lineage_release_membership`, `SECURITY DEFINER`)
  already sets one. An earlier draft said 24: that pattern also matched `encode`
  and `gen_random_uuid`, both of which live in `pg_catalog` and are therefore
  always resolvable regardless of `search_path`. Three functions matched on
  `encode` alone and were false positives. Note also that the pattern is
  case-sensitive, so a future lowercase `st_*` call would be missed.
- The remaining 20 resolve at runtime only because ordinary sessions have a
  non-empty `search_path`. Same latent defect; fix in the same revision.

### 10.2.1 A second defect found in the same functions

While confirming the above, the review lane found an unrelated and more
dangerous problem in `forecast_hindcast_value_checksum`: it pins **only**
`TimeZone`, while rendering six `double precision` arguments through `::text`.
Session `extra_float_digits` therefore leaks into a governed checksum. Measured
on pg16, same inputs, only the GUC changed:

| `extra_float_digits` | rendering of `0.1::float8 + 0.2::float8` | resulting checksum |
| --- | --- | --- |
| 0 | `0.3` | `ba088fd92909377e…` |
| 3 | `0.30000000000000004` | `dac955c2fd55ee3a…` |

The control behaves correctly: `forecast_iteration_value_checksum`, which pins
the full rendering set, returns `f2736dc9224c6b92…` at both settings.

Nothing is broken today only because `forecast_hindcast_value` is empty. As soon
as hindcast rows are written by two sessions with different
`extra_float_digits` — and several client libraries set it — two rows with
identical inputs receive different `value_checksum` values, and the
finalization/immutability machinery treats the checksum as identity.
`strategy_label_bundle_checksum` (pins `TimeZone` and `extra_float_digits`, but
not `DateStyle` or `IntervalStyle`) should be audited in the same pass.

**`0017` must therefore carry both fixes.** A `search_path`-only revision would
leave a live receipt-integrity defect in the schema while appearing to have
hardened it.

For this rehearsal the candidate `search_path` fix was applied to the **restore
targets only** (`ALTER FUNCTION ... SET search_path = public, pg_catalog`, which
adds to `proconfig` and preserves the determinism settings). Nothing in the
repository or in any migration was changed. `0017` is proposed, not written.

### 10.3 Result: with both cleared, the restore is exact

Restored in three sections (`pre-data`, then the candidate fix, then `data`,
then `post-data`) into **both** a fresh pg18 database and a fresh pg16 database,
so the cross-major comparison is against a matched procedure rather than against
the original. All four sections completed on both. (The driver script runs under
`set -e`, so its echoed exit codes cannot report a failure — a failing section
aborts the run instead. Absence of an abort is the evidence, not the echoed
zeroes; and stderr was filtered for `error`, so warnings were not examined.)

A 113-line fingerprint was then taken from each: per-table row count and full
content digest for all 68 tables (row-to-text casting, so geometry, `vector`,
`jsonb` and `numeric` all participate), sequence state, identity columns,
partition bounds, geometry SRID/type, generated-column digests, object
inventory, routine digest, and the Alembic head. Rendering was pinned
(`timezone=UTC`, `DateStyle=ISO, MDY`, `extra_float_digits=1`).

The `SERVER|` and `EXTENSION|` probes are expected to differ across majors and
were excluded before comparison; every other probe was compared verbatim. The
pg16 fingerprints are 112 lines and the pg18 one is 113 — the extra row is
`timescaledb_toolkit`, an `EXTENSION|` line, and therefore inside the excluded
set.

| comparison | result |
| --- | --- |
| pg18-restored vs pg16-restored | **identical on every compared probe** |
| pg16-restored vs pg16-original | identical except `ROUTINEDIGEST`, which differs by exactly the `ALTER FUNCTION` above — confirmed by a per-routine diff over all 93 routines showing only the two `search_path` additions, with every `md5(prosrc)` unchanged |

Two definitions matter for anyone re-deriving these numbers: "68 tables" counts
`relkind IN ('r','p') AND NOT relispartition`, and "197 indexes" counts
`relkind = 'i'`, which excludes the 3 partitioned-index parents (`relkind='I'`).
`select count(*) from pg_indexes where schemaname='agri'` returns 200 — the same
on all three databases, just a different definition.

Load-bearing values, all three databases agreeing:

| probe | value |
| --- | --- |
| `signal_observation` | 10,234 rows, digest `564a1661473cfa5851810918ffb116bc` |
| `drought_polygon_snapshot` | 1,045 rows, digest `28d5fd05a3d283e7733d8087a0f6ef62` |
| `source_release` | 210 rows, digest `8a386a0109c4dd1dd5de83d4ec48697d` |
| `artifact` | 210 rows, digest `92d922be89a3a1ccb37122a1985109bb` |
| generated `value_checksum` × 60 | digest `742da5d8290fd935f7d6f4253051a5f2` |
| sequences | `signal_observation_id_seq` = 10234, `drought_polygon_snapshot_id_seq` = 1081 |
| identity columns | 4, all `d`, preserved |
| partition | `job_event_default` attached, bound `DEFAULT` |
| geometry | `drought_polygon_snapshot` MULTIPOLYGON/4326, `spatial_cell` POLYGON+POINT/4326 |
| inventory | 68 tables, 197 indexes, 93 routines, 12 `SECURITY DEFINER`, 82 triggers, 288 CHECK, 128 FK |
| Alembic head | `20260802_0016` |

The generated-checksum row is the strongest single result. Those 60 checksums
were **recomputed from scratch by pgcrypto 1.4 on PostgreSQL 18** during the
data load, and are byte-identical to the values pgcrypto 1.3 stored on
PostgreSQL 16. §5 predicted this from a synthetic probe; this confirms it on
real finalized rows.

### 10.4 What §10 does not prove

1. **Coverage is limited by what the Boise database actually contains.** 18 of
   68 tables hold rows; 50 are empty. Real data exercised the row-count,
   content-digest, sequence, geometry and generated-checksum paths.
2. **`pgvector` carries no data.** `knowledge_chunks` has 0 rows, so the
   `vector(1536)` column was verified structurally only — its type, dimension
   and index survived, but no vector value was round-tripped. The same applies
   to `strategy_label_episode`, consistent with the strategy plane being empty
   by design.
3. **The partition routed no rows.** `job_event` has 0 rows, so the attach was
   verified structurally; no tuple was routed to `job_event_default`.
4. **`forecast_hindcast_value` is empty**, so of the two generated-column
   checksum functions only `forecast_iteration_value_checksum` was exercised
   with data. `forecast_hindcast_value_checksum` never recomputed a value — and
   it did not need the `search_path` fix either, since it qualifies `digest`
   (§10.2). It is, however, the one carrying the determinism defect in §10.2.1.
5. **`ANALYZE` is mandatory after restore and is not part of it.**
   `pg_restore` leaves no statistics; both targets were explicitly analysed
   (1.0 s on pg16, 1.8 s on pg18) before §11. A cutover runbook that omits this
   will measure missing statistics and blame the planner.
6. **The pg18 image pre-installs `timescaledb_toolkit` 1.24.0**, which is
   present on neither the pg16 warehouse nor the reviewed extension gate. It
   arrives from the image's template database, not from our DDL. Nothing uses
   it; recorded because it is an unrequested difference in the target.

## 11. Planner comparison on the `AS MATERIALIZED` paths

Measured on the two restored, freshly-analysed databases — same data, same
queries, same four-year window (2022-07-23 → 2026-07-23) for the Boise
Trekker Rim cell. `drought_class_daily_series` carries 1 `AS MATERIALIZED` CTE
and `covariate_daily_features` carries 2, confirmed from `prosrc`.

**A measurement note that changes the result.** The as-of argument must be at or
after the source release's `data_available_at` (2026-07-26). At the obvious
choice of 2026-07-24 the availability gate correctly returns an all-null
meteorology block — 0 of 51,170 meteorology features non-null — and the query
finishes in ~4 ms without touching the expensive path. The drought block is
suppressed too, though less completely (1,462 of 4,386 non-null, rising to
4,372 of 4,386). That is the gate working as designed, not a defect, but it
makes the measurement worthless. At as-of 2026-07-27 the meteorology block
returns 50,883 of 51,170 non-null and the real workload is exercised. All
figures below use 2026-07-27.

**The two servers are not configured alike, and this confounds the timings.**
The pg18 image ships `timescaledb-tune`d settings; the pg16 warehouse runs
stock. Measured:

| setting | pg16 | pg18 |
| --- | --- | --- |
| `shared_buffers` | 128 MB | 7,520 MB |
| `work_mem` | 4 MB | 29.4 MB |
| `effective_cache_size` | 4 GB | 22 GB |
| `random_page_cost` | 4 | 1.1 |
| `max_parallel_workers_per_gather` | 2 | 4 |
| `effective_io_concurrency` | 1 | 256 |
| `jit` | on | off |

Every one of those drives plan choice, wall clock, or both. An earlier draft of
this section claimed the two containers had "identical settings"; that was
wrong, and it turned a confounded measurement into an apparently controlled one.
The comparison below is therefore **directional only** — it is evidence that
pg18 does not fall off a cliff, not a like-for-like benchmark.

Wall clock, 5 consecutive runs each, `auto_explain` disabled (its
`log_analyze`/`log_timing` instrumentation on nested statements inflates pg18
disproportionately and, left on, produces a spurious ~30% pg18 regression — the
two runs disagreed, and this is the one without the observer effect):

| query | pg16.14 median | pg18.4 median | direction |
| --- | --- | --- | --- |
| `drought_class_daily_series` | 555 ms | 487 ms | pg18 faster |
| `covariate_daily_features` | 5,578 ms | 5,235 ms | pg18 faster |

The review lane re-ran pg18 with the pg16 GUCs applied at session level and pg18
remained the faster of the two on both queries, so the direction survives
removing the confound — but it survives by luck of the method, not by design.

**Plan shape.** Both majors materialise the same seven CTEs — `spec`, `cell`,
`spine`, `lookback`, `observation`, `drought`, `admissible` — with the same 16
`CTE Scan` nodes per iteration. Node counts across the 3-iteration capture:

| node | pg16 | pg18 |
| --- | --- | --- |
| `CTE Scan` | 48 | 48 |
| `Nested Loop` | 42 | 42 |
| `Seq Scan` | 9 | 9 |
| `GroupAggregate` | 3 | 3 |
| `Sort` | 30 | 27 |
| `Index Scan` | 6 | 9 |
| `Bitmap Heap Scan` | 3 | 0 |
| `Materialize` | 6 | 9 |

An earlier draft reported `Sort` as 138 vs 135 and `Index Scan` as 9 on both.
Those came from a pattern that counted `Sort Key:`/`Sort Method:` detail lines
as nodes and let `Index Scan` also match `Bitmap Index Scan`. The corrected
counts are above.

The remaining difference — pg16's 3 `Bitmap Heap Scan` becoming `Index Scan` on
pg18 — is **a configuration artifact, not a major-version behaviour**. With the
pg16 GUCs applied at session level (chiefly `random_page_cost` and `work_mem`),
pg18 reverts to the pg16 scan shape. Attributing it to PostgreSQL 18 would have
been wrong.

**The hoist survives.** The specific exposure named in the old §7.4 — that a
major-version planner change would defeat the `AS MATERIALIZED` hoist and return
`drought_class_daily_series` to minutes — did not occur. This conclusion is the
one part of §11 that is robust to the configuration confound, and for a
structural reason: `AS MATERIALIZED` is a directive, not a planner choice. The
seven CTEs remain materialised under matched GUCs as well as default ones.

Two further caveats. First, these are single-cell numbers on the Boise evidence
database; `drought_class_daily_series` at ~0.55 s is in the range the original
~0.6 s figure describes, but `covariate_daily_features` at ~5.5 s is slower than
anything §7 anticipated, on **one** cell. That is a scaling question for the
covariate layer independent of the migration, and it is the number to watch when
the layer is pointed at more cells. Second, this is not a production capacity
measurement and no capacity envelope should be derived from it.
