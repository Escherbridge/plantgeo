# cron-era5-land-coverage-status — coverage report for the Open-Meteo ERA5-Land signal lane

**Cron note.** Minute `:55` is shared only with `cron-mtbs` (`55 7 * * 2`), which fires Tuesdays at
07:55 — this service runs `55 22 * * *`, so the two never land in the same minute, and 22:55 UTC sits
clear of the 05:00 NDVI tick, the 06:00 stream validator, the Thursday 14:00 drought tick and the
09:55–11:55 NASA POWER band. It runs an hour after `cron-era5-land-coverage-fill` (21:55) so the
report reads the record the fill just left, not the one before it.

## Not live

**This directory is inert.** No Railway service is provisioned against it.

## What it would run

`agri-cli coverage-status --source-key open-meteo-era5-land-archive` per SKILL.md §8 — the report,
and the only liveness signal this lane family has. It must state completeness, the missing-day count
and the collapsed ranges (`contiguous_ranges` in `execution/coverage_contract.py:239`).

Read the four `DayState` buckets as distinct: `covered`, `thin` (landed under the lane's cell floor —
refillable, not a hole), `absent` (upstream published nothing; evidence, never refetched), `missing`
(the only state the filler acts on). `completeness_fraction` counts absences as satisfied and thin
days as not, which is what stops a lane whose provider never published a given day from sitting at
99% forever with a work list that can never empty.

This contract's horizon opens at 2022-04-30 — earlier than the NASA lane's 2022-08-06 — so the two
families' reports are not comparable day-for-day, and a lower percentage here is not automatically a
worse lane.

## Nothing consumes this yet

`sql/routes/ops_data_streams.sql` already computes `missing_days` and `largest_gap_days` and no
surface reads them. Adding this cron does not change that: a report nobody reads is not a liveness
signal, so wiring the output into `/ops/backfill` is the follow-up that makes this service worth
provisioning.

## Verified against the CLI

Spellings confirmed against `agri-cli coverage-status --help` on 2026-08-11: `--source-key`
(**repeatable**; the default is every declared contract), `--through YYYY-MM-DD` and `--json`. The
selector is `contracts_for_keys` in `execution/coverage_census.py`, which **raises** on a key no
contract declares — a typo is a loud fault, not a silent empty report. It supersedes the single-key
`contracts_for_source` this file previously named.

Exit code is always 0 for a finding. An incomplete lane is a measurement, not an incident, so a red
tick here means the measurement itself failed (unreachable warehouse, undeclared source key), never
that the lane has holes. Read the output, not the tick.

## One number to read first

`contracted through` — the census holds each lane to today minus that provider's **measured**
publication lag (NASA POWER 5 days, Open-Meteo ERA5-Land 9, measured 2026-08-11), never to today.
Running to today reports the provider's release schedule as a hole. `through_day_basis` states which
of the two produced the numbers.

## Provisioning

Root Directory `/` **and** config-as-code path `infra/cron-era5-land-coverage-status/railway.json`
must be set together; changing one fails, and `RAILWAY_DOCKERFILE_PATH` can never work. After a
config fix, deploy from source. Env: `LOCAL_SOURCE_LOADER_DATABASE_URL` on the public proxy, with
`DATABASE_URL` absent.
