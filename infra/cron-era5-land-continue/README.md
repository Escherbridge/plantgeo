# cron-era5-land-continue — forward refresh for the Open-Meteo ERA5-Land signal lane

**Cron note.** Minute `:55` is shared only with `cron-mtbs` (`55 7 * * 2`), which fires Tuesdays at
07:55 — this service runs `55 20 * * *`, so the two never land in the same minute, and 20:55 UTC sits
clear of the 05:00 NDVI tick, the 06:00 stream validator, the Thursday 14:00 drought tick and the
whole 09:55–11:55 NASA POWER band, so the two lane families never contend for the provider or the
warehouse.

## Not live

**This directory is inert.** No Railway service is provisioned against it. Nothing on this lane is
scheduled anywhere except the Windows scheduled tasks on the owner's laptop, and that is still true
after this file was written.

## What it would run

`agri-cli historical-plan-continue` per SKILL.md §8 "forward refresh". Three plans, chained with
`&&`, because the eight signals of the `open-meteo-era5-land-archive` contract are split across three
plan documents (verified against `plans/` 2026-08-11; all three carry support key `era5-land-0.1deg`
over grid `sentinel2-ndvi-0p25deg`):

| plan | signals it carries |
| --- | --- |
| `open-meteo-era5-land-pnw-ndvi-lattice-20220802-20260802.json` | `soil_moisture_0_to_7cm_mean`, `soil_moisture_7_to_28cm_mean`, `soil_moisture_28_to_100cm_mean` |
| `open-meteo-era5-land-pnw-soiltemp-20220802-20260802.json` | the four `soil_temperature_*_mean` bands |
| `open-meteo-era5-land-pnw-vpd-20220802-20260802.json` | `vapour_pressure_deficit_max` |

The VPD plan's filename stem and its `release_set_key`
(`open-meteo-era5-land-pnw-ndvi-lattice-vpd-*`) differ; both are retargeted independently by the
verb, so do not "fix" one to match the other.

`open-meteo-era5-nasa-power-lattice-radiation-*.json` is deliberately **not** here: its source key is
`open-meteo-era5-archive`, a different row in `agri.data_source`, and no lane coverage contract is
declared over it.

`&&` is deliberate. A `ContinuationRefusal` exits zero (`execution/AGENTS.md` §"Exit semantics"), so
an unmoved frontier carries on to the next plan; only a real fault breaks the chain, and under
`restartPolicyType: NEVER` that is a failed tick that waits for tomorrow.

Daily is safe despite the cost: `MINIMUM_CONTINUATION_ADVANCE_DAYS` is 30, so at most one
continuation per 30 days per plan. Each one re-walks a full four-calendar-year window under a new
`source_release_id`, because `require_exact_four_calendar_years` makes "keep the start, push the end"
structurally impossible. **The duplicate cost is worse on this lane than on NASA's**: no reader
merges it. `covariate_daily_features.sql` filters `support_key = 'surface'` and this lane emits
`era5-land-0.1deg`; the climate-field view filters `source.key = 'nasa-power-daily'`. Until one of
those gates is widened, a duplicate lineage here is pure storage
(`execution/AGENTS.md` §"The window slides").

## Blockers before this can be provisioned

Ordered by severity. A reader who fixes only #2 gets a permanently green service that authors
nothing.

1. **The verb refuses on every tick in a container, and exits ZERO doing it.** The first gate of
   `_refusal` (`execution/plan_continuation.py:665`) is
   `if not source.driver_marked_complete and not allow_incomplete`. `driver_marked_complete` is
   `(local_execution_root / "locks" / f"{plan_stem}.done").is_file()`
   (`plan_continuation.py:189-194`), `local_execution_root` is `.agri-local-runs` relative to the
   image's `/app` (`config.py:128`), and that marker is written by the durable driver on the owner's
   Windows laptop. A fresh container's `.agri-local-runs` is empty, so every tick prints
   `driver_has_not_marked_the_plan_complete` and exits 0 — green forever, having authored nothing.
   That is the layer-lane standard's first principle inverted.

   **`--allow-incomplete` is NOT the fix.** It defeats a real guard: continuing a walk that never
   finished stacks a second lineage over an unfinished window. The genuine prerequisite is moving
   durable-run state (the `locks/*.done` markers and the checkpoint tree) off the laptop and onto
   storage the container can read — the same volume #3 needs.

2. **The plans are not in the image.** `infra/cron-ingest/Dockerfile:39` copies
   `services/agri-data-service/src/` and nothing else, so `/app/plans/` does not exist and `--plan`
   (a `click.Path(exists=True)`) fails on the first argument. A `COPY services/agri-data-service/plans/ plans/`
   is required — and it is **not sufficient on its own**: the image drops to `USER plantgeo` (uid
   10001) at `Dockerfile:42-46` while a plain `COPY` lands root-owned, and `--write` writes the
   successor beside its source. It needs `--chown=10001:10001`, or the plans directory needs to be
   a writable mount. Not made here — the Dockerfile is outside this task's file boundary.

3. **A volume is required, and the config does not declare one.** `--write` emits the successor plan
   next to its source, and `load_continuation_source` reads the durable checkpoint under
   `local_execution_root`. Both are ephemeral container disk. Without a volume mounted at
   `/app/plans` the authored plan is destroyed on exit, and the `superseding_sibling` guard — the
   single defence against stacking lineages, which once produced three overlapping continuations at
   ~3.94 M duplicate rows each — cannot see yesterday's output.

4. **Authoring is step 1 of 3.** This verb writes a plan document; it does not fetch and does not
   persist. The backfill and persist verbs still have no cron. A green tick here means "a successor
   plan exists", never "the live edge advanced".

5. **`&&` in a startCommand is unverified on Railway.** No other `infra/*/railway.json` uses a shell
   operator, so whether Railway runs the command through a shell at all has not been demonstrated
   here. Confirm it before relying on the chaining semantics described above; if it is not shelled,
   each plan needs its own service.

## Provisioning

Root Directory `/` **and** config-as-code path `infra/cron-era5-land-continue/railway.json` must be
set together; changing one fails, and `RAILWAY_DOCKERFILE_PATH` can never work because the repo-root
`railway.json` overrides it. After a config fix, deploy from source — a plain redeploy replays the
previous failed build snapshot. Env: `LOCAL_SOURCE_LOADER_DATABASE_URL` on the public proxy, with
`DATABASE_URL` absent. This lane needs no provider credential.
