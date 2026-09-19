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
| 2A | Parquet path grammar, stream schemas, lane clocks, object store, DuckDB session, observed reader, availability publisher, expert label reader | written, sweep owed | monitor sweep, then `/code-review high` and one push |
| 2B | fire-risk and spatial-KNN daily lanes, Monte Carlo dispatch | landed `bc08eca9` | sweep, review, push |
| 2C | the four FR-8 routes, `predict-daily`, the cron image | written, sweep owed | monitor sweep, then `/code-review high` + `/security-review` on the query parsing |
| 3 | Mojo kernels behind parity harnesses, pixi project, Docker Mojo stage | not started | phase 2; WSL2 + pixi on the dev box |
| 4 | platform wiring: agent forecast tool, slider forecast days, web env, fire-risk track activation | not started | phase 2 only; Mojo (phase 3) is an optimisation and never gates it (critic finding 11, 2026-09-18) |

Immediately owed, in order:

1. ~~`QUALITY_RECEIPT.json` records every check as `pending`.~~ **Done 2026-09-18 (phase 1 fix
   batch).** The receipt was written by `uv run --no-sync python scripts/check.py --write-receipt`
   over the staged tree after a green sweep, and `scripts/verify_quality_receipt.py` accepts it, so
   the image build no longer refuses. It is now a standing obligation, not a one-off: any change
   under `services/plantgeo-ml-service/` invalidates the digest, so re-run the sweep, `git add` the
   service path, re-write the receipt, and `git add` it. Never hand-edit it.
2. ~~Create the `plantgeo-ml` Railway service.~~ **Done 2026-09-19.** Service `plantgeo-ml` (id
   `c66ce1ba`) exists in Aevani with root directory `/services/plantgeo-ml-service`, Dockerfile builder,
   `/ready` healthcheck, watch pattern on this directory only, domain
   `plantgeo-ml-production.up.railway.app`. Railway rejects a `railway.json` config-as-code path as
   deprecated, so these settings live on the service. `PORT=8000` is pinned because the generated
   domain targets 8000 while Railway injects 8080; without it the healthcheck passed and the domain 502'd.
3. ~~Set the `OBJECT_STORE_*` variables.~~ **Done 2026-09-19** as reference variables from
   `plantgeo-parquet-api` (plus `OBJECT_STORE_REGION`, `CORS_ORIGINS`, `PLANTGEO_ML_KERNELS=python`).
   Live proof 12:01Z: `/health` 200, `/ready` 200, `/api/v1/ml/artifacts` 501 by design. First deploy
   `c125e2c2` died with a sanic-ext `NameError` (TYPE_CHECKING-only `Request` import); fixed in
   `b1f02f95` with `tests/test_route_annotations.py`.
4. Do NOT set any `*DATABASE_URL*` variable on it. The process refuses to boot with one present.
5. ~~Install the DuckDB extensions into the SERVICE image.~~ **Done 2026-09-19 (phase 2C fix
   batch).** Both `Dockerfile` and `infra/cron/Dockerfile` now call
   `pipeline.duckdb_session.install_extensions` into `/opt/duckdb-extensions`, `chown` it to the
   `plantgeo` service user, and then run a build-time `open_guarded_connection()` probe AS that
   user. The probe is the part that matters on a redeploy: a missing or unreadable extension
   directory now fails the build rather than turning every `/api/v1/ml` partition read into a
   `serving_fault` on the deployed service. Next deploy of `plantgeo-ml` picks this up; verify with
   one real `/api/v1/ml/fire-risk` read after it lands.
6. **Create `plantgeo-ml-cron`, and do not arm it.** `railway.cron.json` documents the settings the
   coordinator applies by hand (Railway rejects a config-as-code path as deprecated): root directory
   `/services/plantgeo-ml-service`, Dockerfile `infra/cron/Dockerfile`, cron `30 6 * * *` UTC,
   restart `ON_FAILURE`, no domain and no healthcheck, the same `OBJECT_STORE_*` reference variables
   as `plantgeo-ml`, and `PLANTGEO_ML_KERNELS=python`. The service is created by the coordinator and
   the schedule is NEVER armed without an explicit owner go (spec FR-10): the first armed turn
   writes to the published lanes.
7. **Prove FR-4a before arming anything.** `plantgeo-ml predict-daily --dry-run-prefix
   ml/scratch/<date>/` writes a whole turn under the scratch root, and the acceptance is reading
   that day back through agri-data-service's `parquet_ops/availability_coverage.py`, never a raw
   listing. Record the listing in `evidence/phase2-dry-run.json`.

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

## What phase 2A landed, and what it still owes

Landed under `src/plantgeo_ml_service/`: `foundation/parquet_paths.py`,
`foundation/parquet_markers.py`, `warehouse/streams.py`, `warehouse/lanes.py`,
`warehouse/availability.py`, `pipeline/object_store.py`, `pipeline/duckdb_session.py`,
`pipeline/observed_reader.py`, `pipeline/availability_publisher.py`, `pipeline/expert_labels.py`.
Five new parity fixtures plus one this service alone produces; `scripts/regenerate_parity_fixtures.py`
is still the ONLY way any fixture changes.

Still owed, and deliberately not in 2A:

1. **The agri-side export verb.** `agri-service ops export-expert-labels --release <id> --prefix
   ml/labels/expert` does not exist yet. `pipeline/expert_labels.py` reads what it will write and
   refuses by naming it. Another session owns `services/agri-data-service`; sequence the verb after
   that session's current push. Running it on production is a mutation and needs an owner go.
2. **`fire-risk` lane registration on the agri side** (`p2d-fire-risk-registration`): a
   `LaneRegistration`, `warehouse/schemas/fire_risk.py` and `docs/lanes/fire-risk.md`. This service's
   `FIRE_RISK_SCHEMA` is what that one must agree with, and until it lands `fire-risk` has no lane
   contract and `warehouse/lanes.py` does not name it.
3. **The daily lanes and the API** (2B): `pipeline/fire_risk_*.py`, `pipeline/analog_ensemble_daily.py`,
   `pipeline/monte_carlo_daily.py`, `planes/`, the four FR-8 routes, and `predict-daily`.
4. **The DuckDB extension directory.** `PLANTGEO_ML_DUCKDB_EXTENSION_DIRECTORY` defaults to
   `/opt/duckdb-extensions` and nothing installs into it yet. The Dockerfile must call
   `pipeline.duckdb_session.install_extensions` at build time, or every read refuses with
   `DuckDbExtensionError` -- which is the correct failure, not a fault.
5. **No real bucket write has happened.** Everything is proven against
   `InMemoryObjectStoreBackend`. The FR-4a acceptance -- a dry run to `ml/scratch/<date>/` read back
   through agri-data-service's `parquet_ops/availability_coverage.py` -- is 2B's first task.

## Continuation plan

Next session picks up at phase 1C: read
`conductor/tracks/plantgeo_ml_service_20260918/evidence/phase1-predictions.md` first, which lists
every file this slice created, every file it copied from (the p1b delete list), and the checks its
author predicted would fail. Then run the sweep on the combined tree, report every failure rather
than the first, batch the fixes, sweep once more, and take the two review verdicts into
`metadata.json.reviews.phase1` before pushing.

Phase 2A is written. Next session reads
`conductor/tracks/plantgeo_ml_service_20260918/evidence/phase2a-predictions.md` first, sweeps the
combined tree once, batches the fixes, sweeps again, and takes the review verdict into
`metadata.json.reviews.phase2a` before pushing. Then 2B starts at `plan.md` section 2B with
`pipeline/fire_risk_features.py`, whose every feature reads through
`ObservedReader.read_lane_window(..., as_of=issued_on)` rather than listing the bucket itself.
