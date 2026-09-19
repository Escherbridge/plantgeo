# plantgeo-ml-service

PlantGeo's machine-learning and Monte Carlo lane, split out of `agri-data-service` so that training
and forecasting deploy on their own schedule. It reads governed observed Parquet from the warehouse
bucket, writes `kind=forecast` partitions and its own artifacts back to that same bucket under an
`ml/` prefix, and serves both over a small HTTP surface at `/api/v1/ml`. It holds the estimator math
(Analog Ensemble spatial k-NN, split-conformal calibration, a ridge covariate model, the expert
label plane and the recommendation models) and the five seeded Monte Carlo forecasters.

The service has no database. That is a decision, not an omission: features, labels, model artifacts,
receipts and predictions all live in the object store as canonical JSON or Parquet, and the settings
object refuses to start the process while any `DATABASE_URL`-shaped variable is in its environment.
The code is arranged as a six-layer lattice -- `foundation`, `method`, `warehouse`, `pipeline`,
`planes`, `interface` -- enforced by an AST import test rather than by convention, so a numeric
routine cannot quietly acquire a storage client. Knowledge the service shares with
`agri-data-service` is copied rather than imported, and each copy is pinned by a parity test that
fails the day the two drift.

Run the checks with `uv run --no-sync python scripts/check.py` from this directory, and the CLI with
`uv run --no-sync plantgeo-ml --help`. `RUNBOOK.md` carries the outstanding work, the environment
notes and the continuation plan; `AGENTS.md` carries the layer rules and the tripwires; every
directory has its own `AGENTS.md` with the rationale for what lives there.
