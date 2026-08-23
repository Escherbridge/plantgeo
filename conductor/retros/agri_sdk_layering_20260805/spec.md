---
type: track-spec
track: agri_sdk_layering_20260805
status: planned
---

# Agri data service SDK layering — specification

Everything below was measured against the working tree on 2026-08-05 with `wc -l`,
`grep`, and direct reads. Line counts and `file:line` citations are evidence. Where
this document estimates rather than measures, it says so.

## Goal

`agri_data_service` becomes a layered library with a dependency direction that a
test can enforce, and a public surface a consumer can import without starting a
CLI. The nine production cron services keep working, byte-for-byte, throughout.

## The problem, measured

The package is 36,438 lines. `cli.py` is 2,474 of them and declares 39 leaf
commands. That is the visible symptom. Three measurements matter more.

**The command surface is 52 leaves, not 39, and it has two registration styles.**
`cli.py` declares 31 top-level `@cli.command`s plus a `local` group with 8
subcommands. A second, separate surface lives in
[`ingest/commands.py`](../../../services/agri-data-service/src/agri_data_service/ingest/commands.py),
which declares 13 standalone `@click.command`s and attaches them through
`register_ingest_commands(cli)` at
[cli.py:193](../../../services/agri-data-service/src/agri_data_service/cli.py#L193).
The registration-function pattern this track needs already exists and already
works; it has simply been applied exactly once.

**Every line of the production cron surface is in the 13, and none is in the 39.**
All nine scheduled Railway services invoke an `ingest-*` command. Not one invokes
anything declared in `cli.py`.

| Service | `startCommand` | Declared in |
|---|---|---|
| `cron-drought` | `agri-cli ingest-drought` | `ingest/commands.py:153` |
| `cron-evacuation-zones` | `agri-cli ingest-evacuation-zones` | `ingest/commands.py:205` |
| `cron-fire-perimeters` | `agri-cli ingest-fire-perimeters` | `ingest/commands.py:137` |
| `cron-firms` | `agri-cli ingest-firms` | `ingest/commands.py:89` |
| `cron-geometry-repair` | `agri-cli ingest-geometry-repair` | `ingest/commands.py:354` |
| `cron-ndvi` | `agri-cli ingest-ndvi` | `ingest/commands.py:173` |
| `cron-sensors` | `agri-cli ingest-sensors` | `ingest/commands.py:189` |
| `cron-streamflow` | `agri-cli ingest-streamflow` | `ingest/commands.py:105` |
| `cron-weather` | `agri-cli ingest-weather` | `ingest/commands.py:121` |

A tenth directory, `infra/cron-ingest/`, carries no `startCommand`; it is the
shared build image, whose `ENTRYPOINT` is `["agri-cli", "ingest-all"]`.

**The layering already exists — in prose, enforced by nothing.**
[`execution/AGENTS.md`](../../../services/agri-data-service/src/agri_data_service/execution/AGENTS.md)
describes module after module as "database-free", "evaluation-only", "commits
nothing itself", "the caller owns transaction boundaries", "never writes to the
warehouse". Those sentences are a dependency rule. Nothing checks them. A single
`from agri_data_service.db.engine import ...` added to
`vegetation_ndvi_forecast.py` would violate the documented contract, pass `ruff`,
pass `mypy`, pass all 955 tests, and be found only by a reader.

That is the actual defect. `cli.py`'s size is downstream of it.

## Settled findings

Measured. Implement against these; do not re-derive them.

**1. `models/` is not machine-learning models.** It is 3,434 lines of SQLAlchemy
ORM mappings. `models/forecasting.py` contains 33 `server_default=text(...)`
declarations and zero runtime `.execute(` calls. Any layering that reads the
directory name as "the ML layer" will be wrong. The real ML lives in
`execution/strategy_selection.py` and the real Monte Carlo in
`execution/vegetation_ndvi_forecast.py`.

**2. The pure-method code the owner named is 5.7% of the package.**

| Pure, database-free module | Lines |
|---|---|
| `execution/strategy_selection.py` (DiD / AIPW / ridge) | 912 |
| `execution/covariate_wind_model.py` (multi-horizon ridge) | 494 |
| `execution/vegetation_ndvi_forecast.py` (the Monte Carlo) | 369 |
| `execution/strategy_label_mapping.py` (label custody preflight) | 300 |
| **Total** | **2,075** |

Against 36,438 that is 5.7%. Acquisition and persistence are the bulk: `ingest/`
is 9,087 lines and the `historical_*` family in `execution/` is a further 7,435,
so 16,522 lines — 45% of the package — belong to neither ML nor Monte Carlo.
This is the single most important correction in this document.

**3. `covariate_wind_model.py` is not fully pure.** `execution/AGENTS.md:181`
calls it "database-free-at-the-core", then states it "reads the pinned covariate
vector and the WS2M target through their own availability-gated SQL functions".
It cannot enter a no-I/O layer unmodified. Its reader and its estimator need
separating — the only genuinely behavioural split in this track.

**4. Two modules do `__file__` path arithmetic, and one of them is in `cli.py`.**
[cli.py:314](../../../services/agri-data-service/src/agri_data_service/cli.py#L314)
is `Path(__file__).resolve().parents[2] / "alembic.ini"`, and
[db/sql_objects.py:26](../../../services/agri-data-service/src/agri_data_service/db/sql_objects.py#L26)
is `Path(__file__).resolve().parents[3] / "db" / "agri"`. Both encode the
module's exact depth in the tree. Moving either file one directory deeper
silently resolves to the wrong directory. A "pure move" is not pure for these two.

**5. The cron image copies `src/` only, on purpose.**
`infra/cron-ingest/Dockerfile` deliberately omits `alembic/`, `db/`, and
`alembic.ini` so that the container "must never run a migration ... by
construction rather than by trusting the ENTRYPOINT string". Its correctness
argument is that `cli.py` imports the `alembic` *package* at module level but
reads `alembic.ini` only inside `_alembic_config()`. That argument is
import-graph-dependent: any new import edge that causes an `ingest-*` command to
touch `alembic.ini`, or `db/agri/**`, at import time breaks all nine cron
services at runtime. **No test in the suite would catch it**, because tests run
in a full checkout where those files exist.

**6. That Dockerfile's citations have already drifted.** It cites
`_alembic_config()` at "cli.py:266-267". The function is at
[cli.py:313](../../../services/agri-data-service/src/agri_data_service/cli.py#L313).
The `cli.py:19` citation is still correct. Line-pinned cross-file comments rot;
this track will invalidate the rest of them and must update the file.

**7. The test suite reaches into `cli.py`'s module namespace.** Nine test modules
import from `agri_data_service.cli`, two of them private names —
`_load_run_plan` and `_strategy_seed_statement`. There are 22 `monkeypatch`
sites binding attributes *on the `cli` module object*, e.g.
`monkeypatch.setattr(cli_module, "_forecast_run_iteration", refresh)` in
`tests/test_cli_contract.py`. Moving a function out of `cli.py` removes the
monkeypatch target and fails the test with no behavioural change whatsoever.
This is why moves must be visible to review as moves.

**8. Runtime SQL sits in 13 modules, not the 15 sometimes quoted.** Counting
modules containing `.execute(` rather than any `text(`: `routes/historical_promotion.py` (29),
`execution/vegetation_ndvi_plane.py` (25), `db/maintenance.py` (11), `cli.py` (11),
`ingest/geometry.py` (7), `ingest/writer.py` (5), `routes/health.py` (4),
`execution/geospatial_pilot.py` (3), `db/sql_objects.py` (3), `ingest/usdm.py` (2),
and one each in `routes/forecasts.py`, `ingest/usdm_history.py`, `ingest/backfill.py`.
The 7 `models/*` modules that match a naive `text(` grep hold only ORM defaults.

## The proposed layering

Six layers. The rule is a strict downward dependency lattice.

```
agri_data_service/
  foundation/    L0  pure helpers: canonical JSON, checksums, time, units
  method/        L1  pure domain computation, no I/O
    monte_carlo/       ndvi_seasonal_anomaly_bootstrap_v1
    ml/                strategy selection, label mapping, covariate estimators
  warehouse/     L1  ORM mappings, engine/session factories, SQL object loading
  pipeline/      L2  upstream acquisition: ingest sources, historical backfill
  planes/        L3  binds method output and pipeline output into the warehouse
  interface/     L4  cli/ (command wiring only) and http/ (Sanic routes)
```

| Layer | May import | May **not** import |
|---|---|---|
| `foundation` | third-party only | any first-party module |
| `method` | `foundation` | `warehouse`, `pipeline`, `planes`, `interface`, **`sqlalchemy`**, `httpx` |
| `warehouse` | `foundation` | `method`, `pipeline`, `planes`, `interface` |
| `pipeline` | `foundation`, `warehouse` | `method`, `planes`, `interface` |
| `planes` | `foundation`, `method`, `warehouse`, `pipeline` | `interface` |
| `interface` | everything below | — |

`method` and `warehouse` are siblings at L1; neither may import the other.

**`method/monte_carlo` and `method/ml` are siblings, not a stack.** They are two
domains over one foundation, and neither imports the other. The owner's phrase
"a shared library between" describes `foundation`, which sits *below* both rather
than between them. This is a wording correction, not a scope change.

### Enforcing it

The repo already enforces architecture with contract tests —
`test_declarative_schema_parity`, `test_cli_contract`, six `*_migration_contract`
modules. Follow that precedent: a `test_layer_import_contract.py` that walks each
layer directory with `ast`, collects every `Import`/`ImportFrom`, and asserts the
table above. It is deterministic, needs no database, and fails with the offending
`file:line`.

`ruff` already selects `TID` (flake8-tidy-imports) in
[ruff.toml](../../../services/agri-data-service/ruff.toml), and its
`banned-api` setting can express per-directory bans if ruff's hierarchical config
discovery applies a nested `ruff.toml` to files beneath it. **That last clause is
a guess and must be verified before being relied on.** The AST contract test is
the primary mechanism regardless; ruff would be a fast-feedback bonus.

## The shared library — and what must stay out

`foundation` holds only things with **no domain meaning and no I/O**: canonical
JSON serialization, SHA-256 digest helpers, UTC/ISO-prefix date handling (the
repo's `substring(...,1,10)::date` rule has a Python twin), unit and range
guards, and the frozen-dataclass validation primitives that plan and checkpoint
types are built from.

A candidate belongs in `foundation` only if **all four** hold:

1. It imports no first-party module.
2. It imports no `sqlalchemy`, `httpx`, `asyncpg`, or `click`.
3. It is already used by at least two layers, or by both `method/ml` and
   `method/monte_carlo`. One caller is not shared.
4. Its name describes a mechanism, not a domain noun.

Explicitly **not** in `foundation`, because each is the junk-drawer failure mode:

- **`contracts.py` wholesale.** Its 783 lines are governed plan and checkpoint
  contracts — domain meaning, per-lane. Its generic validation primitives may be
  lifted; the `ExpectedOutput`-style types stay with their lane.
- **Anything named `utils`, `helpers`, `common`, or `shared`.** A module whose
  name does not say what it does cannot be reviewed for whether it belongs.
- **Config and settings.** `config.py` reads the environment. It is I/O.
- **Anything a single caller uses.** Move it when the second caller appears.
- **ORM base classes or `MetaData`.** Those are `warehouse`.

The rule that makes this stick: `foundation` may not grow in the same commit as
the caller that needs it. A new `foundation` symbol is its own reviewable change.

## The public SDK surface

The package is currently a closed application: `__init__.py` exports only
`__version__`, and every consumer is either `cli.py` or `app.py`. This track
makes the pure layers importable.

Public, re-exported from each layer's `__init__.py` and covered by the layering
contract test:

```python
from agri_data_service.method.monte_carlo import simulate_cells, SeasonalHistory
from agri_data_service.method.ml import (
    load_strategy_label_bundle,
    train_strategy_models,
)
from agri_data_service.foundation import canonical_json, sha256_digest
```

Private, and stated as private: everything in `warehouse`, `pipeline`, `planes`,
and `interface`. They may change shape without a version bump. A consumer wanting
persistence uses the CLI or the HTTP routes, not an import.

Mechanics: add `py.typed` (the package is `mypy --strict` already, so the types
are real), and give each layer an `AGENTS.md` carrying the rationale, with the
one-line pointer convention the repo already uses. Code keeps terse one-line
doc-comments.

## Cron compatibility contract

Binding for every phase.

1. **The 9 `startCommand` strings in `infra/cron-*/railway.json` and the
   `ENTRYPOINT` in `infra/cron-ingest/Dockerfile` are frozen.** No rename, no
   regrouping under a `click.Group`, no added required option. `agri-cli
   ingest-ndvi` must parse identically before and after.
2. **The full 52-leaf command surface is frozen**, not just the 9. Nothing
   catalogues who else calls `agri-cli`; absence of evidence is not evidence.
3. **`agri-cli` stays the single console script** at
   `agri_data_service.cli:cli` (`pyproject.toml:49`). If `cli.py` becomes a
   package, `agri_data_service/cli/__init__.py` must expose the same `cli` object
   so the entry point string never changes.
4. **The import graph reachable from any `ingest-*` command must not touch
   `alembic.ini` or `db/agri/**` at import time.** This is finding 5; it is the
   constraint most likely to be broken accidentally and least likely to be caught.
5. **Verification is the container, not the test suite.** The cron image must be
   built and each of the 9 commands invoked inside it with `--help`. Only that
   reproduces the deliberately-missing-files condition.

A violation of 1-4 is a production outage discovered on the next cron tick.

## Non-goals

- **Changing any command's behaviour, output, or JSON shape.** This track moves
  code and adds a public surface. Behavioural change is out of scope except the
  one split named in finding 3.
- **Extracting runtime SQL into `.sql` files.** That is the parallel SQL
  convention track. See the plan's sequencing section; this track must not
  duplicate it.
- **Splitting the large `historical_*` modules.** `historical_writer.py` at 1,824
  lines is a legitimate target and is not this track's. Layering first; splitting
  within a layer afterwards, with the layer boundary already protecting it.
- **Touching Alembic revisions or `db/agri/**`.** No migration is authored here.
- **Renaming `models/` to `warehouse/models/` in the same phase as anything
  else.** It is 3,434 lines of import churn and gets its own revert point.
- **Publishing to an index.** "Usable as a library" means importable in-repo and
  by the Sanic app; distribution is a later decision.

## Open questions — owner input required

1. Is `covariate_wind_model.py`'s reader/estimator split (finding 3) in scope
   here, or deferred? It is the only behavioural change proposed, and deferring
   it means `method/ml` ships with one module that violates the no-`sqlalchemy`
   rule and needs a documented exemption.
2. Should `warehouse/` absorb `models/` and `db/` under one name, or stay two
   directories inside one layer? Absorbing is cleaner and costs the larger
   import churn.
3. Does anything outside this repository invoke `agri-cli`? Contract item 2
   assumes the worst; a confirmed "no" would let a later track retire dead verbs.
