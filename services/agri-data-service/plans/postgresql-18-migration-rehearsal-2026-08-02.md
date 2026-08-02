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
| Is a Railway migration safe on this evidence? | **Not yet — schema is cleared, data restore is not.** (§7) |

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

There is therefore **no `0017` to propose**. The schema is portable as shipped.

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

1. **No data restore was rehearsed.** This is a *migration* (DDL) rehearsal
   against an empty database. Restoring a populated pg16 dump into pg18 —
   including PostGIS geometry, `pgvector` columns, the partitioned `job_event`,
   and the sequence/identity state — was **not** attempted. The North America
   pilot's standing blocker names "extension parity **+ restore rehearsal**";
   only the first half is discharged here.
2. **No Railway resource was touched.** Railway runs 18.3, this ran 18.4, and
   Railway's networking, TLS, connection limits, and role/permission model were
   not exercised at all.
3. **No performance or planner comparison.** Identical schema does not imply
   identical plans; pg18 changed planner behaviour in ways nothing here
   measures.
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

What still blocks a deployment decision, in priority order:

1. **A data restore rehearsal.** Restore a populated pg16 dump (the Boise
   evidence database is the obvious candidate, via a copy — never the original)
   into pg18 and re-verify receipt checksums and row counts. Until that runs,
   the pilot's blocker is only half discharged. This is the single highest-value
   next step.
2. **Version alignment.** Rehearsed on 18.4, Railway is 18.3. Re-run against
   the exact target build, or accept the gap explicitly.
3. **Railway-specific surface.** Role/permission model, TLS, connection limits,
   and whether the reviewed manual extension gate can even be run there — the
   foundation revision *refuses to create extensions itself* by design, so an
   operator must run that gate first, and it is unverified that Railway permits
   it.
4. **A planner/performance comparison, run as part of the restore rehearsal.**
   Listed as a non-proof in §6.3, but it belongs here too: it needs populated
   data to measure, so it cannot be separated from step 1. The specific
   exposure is that 0016's usable performance rests on `AS MATERIALIZED` CTE
   hoists — `drought_class_daily_series` went from ~11 minutes to ~0.6 s over a
   four-year window on that basis, and `covariate_daily_features` adds two more.
   A major-version planner change targets exactly that construct, so "the schema
   is identical" is not evidence the queries are still usable.
5. **A written rollback plan, before the cutover rather than during it.** A
   pg18 dump cannot be restored into pg16, so the only real rollback is to keep
   the pg16 instance alive and unmutated until the pg18 side is verified. That
   needs stating explicitly, with the verification criteria that end the
   dual-running window.
6. **Wire the cross-major parity test into the migration runbook as a named
   precondition.** It is opt-in (`AGRI_CROSS_MAJOR_DATABASE_URL`, marker
   `agri_db_cross_major`) and correctly announced as skipped in a normal sweep,
   which is acceptable only while this is future work. At the moment it matters
   most it must not depend on someone remembering three environment variables.
7. **A restore-time check that the 722 pg18 NOT NULL constraint rows carry
   acceptable auto-generated names**, since 6 of them are truncated. Cosmetic
   today; worth a look before it becomes load-bearing in a later diff.

## 8. Cleanup

The rehearsal container `plantgeo-pg18-rehearsal` and the
`docker.io/timescale/timescaledb-ha:pg18` image are left in place so a reviewer
can re-run §3 and §4 without a 1.85 GB re-pull. Remove with
`podman rm -f plantgeo-pg18-rehearsal` (and optionally
`podman rmi docker.io/timescale/timescaledb-ha:pg18`). The pg16 warehouse on
5442 was never modified: `plantgeo` remains read-only at 0007 and
`plantgeo_boise_completion_20260725` was not opened.
