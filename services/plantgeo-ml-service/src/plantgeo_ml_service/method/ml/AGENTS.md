# method/ml (L1)

Estimator math over already-loaded arrays: Analog Ensemble k-NN, split-conformal calibration,
the ridge wind model, covariates v2, the expert label plane, the recommendation models and the
seasonal candidate/evaluation/lineage family.

Import rules: `foundation` only. `method/monte_carlo` is a forbidden sibling, and so are
`warehouse`, `pipeline`, `planes`, `interface` and every storage or web dependency
(`polars`, `pyarrow`, `duckdb`, `boto3`, `sanic`, `sqlalchemy`).

Rationale for the individual models lives in `../AGENTS.md`.
