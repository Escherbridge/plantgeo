# cron-era5-land-coverage-fill — gap-fill for the Open-Meteo ERA5-Land signal lane

**Cron note.** Minute `:55` is shared only with `cron-mtbs` (`55 7 * * 2`), which fires Tuesdays at
07:55 — this service runs `55 21 * * *`, so the two never land in the same minute, and 21:55 UTC sits
clear of the 05:00 NDVI tick, the 06:00 stream validator, the Thursday 14:00 drought tick and the
09:55–11:55 NASA POWER band. It is one hour behind `cron-era5-land-continue` (20:55) and one hour
ahead of `cron-era5-land-coverage-status` (22:55), so the family runs refresh → fill → report in
order.

## Not live

**This directory is inert.** No Railway service is provisioned against it. Writing a `railway.json`
schedules nothing.

## What it would run

`agri-cli coverage-fill` per SKILL.md §8 "gap-fill", once per reviewed plan, chained with `&&`.
Dry-run is the default and `--apply` is what writes, per SKILL.md §6.

**One invocation per plan, not one per lane.** `coverage-fill` requires `--plan`: one source key
owns several reviewed plans with different lattices and parameter subsets, so resolving a plan from
a source key alone would guess which lattice a hole belongs to. The verb narrows the lane census to
the signals THIS plan's parameters actually persist (`signals_this_plan_can_fill`), so each of the
three below drains its own signals and the three together cover all 8. `--source-key` is an
assertion the plan must satisfy: a mismatch fails loudly rather than filling a different lane.

| plan | signals it can fill |
| --- | --- |
| `open-meteo-era5-land-pnw-ndvi-lattice-20220802-20260802.json` | the 3 soil-moisture parameters -> `soil_water_content_layer_1..3` |
| `open-meteo-era5-land-pnw-soiltemp-20220802-20260802.json` | the 4 soil-temperature parameters -> `soil_temperature_level_1..4` |
| `open-meteo-era5-land-pnw-vpd-20220802-20260802.json` | `vapour_pressure_deficit_max` -> `vapor_pressure_deficit` |

`open-meteo-era5-land-archive` is a verified value of `agri.data_source.key` (production, 2026-08-11). Governed
absences come from `agri.signal_coverage_audit`, which already exists and already holds 31,564
`complete` / 1,965 `no_data` rows; a day both observed and excused across the lattice resolves to
covered, and it is never re-walked.

## Verified against the CLI

Spellings confirmed against `agri-cli coverage-fill --help` on 2026-08-11: `--plan` (required,
`click.Path(exists=True)`), `--source-key`, `--output-directory`, `--through`, `--probe-cells`,
`--apply`. `coverage-status` takes a **repeatable** `--source-key`, `--through` and `--json`.

## Blockers before this can be provisioned

1. **The plans are not in the image, and `--plan` is required.**
   `infra/cron-ingest/Dockerfile:39` copies `services/agri-data-service/src/` only, so `/app/plans/`
   does not exist and every invocation above fails on its first argument. A
   `COPY --chown=10001:10001 services/agri-data-service/plans/ plans/` is required — plain `COPY`
   lands root-owned while the image drops to `USER plantgeo` uid 10001 (`Dockerfile:42-46`), and
   this verb writes its authored plan into that directory. Not made here: the Dockerfile is outside
   this task's file boundary.

2. **A volume is required for the SERVED half to be worth anything.** The two outcomes are not
   equally durable. An EMPTY verdict writes governed-absence rows to Postgres and survives. A SERVED
   verdict calls `write_fill_plan` (`execution/coverage_fill.py:754`), which writes a plan artifact
   to container disk and **refuses to overwrite an existing one** — so on ephemeral disk the
   authored plan is destroyed at exit and re-authored identically on the next tick, forever. Mount a
   volume at `/app/plans`.

3. **Authoring is step 1 of 2 on the SERVED path.** This verb writes a plan; no walker cron consumes
   it. Until the backfill verb has a cron, a green tick on a SERVED run means "a fill plan exists",
   never "the hole is closed". Only the absence path is complete end to end.

4. **`&&` in a startCommand is unverified on Railway.** No other `infra/*/railway.json` uses a shell
   operator. Every refusal exits 0 by design, so the chain is expected to run all three; confirm
   Railway shells the command before relying on that.

5. **Expect real work only where days are MISSING.** `coverage-fill` acts on missing days and cannot
   close a *thin* day — one that landed some cells but under the lane's cell floor. Run
   `coverage-status` first and read the missing column, not the completeness percentage.

## Provisioning

Root Directory `/` **and** config-as-code path `infra/cron-era5-land-coverage-fill/railway.json` must
be set together; changing one fails, and `RAILWAY_DOCKERFILE_PATH` can never work. After a config
fix, deploy from source. Env: `LOCAL_SOURCE_LOADER_DATABASE_URL` on the public proxy, with
`DATABASE_URL` absent. This lane needs no provider credential.

`restartPolicyType: NEVER` means a failed tick is never retried and simply waits for tomorrow. That
is only safe because the work is ledger-driven and idempotent; Railway is a trigger, not a scheduler
with guarantees — no missed-tick backfill, no failure alerting.
