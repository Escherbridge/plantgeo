# Checksum / guard / finalize layer — keep-or-cut audit

**Date:** 2026-08-03
**Scope:** `services/agri-data-service/db/agri/`, its Alembic revisions, and every Python caller.
**Method:** read-only. Counts are from the declarative tree at `db/agri/` (the generated source of
truth, `db/AGENTS.md:1-6`) and from the shipped Python under `src/`. Nothing was modified.

**Question:** does the forecast/receipt checksum + guard + finalize layer earn its keep for a
single-researcher tool, and if not, what specifically should go?

**Answer in one line:** the *checksum columns* earn their keep and should stay; the *database-level
enforcement built on top of them* does not, and it is where all the measured cost sits. The
recommendation is Option 3 below — keep checksums as recorded data, delete the courthouse.

---

## 1. Measured inventory

### 1.1 The layer, by mechanism

`db/agri/functions/` holds 91 files / 5,722 lines. Sixty of those files are this layer:

| Mechanism | Files | SQL lines | What it is |
| --- | ---: | ---: | --- |
| `*_checksum` functions | 11 | 615 | compute a digest |
| `guard_*` functions | 17 | 672 | block writes via trigger |
| `finalize_*` functions | 4 | 886 | gated staging→finalized transition |
| `enforce_*` functions | 9 | 408 | insert/transition contracts |
| `verify_*` functions | 6 | 240 | post-transition checks (thin wrappers) |
| `require_* / record_* / protect_* / reject_* / prevent_*` | 13 | 464 | initial-state, audit-trail, misc blocks |
| **Layer subtotal** | **60** | **3,285** | **66% of files, 57% of function lines** |
| Everything else in `functions/` | 31 | 2,437 | actual forecasting maths, contracts, views |

### 1.2 Storage and wiring

| Item | Count | Evidence |
| --- | ---: | --- |
| Tables in schema | 69 | `db/agri/tables/` |
| Tables carrying ≥1 checksum column | 34 | `db/agri/tables/*.sql` |
| Checksum **columns** | 63 | `character varying(64)` column defs |
| — of which `GENERATED ALWAYS … STORED` | **2** | `forecast_hindcast_value.sql:25`, `forecast_iteration_value.sql:17` |
| CHECK constraints named for a checksum | 29 | e.g. `forecast_iteration.sql:40` |
| Lines carrying the `^[0-9a-f]{64}$` regex | 42 | `db/agri/tables/*.sql` |
| Status↔checksum "evidence" CHECKs | 10 | e.g. `forecast_receipt.sql:24`, `forecast_iteration.sql:42` |
| **Triggers in the whole `agri` schema** | **82** | 79 `CREATE TRIGGER` + 3 `CREATE CONSTRAINT TRIGGER` |
| — of which execute a `guard_/enforce_/verify_/require_/record_/protect_/reject_/prevent_` function | **82** | **every trigger in the schema is a governance trigger; there is not one ordinary trigger** |
| — on `strategy_*` / `intervention_*` tables | 19 | `db/agri/triggers/strategy_*.sql`, `intervention_*.sql` |
| Tables carrying ≥1 governance trigger | 35 | `db/agri/triggers/` |
| `SECURITY DEFINER` functions | 12 | `db/agri/functions/*.sql` |
| Dedicated owner roles created for them | 4 | `…0012:30`, `…0015:60`, `…0015:147`, `…0015:232` |

Views are downstream of it: all four views in `db/agri/views/` select checksum columns, and two
select the generated `value_checksum` specifically (`v_forecast_hindcast_outcome.sql:47`,
`v_forecast_iteration_outcome.sql:55`).

### 1.3 Migrations

17 revisions, 9,908 lines. Classified by whether the revision's substance is this layer:

| Class | Count | Revisions |
| --- | ---: | --- |
| **Primarily** this layer | **9** | 0005, 0006, 0008, 0010, 0012, 0013, 0014, 0015, 0017 |
| Partially | 4 | 0001, 0002, 0003, 0009 |
| Unrelated | 4 | 0004, 0007, 0011, 0016 |

The two largest revisions in the repo are both primarily this layer: `…0010_forecast_iteration_pipeline.py`
(2,365 lines, 174 token-matching) and `…0005_sql_forecasting_framework.py` (2,122 lines, 163).
Revisions 0012 + 0015 together are **442 lines** whose entire subject is `SECURITY DEFINER` lockdown
so the guards stay safe under a *constrained loader role*.

### 1.4 Test weight

| Item | Count |
| --- | ---: |
| Test files total | 53 (14,760 lines) |
| Test files mentioning `checksum\|finalize\|guard\|immutable\|tamper` | 40 |
| Test functions *named* for one of those tokens | 46 |
| Test files whose stated purpose *is* this layer | **8 (3,082 lines, 42 test functions)** |

The eight: `test_intervention_parent_guard_migration_contract.py` (87), `…_postgresql.py` (191),
`test_security_definer_lockdown_migration_contract.py` (138), `…_postgresql.py` (161),
`test_gate_hardening_migration_contract.py` (147), `test_strategy_selection_finalization_postgresql.py`
(1,251), `test_forecast_quality_gate_postgresql.py` (645), `test_strategy_selection_gates_postgresql.py` (462).

There is **no** dedicated determinism target: `Makefile:9-10` is a blanket `uv run pytest`,
`pyproject.toml:53-55` defines no markers, and there is no `.github/` directory at all.

### 1.5 Churn

Of the 16 commits touching `services/agri-data-service` since 2026-07-01, **9 touched
`db/agri/functions/` or `alembic/versions/`**. The two commits whose subject lines name this layer
are `fc590d8 feat: governance hardening waves 1+2…` and `b7c0023 fix(agri): pin routine search_path
and finish the checksum float pin`.

---

## 2. What each mechanism actually enforces

Removal consequence, one line each, classified **(a) reproducibility**, **(b) tamper-evidence**,
**(c) bookkeeping**.

| Mechanism | If removed, this becomes possible | Class |
| --- | --- | --- |
| **Input-identity checksum columns** (`input_release_checksum`, `model_checksum`, `parameter_checksum`, `contract_checksum`, `history_checksum`, `feature_checksum`, `payload_checksum`, `geometry_checksum`, `observation_checksum` — 61 plain columns) | You hold a forecast and cannot say which release, contract, parameters, or history produced it; you cannot re-derive it or prove two runs used the same inputs. | **(a)** |
| **Identity UNIQUE constraints built on them** (`uq_forecast_hindcast_run_identity` `forecast_hindcast_run.sql:62`; `uq_release_set_manifest_checksum` `release_set.sql:33`; `uq_source_release_identity` `source_release.sql:39`) | The same inputs silently produce two rows instead of colliding; re-runs duplicate rather than dedupe. | **(a)** |
| **`materialize_forecast_iteration` idempotency block** (`procedures/materialize_forecast_iteration.sql:186-212`) | Re-running `forecast-run-iteration` against an existing `iteration_key` with *different* inputs overwrites or duplicates instead of raising. This is the one live re-run protection in the system. | **(a)** |
| **Receipt digests** (`forecast_iteration_receipt_checksum` 55 ln, `forecast_hindcast_receipt_checksum` 159 ln) | You lose a single value that pins a whole finalized aggregate — the one artifact worth quoting in a paper as "this result, exactly". | **(a)**, weakly (b) |
| **The 2 `GENERATED … STORED` `value_checksum` columns** | Nothing. Postgres recomputes a generated column from the same row on every write, so it cannot disagree with its inputs and provides **zero** tamper-evidence. It exists only as an intermediate for `string_agg(value.value_checksum …)` inside the receipt digest (`forecast_iteration_receipt_checksum.sql:43`, `forecast_hindcast_receipt_checksum.sql:43`), which can call the function inline instead. | **(c)** |
| **`guard_*` "finalized rows are immutable"** (e.g. `guard_forecast_receipt_change.sql:11-13`, `guard_forecast_iteration_change.sql:18-20`) | A script could `UPDATE` a finalized row in place. This is the genuine accident-prevention value in the whole layer. | **(b)** as designed, **accident-prevention** in practice |
| **`guard_*` DELETE prohibitions on *non-final* rows** (`guard_forecast_value_write.sql:16-19`, `guard_forecast_hindcast_value_write.sql:12-14`, `guard_forecast_iteration_change.sql:15-17`, `guard_forecast_backtest_change.sql:12-14`, `guard_forecast_receipt_change.sql:11`) | You could delete a half-written staging run. Today you cannot — see §4.2. | **cost only** |
| **`finalize_*`** (4 fns, 886 ln) | A staging row could move to `finalized` without its gates re-checked. But nothing in `src/` ever moves such a row — see §3.2. | **(c)** today |
| **`enforce_*`, `require_*`, `verify_*`** (24 fns, 1,112 ln) | Rows could be inserted in a terminal state or transition without re-validation. Duplicates checks the CLI already makes. | **(c)** mostly |
| **`record_forecast_*` audit trail** (7 fns, 200 ln) | `forecast_input_recorded_at` stops tracking when an input row last changed (`record_forecast_input_change.sql:12-20`). | **(c)** |
| **`SECURITY DEFINER` lockdown + 4 owner roles** (12 fns, 442 migration ln, 577 test ln) | A *constrained loader role* could no longer pass the parent guards. Meaningless with one role. | **(c)** at this scale |

---

## 3. Who is the adversary?

### 3.1 The tamper-evidence property is not achievable here

Tamper-evidence needs the digest to be beyond the reach of the party who might tamper. It is not:

- The receipt digest lives in the **same row** it protects (`forecast_iteration.receipt_checksum`,
  `forecast_iteration.sql:34`), in the same database, defended only by triggers.
- Triggers are removable by the table owner, and the repo does it twice.
  `…0014_hindcast_knowledge_pin_and_gate_hardening.py:169` runs
  `ALTER TABLE agri.forecast_hindcast_run DISABLE TRIGGER forecast_hindcast_run_change_guard;`
  to backfill a column, with the comment (`:165-166`) that the guard "refuses every write to a
  finalized run". `tests/test_forecasting_v1_upgrade_postgresql.py:209` uses
  `SET LOCAL session_replication_role = replica` for the same reason.
- The four-role, 145-privilege scheme that would have separated "the person who writes forecasts"
  from "the person who can drop triggers" was **rejected and never provisioned**.

So the actor who could tamper and the actor who administers the database are the same person, with
the same credentials, and the codebase already contains two copy-pasteable bypasses. Against that
actor the guards are a speed bump, not a seal. **There is no adversary.**

Real tamper-evidence would require the digest to leave the database — committed to git, or written
to the local run store. The CLI already does exactly that (`LocalRunStore`,
`execution/local_store.py`; `local finalize` at `cli.py:894`), which is the *right* place for it and
is independent of every trigger in `db/agri/`.

### 3.2 The accident-prevention value is real but much smaller than the machinery

The honest version of the value is: *"a re-run must not silently overwrite a published result."*
Measured against the code:

- **A clean re-run does not trip the guards.** `materialize_forecast_iteration` short-circuits on an
  identical existing row and returns (`procedures/materialize_forecast_iteration.sql:210-212`).
  A re-run with *changed* inputs raises `'forecast iteration key already has different immutable
  parameters or evidence'` (`:208-209`) — which is correct behaviour, and note it is implemented in
  the **procedure**, not in any of the 82 triggers.
- **The whole live re-run protection therefore comes from the checksum columns plus one procedure**,
  not from the guard layer. The guard layer adds immutability *after* finalization on top.
- **The `finalize_*` family protects a path nothing walks.** All four SQL finalizers have **zero call
  sites in `src/`**; the only Python occurrence of their names is a GRANT-verification string list in
  `routes/health.py:289-290,298`, in a route that is no longer deployed. They are reachable only via
  `verify_forecast_receipt_finalization.sql:11-12` and `verify_forecast_hindcast_finalization.sql:11-12`,
  which fire on `UPDATE OF status` of `forecast_receipt` / `forecast_hindcast_run` — and **no Python
  code in `src/` writes either table** (only ORM class exports, an FK in
  `models/strategy_selection.py:289-290`, and read models). 886 lines of gate logic with no writer.
- The CLI's own `finalize` verbs (`local finalize`, `historical-nasa-finalize`,
  `historical-era5-finalize`, `historical-usdm-finalize`) are unrelated Python/filesystem logic. Two
  independent finalization implementations exist; only the Python one is used.

**Conclusion: the value is accident-prevention, not tamper-evidence, and it is already delivered by
the checksum columns and one stored procedure.**

---

## 4. Cost of keeping

### 4.1 It caused both defects that 0017 had to fix, and both trace to the same 2 columns

`…0017_pin_routine_search_path_and_float_rendering.py` fixed:

- **Defect 1 — the warehouse could not be restored from its own `pg_dump`.**
  `plans/postgresql-18-migration-rehearsal-2026-08-02.md:411-420`: `pg_restore` fails with
  `COPY failed for table "forecast_iteration_value": ERROR: function digest(text, unknown) does not exist`.
  Root cause, quoted at `:416-418`: *"`value_checksum` is a stored generated column, so `pg_dump`
  omits its values and the restoring server **recomputes** them during `COPY`."* The doc is explicit
  at `:426` that **this is not a PostgreSQL 18 problem** — it reproduces on pg16. The consequence at
  `:438-442`: *"The agri warehouse cannot be restored from its own `pg_dump` output by an unattended
  stock `pg_restore`, on any PostgreSQL major… only via a manual `ALTER FUNCTION`… a step that
  appears in no runbook and which nobody knew was required until this rehearsal."*
  **A plain column would have been dumped as a literal and no function would have been called.**
- **Defect 2 — a live receipt-integrity defect**, `…:471-493`: `forecast_hindcast_value_checksum`
  pinned only `TimeZone` while rendering six `double precision` args through `::text`, so session
  `extra_float_digits` leaked into a governed digest — measured, same inputs, `0.3` vs
  `0.30000000000000004` giving `ba088fd9…` vs `dac955c2…`. Also a **generated-column** checksum
  function.

Blast radius of the fix: **24 routines** altered (21 `search_path` pins + 1 full rendering pin + 2
defensive), documented at `db/AGENTS.md:249-254`. 21 of 91 functions shared the latent pattern
(`plans/postgresql-18-migration-rehearsal-2026-08-02.md:459-469`).

Both defects existed for weeks and were found only because someone ran a full cross-major dump/restore
rehearsal. Neither would exist without the two stored generated checksum columns — the two mechanisms
in the entire layer with **zero** security value (§2).

### 4.2 It makes routine operations awkward

- **There is no cleanup path for a botched run.** `guard_forecast_receipt_change.sql:11` raises on
  `TG_OP = 'DELETE'` *unconditionally* — a `forecast_receipt` row can never be deleted at any status.
  Same for `forecast_iteration` (`guard_forecast_iteration_change.sql:15-17`), `forecast_value`
  (`guard_forecast_value_write.sql:16-19` returns only for `INSERT` on a staging receipt; DELETE
  always raises), `forecast_hindcast_value` (`guard_forecast_hindcast_value_write.sql:12-14`), and
  `forecast_backtest_metric` (`guard_forecast_backtest_change.sql:12-14`). All five triggers are
  wired `BEFORE INSERT OR DELETE OR UPDATE` (`triggers/forecast_value.sql`, `triggers/forecast_hindcast_value.sql`).
  *Fairness note:* this is a **latent** hazard, not a demonstrated one. `forecast-run-iteration`
  wraps the whole `CALL` in one transaction (`cli.py:538`), so a crash rolls back and leaves nothing.
  The hazard bites any future path that commits between staging insert and finalization — which is
  precisely what the receipt/hindcast planes are designed to do.
- **Schema evolution requires disabling the guards.** 0014 had to bracket a one-line backfill with
  `DISABLE TRIGGER` / `ENABLE TRIGGER` (`…0014:165-178`). Any future column addition to a guarded
  table needs the same dance on 35 tables.
- **Tests need a bypass to build fixtures** (`tests/test_forecasting_v1_upgrade_postgresql.py:209,223`).
- **3,082 lines of tests exist only to prove the layer works**, plus 577 of those specifically proving
  a `SECURITY DEFINER` lockdown that serves a role model that was rejected.

### 4.3 A large share governs planes with no rows and no path

- **Strategy/selection family: 775 SQL lines** across 17 files (`guard_strategy_*`,
  `finalize_strategy_*`, `strategy_*_checksum`, `export_strategy_label_bundle`,
  `require_strategy_initial_state`), plus 12 triggers, plus revision 0013 (664 lines), plus
  `test_strategy_selection_finalization_postgresql.py` (1,251 lines). `db/AGENTS.md:212-219` records
  that *"revision `0013` deliberately refuses every `effect_candidate` finalization"* — the central
  path is closed by design, and project memory records the plane as zero-row and not a trainable
  target.
- **Intervention/evidence family: 378 SQL lines**, 7 triggers, revision 0009 (926 lines).
- Together ≈ **1,153 of the 3,285 layer lines (35%)** guard planes that are not in use.

---

## 5. Cost of removing

### 5.1 Nothing is live — verified, not assumed

Queried the database `.env` points at (`switchback.proxy.rlwy.net:37967/plantgeo`, the Railway
production instance):

```
agri schema present: 0
agri tables:         0
schemas: public, tracking, geo, drizzle, toolkit_experimental, timescaledb_*
```

**The `agri` schema does not exist in production.** There is no deployed data carrying checksums,
so no migration has to preserve any. (See §7 for the local-warehouse caveat.)

### 5.2 What breaks if the *checksum columns* go — a lot

Checksums are load-bearing throughout the Python layer: **31 of 51 `src/` files** reference them,
**837 lines**; **18 files compute SHA-256 themselves** to satisfy NOT-NULL columns. Canonical example:
`cli.py:974` `checksum = hashlib.sha256(payload).hexdigest()` → `execution/source_ingestion.py:334`
`checksum_sha256=payload_checksum` into a `nullable=False` column
(`models/provenance.py:162`) that also carries a CHECK re-deriving the digest from the bytes
(`models/provenance.py:183`, mirrored at `tables/artifact.sql:20`).

CLI commands that break if checksum columns are dropped (12 of 33):
`forecast-refresh-ml-daily`, `forecast-run-iteration`, `forecast-reconcile-actuals`, `source-ingest`,
`historical-nasa-backfill`, `historical-nasa-finalize`, `historical-era5-persist`,
`historical-era5-finalize`, `historical-usdm-backfill`, `historical-usdm-finalize`,
`historical-promotion-spool`, `historical-promotion-upload` (plus `local publish`, server-side).

Commands that do **not** break (checksums are local-file only): `seed`,
`strategy-label-map-preflight`, `strategy-train`, `db-status`, `db-upgrade`, `job-logs-maintain`,
`local init/status/checkpoint/interrupt/resume/register-output/finalize`, `source-ingest-status`,
`historical-nasa-status`, `historical-nasa-materialize-parquet`, `historical-era5-backfill`,
`historical-era5-materialize-parquet`, `historical-usdm-status`, `pipeline-status`.

**This is the argument for keeping the columns.** Note that the checksum discipline the CLI actually
relies on day to day is its own: `if checkpoint.plan_checksum != historical_era5_plan_checksum(plan)`
(`cli.py:1231`, and the same shape at `:1291`, `:1429`, `:1525`) — "you changed the plan since the
last checkpoint, refuse to resume". Pure accident-prevention, computed in Python, zero DB triggers
involved, and it works.

### 5.3 What breaks if the *enforcement* goes — nearly nothing

- `finalize_*` (4 fns, 886 ln): **zero Python callers.** Only tests.
- `guard_*`, `enforce_*`, `verify_*`, `require_*`, `record_*`: no Python calls them; they fire from
  triggers. Removing them removes checks, not call sites.
- The 2 generated `value_checksum` columns: consumed by two views
  (`v_forecast_hindcast_outcome.sql:47`, `v_forecast_iteration_outcome.sql:55`) and aggregated into
  the two receipt digests (`forecast_iteration_receipt_checksum.sql:43`). Both digests can call the
  checksum function inline over the row and produce a **byte-identical digest**.
- The 4 `SECURITY DEFINER` owner roles and `routes/health.py:277-298`'s privilege probe: the routes
  are undeployed.

Migration cost: **1 revision** (`0018`) doing `DROP TRIGGER` ×76, `DROP FUNCTION` ×55,
`ALTER TABLE … DROP COLUMN value_checksum` ×2, `CREATE OR REPLACE` ×2 receipt functions and ×2 views,
`DROP CONSTRAINT` ×10 evidence CHECKs, `DROP ROLE` ×4. Touches ~35 tables, but **no data**, because
there is none. Risk: **low**.

---

## 6. Options, ranked

### ▶ Option 3 (recommended) — Keep checksums as records; delete the enforcement

**Keep** (285 SQL function lines, 6 triggers):

1. All **61 plain checksum columns** and their 29 format CHECK constraints. These are the
   reproducibility record and they cost nothing at runtime.
2. The identity UNIQUE constraints built on them (`forecast_hindcast_run.sql:62`,
   `release_set.sql:33`, `source_release.sql:39`, `forecast_entity_state.sql:29`).
3. **Four checksum functions** — `forecast_iteration_value_checksum`, `forecast_hindcast_value_checksum`,
   `forecast_iteration_receipt_checksum`, `forecast_hindcast_receipt_checksum` (272 lines) — with
   their 0017 determinism pins intact, but **called explicitly**, not via a generated column.
   *(Amended 2026-08-03: the owner cut the receipt/hindcast planes — see §7 item 2. The two
   `*_receipt_checksum` functions then have no table to digest and go with them; the two
   `*_value_checksum` functions stay regardless, since they are what the generated columns are being
   converted to explicit calls of.)*
4. `procedures/materialize_forecast_iteration.sql` including its idempotency block (`:186-212`).
   This is the live re-run protection.
5. **One** immutability rule: `guard_forecast_immutable_rows` (13 lines, 6 triggers) on the
   append-only reference tables, amended so DELETE is permitted while a row is non-final.

**Remove** (3,000 SQL function lines, 76 of 82 triggers, 2 columns, 10 constraints, 4 roles):

| Remove | Files | Lines | Why |
| --- | ---: | ---: | --- |
| The 2 `GENERATED … STORED` `value_checksum` columns | 2 tables | — | Zero tamper value; sole cause of the `pg_restore` blocker and of 0017 defect 2. **Do this one first even if you keep everything else.** |
| `finalize_*` | 4 | 886 | No writer exists in `src/` |
| `verify_*` | 6 | 240 | Thin wrappers around the above |
| `enforce_*` | 9 | 408 | Duplicates CLI-side validation |
| `guard_*` except the one kept | 16 | 659 | Speed bumps against a nonexistent adversary; source of the no-cleanup-path hazard |
| `require_* / record_* / protect_* / reject_* / prevent_*` | 13 | 464 | Bookkeeping |
| Strategy/intervention checksum functions | 7 | ~343 | Zero-row, deliberately-closed planes |
| 12 `SECURITY DEFINER` fns + 4 owner roles | — | 442 mig. | Serve a role model that was rejected |
| 10 status↔checksum evidence CHECKs | — | — | State machine moves to the CLI |
| 8 test files | 8 | 3,082 | Prove removed behaviour |

**Migrations:** 1 (optionally 2 — split "retire the strategy/intervention plane" from "retire the
enforcement layer" so each is independently revertible). **Risk: low**, because §5.1 verified there
is no data.

**Why this one, in the owner's terms.** The layer's stated purpose is tamper-evidence, and §3.1 shows
tamper-evidence cannot work when the researcher, the DBA and the hypothetical adversary are one
person with one credential — the codebase already ships two bypasses. What is left is
accident-prevention, and §3.2 shows that is already delivered by the checksum columns plus one
procedure. Meanwhile the measured cost is concrete: 3,285 SQL lines + 3,082 test lines + 82 triggers
carried on every schema change; 9 of the last 16 commits; a `DISABLE TRIGGER` bracket needed to add
one column; a backup-recovery step nobody had written down; and two real defects, **both** in the two
mechanisms with zero security value. That is the definition of surface area for bugs and maintenance
with no offsetting benefit. Keeping the checksums keeps everything the research actually needs —
"which inputs made this forecast, and does it re-derive?" — for essentially free.

**What is irreversibly lost:**
- Any future claim that a finalized row is provably unedited. As argued, that claim was never sound
  here; but if this tool ever gains a second writer or an external auditor, this layer would have to
  be rebuilt (and rebuilt properly, with the digest stored outside the database).
- DB-enforced state machines. A buggy script could write `status='finalized'` with no values. The
  CLI must own that check; today it partly does (`_require_sha256`, `cli.py:1760`).
- The `forecast_input_recorded_at` audit trail (`record_forecast_input_change.sql`).
- 3,000 lines of SQL plus 3,082 lines of tests, already written and paid for, plus the governance
  narrative in `db/AGENTS.md:185-259` and `alembic/AGENTS.md`, which goes stale.

### Option 4 — Keep value-level checksums, drop finalize/immutability guards *(close second)*

Same as Option 3 minus the strategy/intervention teardown: drop `finalize_*`/`verify_*`/`guard_*`/
`enforce_*` (2,206 lines, 43 triggers) but leave every checksum function and the zero-row planes in
place. **Migrations: 1. Risk: very low.** Captures ~75% of the benefit for ~60% of the work, and is
the right pick if there is any chance the strategy plane gets revived. Still **must** include the
generated-column drop.

### Option 5 — Move enforcement to the CLI, keep the schema

Keep every column and constraint; delete the triggers; re-implement "don't overwrite a finalized row"
as an explicit precondition in `cli.py`. Attractive because the CLI is already the only writer and
already does this for local checkpoints (`cli.py:1231`). Ranked below Options 3/4 only because it
implies *writing* new enforcement code, and the honest finding is that the existing procedure-level
idempotency already covers the live path. Merge it into Option 3 if you want belt-and-braces.

### Option 6 — Keep guards, drop the receipt/bundle checksum family

Rejected. This is backwards: the receipt digest is the cheapest and most useful artifact in the
layer (one value that pins a whole result), while the guards are the expensive part. It would also
leave the generated columns and therefore the restore blocker.

### Option 7 — Keep as-is, because 0017 already paid the fix cost

Rejected, and this is the trap. 0017 fixed two *instances*; it did not remove the *class*. The
generated-column recompute-on-restore behaviour is still there, so any future function this layer
touches can reintroduce Defect 1, and `db/AGENTS.md` notes the detection pattern is case-sensitive and
would miss a lowercase `st_*` call (`plans/postgresql-18-migration-rehearsal-2026-08-02.md:466-467`).
Sunk cost is not a reason to carry 3,285 lines forward. That said, if the answer is "keep", 0017 did
leave the layer in a genuinely correct state, and doing nothing is safe — just not cheap.

### Option 8 — Delete everything including the columns

Rejected. §5.2 measures the blast radius: 31 Python files, 837 lines, 12 of 33 CLI commands. The
checksums are the one part that unambiguously earns its keep for a research tool.

---

## 7. What I could not determine

1. **Whether a local warehouse holds `agri` rows.** The Railway production DB definitively has zero
   `agri` tables (§5.1). I could not reach `127.0.0.1:5442` (`ConnectionError` on `plantgeo`,
   `plantgeo_forecast_test`, and `postgres`) so I cannot rule out local rows carrying checksums.
   `plans/postgresql-18-migration-rehearsal-2026-08-02.md:487` states `forecast_hindcast_value` was
   empty as of 2026-08-02 and the rehearsal fingerprinted 68 tables, implying a populated local DB
   exists. **Confirm before writing any destructive migration.**
2. **Whether the receipt / publication / hindcast planes are intended to be wired up.** They have no
   Python writer today, which is what makes `finalize_*` free to remove. If the plan is to wire them
   next month, Option 3 discards work that would need rebuilding. This is the single question that
   could flip the recommendation, and only the owner can answer it.

   > **ANSWERED 2026-08-03 — owner: *"does not seem needed lets cut it."* Cut the planes.**
   >
   > **Correction to this audit.** This item framed the planes as *unused*, and §5.3 concluded that
   > removing the enforcement breaks "nearly nothing". Both are true of the *enforcement*, and both
   > understate the blast radius of dropping the plane **tables**, which the audit did not measure:
   >
   > `agri.mv_forecast_ml_daily_serving` — the ML forecast serving lane — is built on
   > `agri.v_forecast_series_serving`, which joins
   > `publication_pointer → forecast_publication → forecast_publication_item → forecast_receipt →
   > forecast_value` (`db/agri/views/v_forecast_series_serving.sql:53-59`) and is uniquely indexed on
   > `(publication_id, forecast_receipt_id, series_id, valid_day)`
   > (`db/agri/materialized_views/mv_forecast_ml_daily_serving.sql:47`).
   > **Dropping the publication/receipt tables therefore deletes the ML serving lane**, which
   > `plans/ingestion-warehouse-consolidation-2026-08-03.md` §6 relies on for the slider's ML variant.
   >
   > Two admissible readings, and the choice belongs in that plan's Phase 2, not here:
   > **(a)** cut the enforcement and keep the plane tables as plain storage — i.e. Option 4, which
   > already "leaves the zero-row planes in place" — so the ML lane keeps working unchanged; or
   > **(b)** cut the tables too and rebuild the ML lane narrow, writing
   > `(geometry_id, metric_name, valid_on, issued_on, p10, p50, p90)` directly into the serving fact
   > table. §3.2's finding that **no `src/` code writes `forecast_receipt` or `forecast_publication`**
   > makes (b) cheap today and expensive once rows exist.
   >
   > Nothing else in §6's recommendation changes: the checksum columns, the identity UNIQUEs,
   > `materialize_forecast_iteration`'s idempotency block and the 11 checksum functions are kept
   > either way, and the two `GENERATED … STORED` `value_checksum` columns go either way.
3. **Current row counts for the strategy/intervention planes.** The "zero rows" claim comes from
   project memory and is corroborated by `db/AGENTS.md:212-219` (0013 refuses every
   `effect_candidate` finalization), but I did not query them.
4. **Runtime cost of the guards.** 82 row-level triggers, several doing `SELECT … FOR SHARE` on a
   parent per row (`guard_forecast_value_write.sql:12-15`), should measurably slow bulk inserts. No
   benchmark exists in the repo, so I cannot put a number on it.
5. **Whether a squash-to-baseline is viable.** With no production schema, replacing the 17 revisions
   (9,908 lines) with one regenerated baseline would be far cheaper than a 2,985-line drop migration.
   It conflicts with the forward-only contract at `db/AGENTS.md:15-19` and would invalidate any local
   DB, so it depends entirely on item 1.
6. **Whether the exact ~53 function / 73 trigger drop counts in §5.3 are right to the unit.** They are
   derived by subtracting the retention list from the inventory; the inventory counts are exact, the
   subtraction assumes no function is shared with a retained caller. A `DROP FUNCTION` without
   `CASCADE` will surface any mistake immediately.
