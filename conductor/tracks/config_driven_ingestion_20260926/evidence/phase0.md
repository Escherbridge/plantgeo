---
type: evidence
track: config_driven_ingestion_20260926
phase: 0
---

# Phase 0 evidence

## GL-1 `o1-logging-core` (pushed `f2a27473`, 2026-09-27 20:20Z)

The owner waived two preconditions with "merge the prs in we want to test things live":
- G0's 24 h observation, which closes 2026-09-28 01:50Z;
- the ~500-line split. The non-test diff is 2,900 lines, pushed as one commit.

Authoring used the isolated worktree `.claude/worktrees/wave-o-gl1` because another session held the shared checkout.

| row | check | result |
|---|---|---|
| sweep | `scripts/check.py --write-receipt` on `14abe742` | format, lint, mypy, pytest (272 s) PASS; receipt `sha256:451f271b` over 976 files |
| deploys | `railway deployment list` for the executor, parquet-api, martin and main | all four `SUCCESS` on `f2a27473` |
| 3 | secret probe over the first ~30 min after the deploy: the executor (48 lines), parquet-api (15) and main (8), for `apikey=`, `api_key=`, `://user:pass@`, `Bearer <token>`, `Authorization:`, `[SQL:` and `password=` | **PASS**: 0 hits. Raw logs are in `.omc/research/wave-o-20260927/smoke/` |
| levels | Railway level column on the executor | lines are `[INFO]` JSON with `event`, `timestamp`, `service` and `deploy`. Lane child output that used to be tagged `[ERRO]` (stderr) now carries a JSON `level` and renders as `[INFO]` |
| warnings | parquet-api | the one `[WARN]` is a Sanic `DeprecationWarning`, routed through `captureWarnings` as a warn-level JSON line (expected) |
| ticks | executor after the deploy | `plantgeo_job_executor_tick_healthy` every 30 s, 18 lanes, `incomplete_lanes=[]`; one lane run published at 20:22Z |

Railway facts (A23) are recorded in `foundation/observability/AGENTS.md`:
- 500 lines per second per replica; lines over that are dropped with a notice;
- levels are fuzzy-matched to debug, info, warn and error, and an unlevelled stderr line becomes error;
- the maximum line size is not documented.

The source is `.omc/research/wave-o-20260927/railway-logging-docs.md`.
