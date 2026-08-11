# cron-nasa-power-continue — forward refresh for the NASA POWER signal lane

**Cron note.** Minute `:55` is shared only with `cron-mtbs` (`55 7 * * 2`), which fires Tuesdays at
07:55 — this service runs `55 9 * * *`, so the two never land in the same minute, and 09:55 UTC sits
clear of the 05:00 NDVI tick, the 06:00 stream validator and the Thursday 14:00 drought tick.

## Not live

**This directory is inert.** No Railway service is provisioned against it. A `railway.json` in the
repository is configuration waiting for a service, not a running cron. Nothing on this lane is
scheduled anywhere except the Windows scheduled tasks on the owner's laptop, and that is still true
after this file was written.

## What it would run

`agri-cli historical-plan-continue` per SKILL.md §8 "forward refresh". Three plans, chained with
`&&`, because `contracts_for_source("nasa-power-daily")` covers two contracts whose eleven signals
are split across three plan documents (verified against `plans/` 2026-08-11):

| plan | signals it carries |
| --- | --- |
| `nasa-power-western-na-weather-fast-20220806-20260806.json` | the 7 fast parameters (`PRECTOTCORR`, `RH2M`, `T2M`, `T2MDEW`, `T2M_MAX`, `T2M_MIN`, `WS2M`) |
| `nasa-power-western-na-weather-radiation-20220531-20260531.json` | `ALLSKY_SFC_SW_DWN` — split out because its measured AG-community frontier lags ~2 months |
| `nasa-power-western-na-soil-wetness-20220806-20260806.json` | `GWETPROF`, `GWETROOT`, `GWETTOP` |

`&&` is deliberate. A `ContinuationRefusal` exits zero (`execution/AGENTS.md` §"Exit semantics"), so
"the frontier has not moved" carries on to the next plan; only an unreadable plan, an unreachable
provider or an unwritable path breaks the chain, and under `restartPolicyType: NEVER` that is a
failed tick that waits for tomorrow rather than retrying.

Daily is safe despite the cost: `MINIMUM_CONTINUATION_ADVANCE_DAYS` is 30, so at most one
continuation is authored per 30 days per plan. When one *is* authored it costs a full window
re-walk — measured on `weather-fast` at the 2026-08-09 frontier: **2,779 genuinely new rows against
4,060,119 duplicated ones, roughly 2 GB** (`execution/AGENTS.md` §"The window slides").

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

Root Directory `/` **and** config-as-code path `infra/cron-nasa-power-continue/railway.json` must be
set together; changing one fails, and `RAILWAY_DOCKERFILE_PATH` can never work because the repo-root
`railway.json` overrides it. After a config fix, deploy from source — a plain redeploy replays the
previous failed build snapshot. Env: `LOCAL_SOURCE_LOADER_DATABASE_URL` on the public proxy, with
`DATABASE_URL` absent.
