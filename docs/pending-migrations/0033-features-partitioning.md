# 0033 — `geo.features` LIST-partitioned on `layer_id`

Status: **code written, NOT applied, NOT journaled, NOT pinned.** Nothing in this document has run
against production.

`geo.features` is 7,872 MB / 5,080,640 rows across 11 layers sharing one heap, one 1,518 MB jsonb
TOAST relation and 2,563 MB of index. This converts it to a LIST-partitioned table on `layer_id`,
one partition per layer plus a mandatory DEFAULT.

The conversion is **not** a Drizzle migration and cannot become one. Every drizzle `.sql` runs
inside one transaction (`drizzle/0031:27-28`), and a 5M-row copy in one transaction holds its
snapshot and its locks for the whole run. The work lives in `scripts/partition-features.mjs`; the
migration that records it should carry nothing but a `DO $$` precondition assert, the pattern
`drizzle/0030:196-224` and `drizzle/0032:41-51` already set.

**Numbering collision, read before creating the `.sql`.** Runbook §0.10 also wants a new
`drizzle/0033_*` to record the out-of-band `DROP MATERIALIZED VIEW geo.mv_signal_cell_daily` (2026-08-18,
never recorded, so a rebuild from migration history silently resurrects a 6,349 MB / 1,729 s
relation). Two different changes both want the next index. Pick the order deliberately; this
document keeps the `0033-` prefix only because that is the name it was created under.

---

## What lands

| file | what it does | applied by |
|---|---|---|
| `src/lib/server/db/schema.ts:221,240` | `id` drops `.primaryKey()` for `.notNull()`; `primaryKey({ columns: [table.id, table.layerId] })` joins the trailing config array | deploy, **same commit as the DDL** |
| `scripts/partition-features.mjs` | create → copy → index → trigger → verify → swap → analyze, each phase separately invocable and idempotent | by hand, out of band |
| `drizzle/00NN_features_partitioned.sql` | **not yet written.** A `DO $$` assert that `geo.features` is `relkind = 'p'` and that `features_layer_external_id_unique` exists and is valid | `psql -f`, out of band |

The schema change is not optional and not deferrable. A partitioned table requires the partition
key in every unique index, so the live PK becomes `(id, layer_id)`; if `schema.ts` still declares a
single-column PK the next `db:generate` emits DDL that **reverts the production primary key**.

---

## Preconditions

1. **Disk.** The copy roughly doubles the table before the legacy heap is dropped: ~5.3 GB of new
   heap + TOAST, ~1.7 GB of new index, plus WAL for every copied row. Have ≥ 15 GB free. The
   database is ~37 GB after the 2026-08-18 matview drop.
2. **`btree_gist`** installed — `ix_features_layer_geom` leads with a uuid and GiST has no native
   uuid operator class. `--phase=create` asserts this.
3. **`geo.feature_observation_day(jsonb)`** present — `ix_features_layer_observation_day` is an
   expression index over it. `--phase=create` asserts this.
4. **Column contract.** `--phase=create` compares `geo.features` against `FEATURE_COLUMNS` in the
   script — name, type, NOT NULL, default, and physical order — and refuses to build anything on a
   mismatch. Reconcile the constant, do not bypass the check: every later phase names these columns
   explicitly and the new table is built from the constant, so a drifted default would be silently
   changed.
5. **Writers quiesced** for the copy — three Railway crons plus `plantgeo-main`. Ingest commits per
   100-row batch (`writer.py:38,300-306`) and per 200-row repair page (`backfill.py:69`), so a
   stopped cron leaves no partial batch. A copy against live writers is supported but needs
   `--phase=copy --catchup` afterwards, and catch-up sees INSERTs, not in-place UPDATEs.
6. **The DSN.** `PARTITION_DATABASE_URL`, falling back to `MIGRATION_DATABASE_URL` / `DATABASE_URL`
   — same precedence as `scripts/apply-pre-aggregation.mjs`.

---

## Ordered apply sequence

Every phase is separately invocable, idempotent, and safe to re-run. Add `--dry-run` to any of them
to print the exact statements and touch nothing.

```
0.  node scripts/partition-features.mjs --phase=plan
1.  <quiesce the three Railway crons and plantgeo-main>
2.  node scripts/partition-features.mjs --phase=create
3.  node scripts/partition-features.mjs --phase=copy
4.  node scripts/partition-features.mjs --phase=index
5.  node scripts/partition-features.mjs --phase=copy --catchup    # only if step 3 ran hot
6.  node scripts/partition-features.mjs --phase=trigger
7.  node scripts/partition-features.mjs --phase=verify
8.  node scripts/partition-features.mjs --phase=swap
9.  node scripts/partition-features.mjs --phase=analyze
10. <re-create every dependent relation — see below>
11. <restart Martin; bounce plantgeo-main>
12. <un-quiesce ingestion; watch the first cron exit code>
```

**0 — plan.** Read-only. Prints the live per-layer census beside the 2026-08-20 numbers, the chunk
count each layer will use, what already exists, and the dependent-relation list.

**2 — create.** Preconditions, `geo.features_new PARTITION BY LIST (layer_id)`, one partition per
`geo.layers` row, `geo.features_default`, and `geo.features_partition_copy_log`.

The DEFAULT partition is **mandatory**, not a safety net. `layersRouter.create` is a
`contributorProcedure` — team-editor, not admin — and mints a `geo.layers` row at request time
(`src/lib/server/trpc/routers/layers.ts:140-149`); the new id can receive features immediately
(`contributions.ts:7-24` takes `layerId` straight from the client). Without a DEFAULT the first
such write fails with `no partition of relation "features" found for row`: a 500 on
`contributions.submitObservation`, and in ingestion a per-job hard failure that flips the cron exit
code (`ingest/results.py:86-94`). Loud, not silent — but every run.

**3 — copy.** Per layer, **smallest first**, so the ten cheap layers validate the whole path before
`fire-detections` (3,019,709) and `water-gauges` (1,413,932). Layers at or under 250,000 rows copy
in one statement; the two big ones chunk on `created_at`, with boundaries walked off
`idx_features_layer_created_at` using `ORDER BY created_at OFFSET n LIMIT 1` — an index-only seek,
no sort, flat memory. `percentile_disc` or `ntile` would put the whole layer in a tuplestore, which
is what a 2 GB box cannot afford. Rows with a NULL `created_at` get their own final chunk.

Each chunk is one transaction that writes its rows **and** its `geo.features_partition_copy_log`
row together, so a crash rolls back both and a resume skips exactly what is recorded. Re-running
with a different `CHUNK_TARGET_ROWS` is refused rather than silently double-copied.

The copy runs with no indexes and **no trigger** on the target — see step 6.

**4 — index.** The primary key, eight parent indexes, the per-partition geom indexes, both outbound
foreign keys. All plain `CREATE INDEX`: **PostgreSQL does not support `CREATE INDEX CONCURRENTLY`
on a partitioned parent** (verified 2026-08-20 — `ERROR: cannot create index on partitioned table
... concurrently`; the claim to the contrary in `drizzle/0030`'s comments is wrong). It does not
matter here, because nothing reads `geo.features_new` yet and the ACCESS EXCLUSIVE lock a plain
build takes is invisible to production.

Indexes are built under a `_swap` suffix. Index names are unique per **schema**, so the incoming
indexes cannot hold their final names until the outgoing ones let go; constraint names are unique
per **table** and do not collide, which is why both FKs and the trigger get their final names from
the start.

The FKs are added validated, not `NOT VALID`. `ADD CONSTRAINT` takes ShareRowExclusive on the
*referenced* table, so `geo.layers` and `geo.geometry` are briefly closed to writers — the script
uses a 5 s `lock_timeout` and retries rather than queueing.

**5 — catch-up.** Only needed if step 3 ran against live writers. Anti-joins on `(id, layer_id)`,
so it requires the PK from step 4. It collects rows INSERTED during the copy window; it cannot see
an in-place UPDATE of an already-copied row, because the count does not move. That is the honest
reason to quiesce first.

**6 — trigger.** `geo_features_sync_geom` BEFORE INSERT OR UPDATE OF `properties` FOR EACH ROW →
`geo.sync_feature_geom_from_properties()`. Its own phase, and deliberately **after every copy**:
the function recomputes `geom` from `properties->'geometry'` and, since `drizzle/0004`, runs the
result through `ST_MakeValid`, so copying with it attached would cost a geometry parse per row and
silently rewrite any geometry stored before that repair landed. The copy moves bytes; it does not
recompute them. BEFORE-row triggers on a partitioned parent are legal and are cloned onto every
partition, including partitions attached later.

**7 — verify.** Per-layer counts, DEFAULT partition population, every expected index present and
`indisvalid`, every partition index valid, both FKs `convalidated`, the trigger present, a DEFAULT
partition present. Exits non-zero on any failure.

**8 — swap.** Re-runs verify as a hard gate and **refuses to swap if it fails**. Then one
transaction: rename `geo.features` → `geo.features_legacy`, rename its indexes to `legacy_*`,
rename `geo.features_new` → `geo.features`, strip the `_swap` suffixes. Both table renames are in
the same transaction — there must never be an instant in which no relation is named `geo.features`.

`lock_timeout` is 5 s with 20 retries, 15 s apart. **A queued `ACCESS EXCLUSIVE` does not merely
wait — every reader that arrives behind it waits too**, so a rename that queues is a full outage
for as long as it queues. Only the first statement can wait; once it holds the lock the rest are
catalog updates.

**9 — analyze.** Every partition, then the parent. Not a tidy-up: autovacuum **never** analyzes a
partitioned parent, and an expression index has no statistics at all until the table is analyzed —
without them the planner reverts to the sequential scan the index exists to replace.

---

## Index decisions — state these when reviewing, they are deliberate

Production carries eleven indexes on `geo.features`. Nine are re-created by exact name. Two are
not, and both omissions are intentional:

**`idx_features_layer` (layer_id), 61 MB — DROPPED OUTRIGHT.** `layer_id` is constant within a
partition, so the index answers nothing the partition constraint does not already answer. It is
recreated nowhere and should not be missed.

**`idx_features_geom` USING GIST (geom), 313 MB — no longer a parent index.** It is created
**per-partition** and **not at all on `features_fire_detections` or `features_water_gauges`**, which
own 87.6% of it between them and are served by no tile function (runbook §0.3). This has a
structural consequence worth stating plainly: a parent index requires a matching child index on
**every** partition, so an index two partitions deliberately lack cannot exist at the parent at
all. **After the swap there is no relation named `geo.idx_features_geom`.** Nothing probes that name
— `src/app/api/ready/route.ts` names only `features_layer_external_id_unique` — and the two layers
keep spatial access through `ix_features_layer_geom`, which *is* a parent index and therefore
exists on every partition. What is actually given up is unindexed spatial access to the
*unpublished* rows of those two layers, which nothing queries. Per-partition names follow the
convention `scripts/apply-pre-aggregation.mjs` uses: `features_<slug>_geom_idx`.

**`features_layer_external_id_unique` is the one name the deploy depends on.**
`src/app/api/ready/route.ts:60` resolves it literally with `to_regclass`; a missing one 503s the
readiness probe, fails the Railway healthcheck and blocks every deploy. A partial unique index on a
partitioned parent is legal (verified 2026-08-20 on PG 18.4), and global uniqueness still holds
because one `layer_id` implies exactly one partition.

**Not done, deliberately.** Runbook §0.3 proposes rebuilding `idx_features_layer_status`,
`idx_features_layer_created_at` and `idx_features_layer_updated_at` **without** their now-redundant
leading `layer_id` column (~213 MB across the three). They are re-created unchanged here. Narrowing
them changes plan shapes on read paths that are being rewritten in the same batch, and it is a
cheap follow-up once post-swap `EXPLAIN` output exists. Do it as its own change, with measurements.

**Any index added AFTER the swap** must use the verified three-step dance, because CONCURRENTLY is
unavailable on the parent and a plain build would lock a live table:

```sql
CREATE INDEX <name> ON ONLY geo.features <definition>;      -- metadata only, indisvalid = false
CREATE INDEX CONCURRENTLY <child> ON geo.features_<slug> <definition>;   -- once per partition
ALTER INDEX <name> ATTACH PARTITION <child>;                             -- once per partition
```

The parent index flips valid only once **every** partition index is attached and valid.

---

## Dependent relations — the swap's quietest hazard

A view's or matview's stored rewrite rule references the table by **OID, not name**. `ALTER TABLE
... RENAME` does not rewrite it. So after the swap every relation below still reads the orphaned
`geo.features_legacy` heap — successfully, silently, and forever.

At least seven exist today: `geo.mv_feature_observation_day` (`drizzle/0029:166`),
`geo.mv_layer_feature_stats` (`:728`), `geo.mv_layer_hourly_activity` (`:763`),
`geo.mv_soil_survey_grid` (`:816`), `geo.mv_soil_survey_union` (`:919`),
`geo.mv_feature_observation_day_axis` (`drizzle/0031:142`) and `geo.watershed_rollup`
(`drizzle/0023:36,176`).

`--phase=plan` and `--phase=swap` both enumerate the live list from `pg_depend`/`pg_rewrite` — trust
that over this paragraph. Each one must be dropped and re-created from its migration after the
rename, then repopulated. This is **not** a gate the script can refuse on: there is no way to swap
without it, so it is a mandatory post-swap step instead.

Plpgsql tile functions are different — they resolve `geo.features` by name at execution and follow
the new table. What does not follow is a **cached plan** inside a long-lived Martin session, which
is why Martin is restarted at step 11 rather than reasoned about.

---

## Verification queries

Per-layer counts, old against new (the proof the swap is allowed):

```sql
SELECT l.name,
       count(*) FILTER (WHERE src.layer_id IS NOT NULL) AS legacy_rows,
       (SELECT count(*) FROM geo.features f WHERE f.layer_id = l.id) AS partitioned_rows
  FROM geo.layers l
  LEFT JOIN geo.features_legacy src ON src.layer_id = l.id
 GROUP BY l.id, l.name
 ORDER BY 2 DESC;
```

Partitions and their bounds — the DEFAULT must be there:

```sql
SELECT c.relname, pg_get_expr(c.relpartbound, c.oid) AS bound,
       pg_size_pretty(pg_total_relation_size(c.oid)) AS size
  FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid
 WHERE i.inhparent = 'geo.features'::regclass
 ORDER BY pg_total_relation_size(c.oid) DESC;
```

Every index on the parent, and every index on every partition, valid:

```sql
SELECT c.relname AS index_name, i.indisvalid, i.indisready
  FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid
 WHERE i.indrelid = 'geo.features'::regclass
    OR i.indrelid IN (SELECT inhrelid FROM pg_inherits WHERE inhparent = 'geo.features'::regclass)
 ORDER BY i.indisvalid, c.relname;
```

The readiness probe's exact name, and the primary key's shape:

```sql
SELECT to_regclass('geo.features_layer_external_id_unique') IS NOT NULL AS readiness_index_present;
SELECT conname, pg_get_constraintdef(oid)
  FROM pg_constraint WHERE conrelid = 'geo.features'::regclass ORDER BY contype;
```

The trigger, cloned to every partition (expect `1 + partition count`):

```sql
SELECT count(*) FROM pg_trigger
 WHERE tgname = 'geo_features_sync_geom' AND NOT tgisinternal;
```

**Pruning actually happening** — the whole point of the exercise. Run this against a rewritten
reader and confirm the plan touches one partition, not eleven:

```sql
EXPLAIN (ANALYZE, BUFFERS)
SELECT count(*) FROM geo.features WHERE layer_id = '<a real layer id>';
```

Execution-time pruning is **not** a substitute for plan-time pruning and must not be used to
deprioritise a `l.name`-join site: a plpgsql tile function's plan is cached across a long-lived
Martin session.

Smoke test, cheapest available: `REFRESH MATERIALIZED VIEW geo.mv_layer_feature_stats` (after it has
been re-created against the new OID) and diff its 11 counts against the pre-swap census.

---

## Rollback

The legacy heap is retained under `geo.features_legacy`. **Do not drop it** until steps 9-12 are
green and have stayed green through at least one full ingest cycle. It is ~7.9 GB.

```
node scripts/partition-features.mjs --phase=rollback --force
```

Reads the catalog and emits the exact inverse of the swap: `geo.features` → `geo.features_new` with
the `_swap` suffixes restored, `geo.features_legacy` → `geo.features` with the `legacy_` prefixes
stripped. It refuses without `--force`, and prints what it is about to discard first.

**Rollback discards every row written since the swap.** The legacy heap has had no writes since the
rename, so anything ingestion or the app wrote afterwards lives only in the partitioned table and is
thrown away. The script prints the row-count delta before it will proceed. Restart Martin and bounce
`plantgeo-main` again afterwards — the OID moved back — and re-create any dependent matview a second
time.

Before the swap there is nothing to roll back: `geo.features` has not been touched, and abandoning
the attempt is `DROP TABLE geo.features_new CASCADE` plus `DROP TABLE geo.features_partition_copy_log`.

---

## If the rename times out

It will queue behind ingestion if ingestion is running — `pg_stat_activity` showed a live ingest
backend mid-batch on 2026-08-20, and that is what made a probe `CREATE INDEX CONCURRENTLY` time out
at 30 s.

The script never queues: `lock_timeout = 5s`, 20 attempts, 15 s apart, and it prints the non-idle
backends it can see before it starts. If all 20 attempts fail:

1. **Confirm the writers are actually stopped.** Three Railway crons plus `plantgeo-main`. A
   redeployed service restarts its pool.
2. **Find the holder**, do not raise the timeout:
   ```sql
   SELECT pid, state, wait_event_type, xact_start, left(query, 120)
     FROM pg_stat_activity
    WHERE pid IN (SELECT pid FROM pg_locks WHERE relation = 'geo.features'::regclass)
    ORDER BY xact_start;
   ```
3. **Never raise `lock_timeout` to "just get it through".** A long ACCESS EXCLUSIVE *wait* blocks
   every reader that arrives behind it, so a 10-minute wait is a 10-minute outage that ends in a
   rename you could have had in 50 ms at a quieter moment.
4. An idle-in-transaction backend is the usual culprit; terminate that one session rather than
   widening the window.

Failure is safe. The swap is one transaction: it either renames everything or nothing.

---

## Follow-ups this does not do

- **`--phase=adopt --layer=<name>`** exists and is the recurring op that keeps `geo.features_default`
  from becoming the table: it creates the partition, drains the layer's rows out of DEFAULT, and
  attaches — one transaction, under the same `lock_timeout` and retry as the swap. It is **not**
  scheduled. Runbook §0.4 wants a periodic drain; prior art for one is
  `services/agri-data-service/src/agri_data_service/db/maintenance.py` (`agri.job_event`, RANGE/day,
  with an existing `job_event_default`). Note that every ATTACH scans the DEFAULT partition to prove
  nothing left in it belongs to the new partition — which is exactly why DEFAULT must be kept small.
- **Synchronous partition creation inside `layersRouter.create`'s transaction** is the alternative
  to draining. It needs the app role to hold `CREATE` on schema `geo`, which is still unverified
  (runbook §0.7). The DEFAULT partition is not optional either way.
- **Remodelling `water-gauges` during the copy** (1,413,932 rows over 953 geometries, runbook §0.1)
  is not attempted. The copy is byte-for-byte on purpose; a remodel is a separate, reviewable change.
- **Narrowing the three `layer_id`-leading composite indexes** — see "Index decisions".
- **`enable_partitionwise_aggregate`** must be confirmed after the cutover: the census matviews are
  all-layer by design and are the ones that care.
