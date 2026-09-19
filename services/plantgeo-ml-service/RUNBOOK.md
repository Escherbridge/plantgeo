---
type: runbook
status: active
updated_on: 2026-09-18
---

# plantgeo-ml-service RUNBOOK

## Directive

Ship a Parquet-fed machine-learning service that trains and forecasts without a database, proves
every numeric claim against a golden reference, and never promotes a model without an owner go.
Training against production data is out of scope for the whole track: it ships code and plans, and a
later owner-triggered run does the training. Nothing here promotes a model.

The service is complete without Mojo. `PLANTGEO_ML_KERNELS=python` is the default and the rollback;
`mojo` is an optimisation behind a parity harness, never a dependency.

## Outstanding work

| phase | ships | state | blocked on |
|---|---|---|---|
| 1 | service skeleton, hard cut of ML and Monte Carlo out of agri-data-service | skeleton written, sweep owed | monitor sweep (1C), then `/code-review high` and one push |
| 2 | Parquet readers, forecast writer, fire-risk and spatial-KNN daily lanes, artifacts, API, cron | not started | phase 1 push; owner go before any real bucket write |
| 3 | Mojo kernels behind parity harnesses, pixi project, Docker Mojo stage | not started | phase 2; WSL2 + pixi on the dev box |
| 4 | platform wiring: agent forecast tool, slider forecast days, web env, fire-risk track activation | not started | phase 2 only; Mojo (phase 3) is an optimisation and never gates it (critic finding 11, 2026-09-18) |

Immediately owed, in order:

1. ~~`QUALITY_RECEIPT.json` records every check as `pending`.~~ **Done 2026-09-18 (phase 1 fix
   batch).** The receipt was written by `uv run --no-sync python scripts/check.py --write-receipt`
   over the staged tree after a green sweep, and `scripts/verify_quality_receipt.py` accepts it, so
   the image build no longer refuses. It is now a standing obligation, not a one-off: any change
   under `services/plantgeo-ml-service/` invalidates the digest, so re-run the sweep, `git add` the
   service path, re-write the receipt, and `git add` it. Never hand-edit it.
2. Create the `plantgeo-ml` Railway service in the Aevani project and set its config-as-code root to
   `services/plantgeo-ml-service`. Owner action; the push is not blocked on it, the `/ready` proof is.
3. Set `OBJECT_STORE_ENDPOINT_URL`, `OBJECT_STORE_BUCKET`, `OBJECT_STORE_ACCESS_KEY_ID` and
   `OBJECT_STORE_SECRET_ACCESS_KEY` on that service through Railway reference variables. Until then
   `/ready` answers 503 with `reason: object_store_unconfigured`, which is correct, not a fault.
4. Do NOT set any `*DATABASE_URL*` variable on it. The process refuses to boot with one present.

## Environment

**Today (phase 1 and 2):** Python 3.12 and `uv`. Nothing else. From the service root:

```
uv sync --extra dev
uv run --no-sync python scripts/check.py        # the four gates, run once at the end of a pass
uv run --no-sync python scripts/check.py --write-receipt   # only on a committed, green tree
uv run --no-sync plantgeo-ml --help
```

Authors do not run the suite (owner rule 2026-08-25): an implementation pass predicts what will
fail and a monitor sweeps the combined tree. In a multi-fix pass, apply every fix first and sweep
once.

**From phase 3:** WSL2 plus `pixi` for the Mojo 1.0 toolchain, and a Mojo build stage in the
Dockerfile. Windows has no native Mojo, so Python tests on Windows run against the pure-Python
reference of every kernel and the parity harness runs in WSL2 and inside the Docker build. A skipped
parity test names its reason; it never passes silently.

## Health and readiness

- `GET /health` is liveness only and answers 200 unconditionally.
- `GET /ready` answers 200 when the bucket credentials resolve AND a bounded `head_bucket` (5 s)
  succeeds. Otherwise 503 with a typed reason: `object_store_unconfigured`, `object_store_timeout`
  or `object_store_unreachable`. Railway's healthcheck points at `/ready`.

## Continuation plan

Next session picks up at phase 1C: read
`conductor/tracks/plantgeo_ml_service_20260918/evidence/phase1-predictions.md` first, which lists
every file this slice created, every file it copied from (the p1b delete list), and the checks its
author predicted would fail. Then run the sweep on the combined tree, report every failure rather
than the first, batch the fixes, sweep once more, and take the two review verdicts into
`metadata.json.reviews.phase1` before pushing.

Phase 2 starts at `plan.md` section 2A: `foundation/parquet_paths.py` with its cross-service parity
test, because every later file names an object key.
