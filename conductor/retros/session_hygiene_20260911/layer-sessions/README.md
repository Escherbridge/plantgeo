---
type: reference
---

# Per-layer session briefs

One self-contained brief per Parquet lane. **Paste a whole file as the first message of a fresh
session**; each repeats its shared context deliberately so no brief depends on another being read.

Every number was measured against the production bucket on **2026-08-25** by
`agri-service data parquet-drain --dry-run` (both selections). The bucket moves — each brief tells its
session to reproduce the census before acting, and to report anything that contradicts the brief.

## Why one session per lane is safe

Every write goes through `fill_one_lane_day`, which takes a Postgres advisory lock keyed on
(lane, day). Two sessions on **different** lanes can never contend, so the lane is the natural
partition. Two sessions on the **same** lane are the hazard.

The shared hazard is not corruption but **disk contention**: `--selection missing` is
source-connected, and several of those running at once — or one running beside the armed hourly
ingest cron — reproduces the fe9b241 collision, where a `signal` lane-day measured **~8 s alone vs
~25 minutes beside a cron tick**, both on `IO/DataFileRead`. `--selection ladder` is exempt; it
takes the lock but reads zero source rows.

**So: run ladder work in parallel freely. Serialise `--selection missing` work.**

## State at generation, 2026-08-25

The ladder repair closed 585 of 1,037 incomplete-ladder days that morning (0 failures). What
remains:

| lane | base | ladder gap | missing | unfinished | headline |
|---|---:|---:|---:|---:|---|
| [signal](signal.md) | 1560 | **222** | 1 | 0 | blocked — base lacks coordinate columns |
| [vegetation](vegetation.md) | 1195 | **205** | 1 | 0 | blocked — same fault |
| [sensors](sensors.md) | 26 | **25** | 1 | 0 | blocked, **and losing history after 2026-08-31** |
| [fire-perimeters](fire-perimeters.md) | 45 | 0 | **62** | 1 | largest export backlog |
| [burn-severity](burn-severity.md) | 4 | 0 | 2 | 1 | 2,089 absences are CORRECT |
| [water-gauges](water-gauges.md) | 91 | 0 | 2 | 0 | ladder complete |
| [weather-observations](weather-observations.md) | 20 | 0 | 2 | 0 | **has no contract at all** |
| [fire-detections](fire-detections.md) | 8357 | 0 | 1 | 0 | deepest window, ~9,400 days |
| [drought](drought.md) | 209 | 0 | 0 | 0 | warehouse complete |
| [soil-survey](soil-survey.md) | **0** | 0 | 0 | 1 | empty; own design track |
| [calendar](calendar.md) | 1 | 0 | 0 | 0 | static; coverage unread |
| [watersheds](watersheds.md) | 1 | 0 | 0 | 0 | static; coverage unread |
| [evacuation-zones](evacuation-zones.md) | 1 | 0 | 0 | 0 | static; coverage unread |

**Three gap classes, and they are not interchangeable:**

| class | total | remedy | source-connected? |
|---|---:|---|---|
| incomplete ladder | 452 | retract + re-export (schema mismatch) | yes |
| missing base | 72 | `--selection missing` | yes |
| governed absence at z13 only | 3,740 | absence propagation to coarse rungs | **no — admin decision** |

The 3,740 are **not** missing data. They are days upstream legitimately cannot serve, correctly
recorded. Minting coarse-rung absences from a repair sweep is deliberately an admin decision, not
drain work — see `pipeline/parquet/drain.py:256-258`.

## Regenerating

These are generated from a measured fact table, not hand-written, so the numbers cannot drift
between briefs. Re-run both censuses, update the fact table, regenerate. The generator lives with
the session that produced it; if it is gone, the briefs are still authoritative as a snapshot —
just re-measure before trusting any number.
