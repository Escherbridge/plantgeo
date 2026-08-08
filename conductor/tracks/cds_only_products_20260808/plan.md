---
type: track-plan
track: cds_only_products_20260808
status: planned
---

# Plan

See [`./spec.md`](./spec.md) for the measured findings this plan implements against.

Three phases, strictly ordered by credential cost, not by product importance. Phase 1 (AgERA5)
ships first because it needs no new credential plumbing at all. Phase 2 (CEMS) is explicitly
gated behind provisioning a second Copernicus credential surface before any plan is authored.
Phase 3 (seasonal forecasts) is scoping only — no code — because its dataset ids were not verified
to the same standard as the other two.

| Phase | Scope | New credentials | Runner |
|---|---|---|---|
| 1 | AgERA5 agrometeorological indicators | none — reuses classic CDS host | `durable-backfill.sh agera5 <plan>` |
| 2 | CEMS fire danger indices | new EWDS account + key pair, gated | `durable-backfill.sh cems <plan>` |
| 3 | Seasonal forecasts (SEAS5 / C3S) | unconfirmed | not scoped — no runner yet |

## Effort

This reuses a working, credentialed CDS client shape (`historical_era5.py`) rather than building
new I/O plumbing from nothing — the sibling tracks' own estimates for similarly-shaped work
(a day or two per lane once the client already exists) are the closest comparable. Call this a
guess from structure, the same caveat every other track in this repo carries on its estimate.

Phase 1 is a day or two for the evaluation-and-cardinality tasks, then a day or two for the
contract classes, plan generator, and CLI verbs once the variable set is settled — the value here
is bounded mostly by how open questions 1 and 2 resolve, not by unknown engineering. Phase 2 is
the same shape of work plus the credential-plumbing task, which is new to this repo (no prior lane
has threaded a second host/key pair through `Settings`) and should be sized as its own half-day to
a day separate from the dataset integration itself. Phase 3 is scoping effort only — reading CDS's
seasonal dataset catalogue to the same verification standard as products 1 and 2 — on the order of
a few hours, explicitly not an implementation estimate.

## Phase 1 — AgERA5 agrometeorological indicators

No credential gate. Start here.

- **Evaluate `sis-agrometeorological-indicators-timeseries` before writing any gridded-product
  code.** Confirm whether its point/region coverage fits PlantGeo's existing 0.1-degree cell
  lattice well enough to source AgERA5 entirely from it — if so, this phase never needs a NetCDF
  gridded-request path at all, which changes the shape of every task below it. Record the
  decision and its evidence; do not silently default to the gridded product because it is the
  more obvious CDS pattern to copy from `historical_era5.py`.
- **Confirm request cardinality on the live CDS request form for whichever product is chosen**,
  before any plan file is checksummed (spec finding 7). Unlike `derived-era5-land-daily-statistics`,
  AgERA5's gridded product retrieves one variable and one statistic per call — size the eventual
  variable set against the measured per-period request count, not against the retired lane's
  `cost = 2 x variables x days` figure, which does not transfer.
- **Identify which variables are genuinely new.** Cross-check the candidate AgERA5 variable list
  (`2m_temperature` and its required statistics, precipitation flux, solar radiation flux, vapour
  pressure, wind speed, relative humidity) against the existing NASA POWER lattice (9 signals,
  397 cells, complete) and the Open-Meteo archive lane. Do not re-ingest a signal this platform
  already has from a keyless or already-credentialed source — the value of this phase is closing
  a real coverage gap, not duplicating one.
- **Pin the dataset version explicitly.** Every request and every authored plan records
  `version: "2_0"` as an explicit field, never a default — record why in the plan (published
  2025-05-15, supersedes `1_1`).
- **Author sibling contract classes**, not a reuse of `Era5LandPeriod` / `HistoricalEra5LandBackfillPlan`
  (spec finding 3): a period/window type reflecting AgERA5's actual per-variable-per-statistic
  request shape, and a plan type with `dataset: Literal["sis-agrometeorological-indicators"]` (or
  the timeseries dataset id, per the first task above), following `HistoricalBackfillWindow`'s
  existing four-calendar-year window contract from `execution/historical_backfill.py`.
- **Register a new `data_source`** (DML, no Alembic revision — the same "ensure" pattern every
  existing lane already uses).
- **Author the plan file with a generator, not by hand.** A sibling to
  `plans/author_pnw_soil_moisture_plans.py`, following the existing
  `<source>-<region>-<purpose>-<start>-<end>.json` naming convention (spec finding 8). A
  hand-typed plan risks a wrong lattice or checksum that looks valid forever while pointing at
  nothing.
- **Add `historical-agera5-backfill` / `historical-agera5-persist` CLI verbs**, modeled on the
  ERA5 verbs' shape, and decide their persist-completeness signal deliberately (spec finding 4) —
  most likely the era5-style "exit non-zero on an incomplete checkpoint" shape, since this lane
  inherits the checkpoint-and-plan-checksum contract, not the open-meteo one, but confirm rather
  than assume.
- **No `durable-backfill.sh` change needed** (spec finding 4) — it dispatches by string
  interpolation. Run it as `./durable-backfill.sh agera5 <plan-path>` once the CLI verbs exist.
  If the multi-year walk needs to self-drive across days, wire it the same way the retired ERA5
  lane did: a Windows scheduled task (`schtasks`, Git Bash `-lc`, ~20-minute cadence), not a
  harness cron.
- **Record, do not solve, the ML-invisibility caveat** (spec finding 6): every new `signal_name`
  this phase lands is invisible to model training until a new `agri.covariate_feature_schema`
  version is authored. State this in the release notes.

**Acceptance:** the timeseries-vs-gridded decision is recorded with its evidence; a new plan file
and release set exist, independent of every existing CDS or Open-Meteo plan's checksum; a real
backfill run against `durable-backfill.sh agera5 <plan>` lands governed, bounded rows for at least
one full period; the ML-invisibility caveat is recorded, not silently assumed away.

## Phase 2 — CEMS fire danger indices

**Gated. Do not author a plan or attempt a `retrieve()` call until the credential precondition
below is confirmed working.**

- **Provision the EWDS credential surface first, as its own reviewable unit of work.** Register a
  second, separately-registered account at `ewds.climate.copernicus.eu`, accept its terms/licence
  click-through, and add two new `Settings` fields (an EWDS URL and key, mirroring the
  `cdsapi_url`/`cdsapi_key` shape at `config.py:130-133`) plus the matching local `.env` entry and
  Railway variable pair. This is new plumbing, not an extra dataset id on the existing credential
  pair (spec, product 2).
- **Confirm the credential surface works before writing any CEMS-specific code**: a real,
  non-404 request against `cems-fire-historical-v1` on the EWDS host. The known failure mode —
  `"dataset cems-fire-historical-v1 not found"` — is the classic client pointed at the wrong host,
  and this task exists specifically to catch that at activation time rather than mid-backfill.
- **Resolve the resolution ambiguity on the live request form** (open question 3) before
  checksumming any plan: is the live grid 0.25 degree, 0.5 degree, or `product_type`-dependent
  (deterministic vs. ensemble)? Record the evidence, not a guess from either the landing page or
  the Confluence guide alone, since the two disagree.
- **Author sibling contract classes** for CEMS: `dataset: Literal["cems-fire-historical-v1"]` (and
  a second type for `cems-fire-seasonal` if this phase's scope extends to it), `product_type:
  Literal["reanalysis"]`, `system_version` (seen as `4_1` — confirm on the live form rather than
  hardcoding from this spec alone), the full Canadian FWI / US NFDRS / Australian McArthur
  variable list from the spec, and the resolved `grid` value from the task above.
- **Register a new `data_source`.**
- **Author the plan file with a generator**, same convention as Phase 1.
- **Add `historical-cems-backfill` / `historical-cems-persist` CLI verbs**, with the same
  deliberate exit-semantics decision Phase 1 makes (spec finding 4).
- **Run via `./durable-backfill.sh cems <plan-path>`** — no script change needed, same as Phase 1
  (spec finding 5, this is a checkpoint-and-plan-checksum lane, not an `ingest-backfill` source).
- **Record the ML-invisibility caveat and defer the serving decision** (open question 4) — this
  phase ingests; whether CEMS output becomes a map layer, an ML covariate, both, or neither is an
  explicit open question this phase does not resolve.

**Acceptance:** the EWDS credential surface is provisioned and confirmed working with a real
non-404 request, recorded as its own step separate from any backfill; the resolution ambiguity is
resolved with cited evidence before any plan is checksummed; a new plan file and release set exist;
a real backfill run against `durable-backfill.sh cems <plan>` lands governed, bounded rows for at
least one full period; the ML-invisibility caveat and the deferred serving decision are both
recorded.

## Phase 3 — seasonal forecasts (scoping only)

No code in this track. The deliverable is verification, not implementation.

- Identify the actual SEAS5 / C3S seasonal dataset ids on CDS or EWDS (host is itself
  unconfirmed) to the same standard products 1 and 2 were verified to this session — live request
  form, confirmed variable names, confirmed resolution and temporal coverage.
- Record whichever of AgERA5's and CEMS's settled findings transfer (credential host, request
  cardinality shape, coverage-window contract) and which do not.
- Do not author a plan, a contract class, or a CLI verb. This phase's output is a spec-quality
  findings write-up an owner can use to charter a follow-on track, not working code.

**Acceptance:** dataset ids and host are confirmed or explicitly still unconfirmed with the reason
why; no code changes; a clear owner-facing recommendation on whether this is worth a follow-on
track given what Phase 1 and Phase 2 learned about CDS/EWDS queue latency and request cardinality.

## Verification

One sweep per phase, at the end of that phase — never test → fix → test inside one: `ruff check`,
`mypy` (strict), `pytest`. The floor is whatever the suite reports at the start of this track; the
number must not drop. Phase 3 has no code sweep, only its scoping write-up.

There is no `psql` on this machine. Verify any warehouse row this track lands the way every other
track in this repo does: `asyncpg`/`psycopg2` against the `postgresql://` `DATABASE_URL_SYNC`
connection string, never a `psql` invocation.

Multi-day CDS/EWDS walks self-drive via Windows scheduled tasks (`schtasks`, Git Bash `-lc`,
~20-minute cadence), the same mechanism the retired ERA5 soil lane used — not a harness cron.

Route the approval pass to `quality-reviewer`. The author does not self-approve, and every phase
here is a new credential-bearing upstream integration — exactly the class of change a second
reader exists to catch, and Phase 2 doubles that risk with a second credential host.
