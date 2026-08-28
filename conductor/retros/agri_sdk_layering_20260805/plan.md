---
type: track-plan
track: agri_sdk_layering_20260805
status: planned
---

# Plan

Nine phases. Phase 0 adds safety nets and moves nothing. Phases 1-5 are pure
moves. Phase 6 is the dangerous one. Phases 7-8 are additive.

The repo must be green and deployable at the end of every phase. There is no
cutover commit. Each phase is a revert point: `git revert` of one phase must
leave a working tree, which means no phase may depend on a later phase's
existence.

| Phase | Scope | Kind | Risk |
|---|---|---|---|
| 0 | Safety nets: golden command surface, layering contract harness, cron image smoke | additive | none |
| 1 | Neutralize `__file__` path arithmetic | behavioural (tiny) | low |
| 2 | `foundation/` | move | low |
| 3 | `method/monte_carlo/` and `method/ml/` | move | low |
| 4 | `warehouse/` (`db/` + `models/`) | move | medium — import churn |
| 5 | `pipeline/` (`ingest/` + `historical_*`) | move | medium — largest |
| 6 | `interface/` — dissolve `cli.py`, relocate `routes/` | move + rewiring | **highest** |
| 7 | `planes/` | move | low |
| 8 | Public SDK surface: exports, `py.typed`, per-layer `AGENTS.md` | additive | low |

Phase 7 sits after Phase 6 deliberately: `planes` is defined as what is left in
`execution/` once method, pipeline, and interface have been lifted out. Naming
it earlier means guessing at its membership.

## Effort

This is not a small change. 36,438 lines, 52 command leaves, 72 test modules,
and a live production dependency.

Phases 0-1 are roughly a day. Each of phases 2, 3, 7 is a day or less. Phases 4
and 5 are two to three days each, dominated by import rewriting and re-running
the suite. Phase 6 is a week on its own, and is the phase where an estimate is
least trustworthy. Phase 8 is a day.

Call it **three to four working weeks** for one engineer, and treat that as a
guess with a long right tail — it is an estimate from structure, not from any
comparable refactor in this repo. The load is concentrated: Phase 6 is a quarter
of the calendar and most of the risk.

An honest alternative, if that is too much: **Phases 0-3 alone** deliver the
owner's stated intent — ML and Monte Carlo separated over a shared foundation,
with an enforced dependency rule — for about a fifth of the cost, and leave a
repo that is strictly better than today. Phases 4-8 are the rest of the package
catching up. Stopping after Phase 3 is a legitimate outcome, not a failure.

## Sequencing against the SQL extraction track

The two tracks overlap on exactly two files, and are naturally disjoint before
that.

Runtime SQL, by the phase that moves the file:

| Phase | Modules it moves that hold runtime SQL | `.execute(` count |
|---|---|---|
| 2, 3 | `execution/vegetation_ndvi_forecast.py` | 1 |
| 4 | `db/maintenance.py`, `db/sql_objects.py` | 11, 3 |
| 5 | `ingest/geometry.py`, `ingest/writer.py`, `ingest/usdm.py`, `ingest/usdm_history.py`, `ingest/backfill.py` | 7, 5, 2, 1, 1 |
| 6 | `cli.py`, `routes/historical_promotion.py`, `routes/health.py`, `routes/forecasts.py` | 11, 29, 4, 1 |
| 7 | `execution/vegetation_ndvi_plane.py`, `execution/geospatial_pilot.py` | 25, 3 |

**The governing rule: a file is SQL-extracted or moved in a given step, never
both.** A file that is rewritten and relocated in one change cannot be reviewed —
the diff shows a deletion and an addition, and no reviewer can confirm the SQL
was extracted faithfully.

**Layering goes first where the two collide, for one concrete reason:** the SQL
styleguide wants `.sql` files in "domain-specific folders", and the domain
folders do not exist until this track creates them. Extracting
`vegetation_ndvi_plane.py`'s 25 statements today means choosing a home under
`execution/`, then moving that home in Phase 7. Extracting after the move means
choosing it once.

Concretely:

- **Phases 0-3 can run fully in parallel with SQL extraction.** They touch one
  file with one `.execute(` call. Let the SQL track work anywhere it likes.
- **From Phase 4 the SQL track should work only on `routes/`** — 34 of the
  remaining statements, the largest single cluster, and untouched until Phase 6.
- **`cli.py` is the exception that runs the other way round: do not extract its
  11 statements at all.** Phase 6 dissolves the file. Extracting SQL from a
  module that is about to stop existing is work thrown away, and it guarantees a
  conflict on the repo's most dangerous file. Its SQL gets extracted after Phase
  6, from wherever each statement lands.
- **Freeze protocol:** when a phase starts, its file list is closed to the SQL
  track until the phase merges. The lists above are the freeze lists.

## Phase 0 — safety nets, before anything moves

Nothing in this phase changes behaviour or moves a line. It builds the
instruments the rest of the track is measured with, and they must be green
against today's tree.

- **Golden command surface test.** Walk the `cli` group recursively, collect
  every leaf command's full invocation path and its option strings, and assert
  against a checked-in fixture of all 52. A rename, a regrouping, or a new
  required option fails locally. Generate the fixture from the current tree —
  hand-typing it will get it wrong.
- **Layering contract test harness.** `test_layer_import_contract.py`, walking
  each layer directory with `ast` and asserting the spec's dependency table. It
  starts with an empty layer map and grows one entry per phase, so it is green
  today and never retrofitted.
- **Cron image smoke script.** Build `infra/cron-ingest/Dockerfile` and run each
  of the nine `ingest-*` commands with `--help` inside the container. This is
  the only check that reproduces the deliberately-absent `alembic.ini` and
  `db/agri/**`. It belongs in the repo as a script, not a one-off.
- Confirm ruff's hierarchical config discovery (spec, "Enforcing it"). If nested
  `ruff.toml` files do scope `banned-api` per directory, add them as fast
  feedback. If they do not, record that in `AGENTS.md` and rely on the AST test.

**Acceptance:** all three instruments exist and pass against an unmodified tree.
The command fixture lists 52 leaves. The cron smoke script exits zero.

## Phase 1 — neutralize the path arithmetic

Two lines, and they must change before any file moves, because moving them first
breaks things silently.

- Replace [cli.py:314](../../../services/agri-data-service/src/agri_data_service/cli.py#L314)
  `Path(__file__).resolve().parents[2] / "alembic.ini"` with a location that is
  not a function of the module's depth — an explicit setting with the current
  path as default is the smallest change that removes the coupling.
- Same treatment for
  [db/sql_objects.py:26](../../../services/agri-data-service/src/agri_data_service/db/sql_objects.py#L26)
  `parents[3] / "db" / "agri"`.
- Do not change *when* `alembic.ini` is read. The cron image's correctness
  argument depends on it being read inside `_alembic_config()` and nowhere else
  (spec finding 5). Resolution moves; laziness stays.

**Acceptance:** `agri-service ops db-status` still finds `alembic.ini`; the declarative
schema parity test still finds `db/agri/**`; the cron smoke script still passes.
Both files could now be moved anywhere without silent misresolution.

## Phase 2 — `foundation/`

- Create `agri_data_service/foundation/` with an `AGENTS.md` stating the
  four-part admission test from the spec, verbatim, before any symbol lands.
- Move only symbols that already have two or more callers across different
  future layers. Candidates observed while surveying: canonical JSON
  serialization, SHA-256 digest helpers, the ISO-date-prefix convention, and the
  frozen-dataclass validation primitives underneath `contracts.py`. **This list
  is a guess from reading module docstrings, not an audit** — Phase 2 begins by
  auditing actual call sites and may find fewer.
- If the audit finds only one genuinely shared symbol, create the layer with
  that one symbol. An almost-empty `foundation` is correct; a speculative one is
  the junk drawer the spec forbids.
- Register `foundation` in the layering contract test: imports no first-party
  module, imports no `sqlalchemy`/`httpx`/`click`.

**Acceptance:** `foundation` exists, the contract test asserts its emptiness of
first-party imports, and no call site changed behaviour.

## Phase 3 — `method/monte_carlo/` and `method/ml/`

The phase that delivers the owner's request. All four modules are already
documented as database-free, so this is a move plus a rule.

- `method/monte_carlo/` receives `execution/vegetation_ndvi_forecast.py` (369).
- `method/ml/` receives `execution/strategy_selection.py` (912),
  `execution/strategy_label_mapping.py` (300), and — subject to the spec's open
  question 1 — `execution/covariate_wind_model.py` (494).
- `covariate_wind_model.py` is the one module that is not purely a move. Its
  warehouse reads must be lifted into an injected reader so the estimator takes
  data as an argument. If the owner defers this, it stays in `execution/` for
  now and the contract test carries a named, dated exemption rather than a
  weakened rule. **Do not weaken the rule to admit it.**
- Add `AGENTS.md` to both. The rationale already written in
  `execution/AGENTS.md` §"Vegetation NDVI Monte Carlo" and §`strategy_selection`
  moves with the code — that prose is the most valuable documentation in the
  package and must not be orphaned. Leave a pointer behind in `execution/`.
- Register both in the contract test, including the `sqlalchemy` ban.

**Acceptance:** ML and Monte Carlo are separate importable packages over
`foundation`, neither imports the other, and the contract test fails if either
grows a `sqlalchemy` import.

## Phase 4 — `warehouse/`

Pure move, large import churn. `db/` is 478 lines and `models/` is 3,434, but
nearly every module in the package imports from one or the other.

- Resolve the spec's open question 2 first (one directory or two).
- `git mv` only. Do not reformat, do not reorder, do not fix an unrelated lint
  finding in a moved file — the diff must read as a rename so review can skip it.
- Rewrite imports mechanically. `ruff`'s `I` rules will re-sort; let them, in the
  same commit, so the sort is not mistaken for hand-editing.
- The ORM `MetaData` and declarative base land here, not in `foundation`.

**Acceptance:** `warehouse` registered in the contract test as importing
`foundation` only. Suite green. Cron smoke green — this phase changes what
`ingest/` imports, so the smoke script is load-bearing here.

## Phase 5 — `pipeline/`

The largest move: `ingest/` (9,087) plus the `historical_*` family (7,435).

- `pipeline/ingest/` receives `ingest/` **except `commands.py`**, which is
  interface and waits for Phase 6. `register_ingest_commands` keeps working
  across the split; that is exactly the seam it was built for.
- `pipeline/historical/` receives the nine `historical_*` modules from
  `execution/`.
- `geospatial_capture.py` (749) is acquisition and belongs here.
  `geospatial_pilot.py` (1,558) consumes a capture and writes a release set —
  that is `planes`, in Phase 7.
- Keep `ingest/AGENTS.md`'s deployment note with the code; the cron Dockerfile
  points at it.

**Acceptance:** contract test asserts `pipeline` imports `foundation` and
`warehouse` and never `method`. Cron smoke green — this phase moves the code all
nine services run.

## Phase 6 — `interface/` (highest risk)

Dissolving `cli.py`. Everything that can go wrong in this track goes wrong here.

**Why it is the riskiest phase:**

1. Nine production services invoke frozen command strings. A rename is an outage
   found on the next cron tick, not at deploy.
2. The import graph reachable from `ingest-*` must stay clear of `alembic.ini`
   and `db/agri/**` (spec finding 5). Splitting `cli.py` into command modules is
   precisely the operation that adds import edges, and the full test suite
   cannot see the problem.
3. 22 `monkeypatch.setattr(cli_module, ...)` sites break the moment their target
   leaves `cli.py`, and two tests import private names. These fail loudly for
   non-behavioural reasons, which trains a reader to dismiss failures in this
   phase. That is the trap.
4. `pyproject.toml:49` pins `agri_data_service.interface.cli:cli`. If `cli.py` becomes
   `cli/`, the package `__init__` must still expose `cli`.

**What makes it safe:**

- **Phase 0's golden surface test is the gate.** It must pass unchanged. If a
  command string has to change, this track has failed its contract, not found a
  reason to edit the fixture. Editing the fixture in this phase is prohibited.
- **Split the phase in two, merged separately.** 6a moves each command *body*
  out of `cli.py` into its layer, leaving thin `@cli.command` wrappers behind, so
  `cli.py` shrinks but every decorator and every module attribute stays put — the
  22 monkeypatch targets survive. 6b converts `cli.py` into `cli/` with one
  module per command family, each exposing a `register_*_commands(cli)` function
  modelled on the one that already works at `cli.py:193`, and updates the tests'
  monkeypatch targets in the same commit as the move that invalidates them.
- **Run the cron smoke script on every commit in this phase**, not at the end.
  It is the only instrument that sees risk 2, and bisecting a broken import graph
  across a week of commits is far worse than running a container build per commit.
- **Update `infra/cron-ingest/Dockerfile`'s comment** — its `cli.py:19` and
  `cli.py:266-267` citations both become meaningless, and one is already wrong
  (spec finding 6). Restate the argument structurally: name the invariant
  ("no `ingest-*` import path reads `alembic.ini`") and name the test that
  enforces it, instead of citing lines that will rot again.
- Do not deploy 6a and 6b in the same release. Let 6a sit through at least one
  full cron cycle of every schedule — the longest is `cron-drought`'s weekly
  `0 14 * * 4`, which is the real gate on this phase's calendar.

`routes/` (3,630) moves to `interface/http/` in the same phase but is
independent of the CLI work and carries none of this risk.

**Acceptance:** golden surface fixture passes byte-identical. Cron smoke green.
`agri-service` entry point unchanged in `pyproject.toml`. One full weekly cron cycle
observed green after 6a before 6b ships.

## Phase 7 — `planes/`

What remains in `execution/` after the lifts: `vegetation_ndvi_plane.py` (1,331),
`geospatial_pilot.py` (1,558), `promotion.py` (1,207),
`historical_promotion.py` (1,005), `historical_writer.py` (1,824),
`historical_export.py` (1,119), `publisher.py` (308), `hot_projection.py` (299),
`local_store.py` (445), `source_ingestion.py` (430), and whatever survives of
`contracts.py` (783).

- Move, register in the contract test as the only layer permitted to import both
  `method` and `warehouse`, and delete the now-empty `execution/`.
- `execution/AGENTS.md` is 285 lines of hard-won rationale. Split it to follow
  its modules; do not leave a stub pointing at nothing and do not let it become
  the one file nobody updates.

**Acceptance:** `execution/` is gone, every module has a layer, and the contract
test covers all six layers with no exemptions except any granted in Phase 3.

## Phase 8 — the public SDK surface

- Layer `__init__.py` re-exports exactly the symbols the spec names public.
- Add `py.typed`.
- `AGENTS.md` in every layer directory, each stating the layer's responsibility,
  its dependency rule, and its admission test. One-line pointer from code.
- Extend the layering contract test to assert that the public names actually
  import from a clean interpreter — an export that only resolves because
  something else was imported first is not a public surface.

**Acceptance:** a consumer can `from agri_data_service.method.ml import
train_strategy_models` in a fresh process with no database configured.

## Verification

**One sweep per phase, at the end of that phase — never test → fix → test inside
one.** Batch every edit in a phase, then run:

- Python: `ruff check`, `mypy` (strict, per `mypy.ini`), `pytest` — 955 passing
  is the floor and the number must not drop.
- JavaScript: `npm run test` (646), `npm run lint`, `npm run type-check`,
  `npm run build`.
- `check:data-boundary`.

Two additions specific to this track:

- **The cron image smoke script runs in every phase's sweep, and on every commit
  of Phase 6.** It is the only check that sees the constraint most likely to
  break production.
- **The golden command surface test runs in every sweep.** If it fails outside
  Phase 6, a move has changed the CLI, which no phase is allowed to do.

The JS sweep will not detect anything in phases 1-5 and 7-8 — nothing in `src/`
imports Python. Run it anyway at phase boundaries so a green tree means the whole
tree, but do not treat its passing as evidence about this track.

Route the approval pass to `quality-reviewer`. The author does not self-approve,
and this track's whole value is a boundary that only a second reader can confirm
was respected.
