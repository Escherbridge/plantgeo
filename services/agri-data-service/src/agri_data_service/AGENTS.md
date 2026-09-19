# Agri data service application contracts

## Production database target

The `receiver_writer` and `published_reader` HTTP profiles target the canonical PostgreSQL
database named `plantgeo`. Railway-generated PostgreSQL URLs use the plain `postgresql` scheme;
`Settings` normalizes that scheme to `postgresql+asyncpg` before validating the target.

Keep the database-name check in the profile resolver. It makes an accidental reference to the
legacy Aevani database named `railway` fail during Sanic startup with an explicit configuration
error instead of surfacing as repeated opaque `/ready` failures. Command-scoped and local DSNs
remain portable and continue to use the shared completeness validator without this production
target restriction.

## ML and Monte Carlo left on 2026-09-18

`method/ml/`, `method/monte_carlo/` and the eighteen Postgres-coupled execution modules that drove
them (`analog_ensemble_*`, `covariate_wind_*`, `conformal_recalibration`, `forecast_receipt_writer`,
`recommendation_*`, `seasonal_*`, `strategy_selection`, `strategy_label_mapping`) were cut out of
this package in one push, together with the unmounted `routes/recommendations.py`, the `ml` CLI
group and the 40 `sql/` files whose only loaders they were. They now live in
`services/plantgeo-ml-service/` under `plantgeo_ml_service.method.{ml,monte_carlo}` and
`plantgeo_ml_service.pipeline` (track `plantgeo_ml_service_20260918`, owner decisions D2 and D6;
git history is the archive). The `method` layer no longer exists here, so the layer-contract rules
that named it are gone rather than retained as dead rules.

What this leaves behind is deliberate: **this service is observed-only.** It reads and publishes
`kind=observed` partitions; the ML service reads them and writes `kind=forecast`. A
`LaneRegistration.forecast_module` stem therefore names a module in the ML service, not a file
under this tree. `execution/vegetation_ndvi_forecast.py` is NOT the module that left: it is a
separate retained copy on the observed NDVI release path that `execution/vegetation_ndvi_plane.py`
and `execution/vegetation_partition_promotion.py` import, and the four `insert_forecast_*` /
`reconcile_forecast_iteration_actuals` SQL files it loads stayed with it. `models/forecasting.py`,
`models/strategy_selection.py` and `scripts/readiness.py`'s `agri.strategy_selection_receipt` probe
also stayed; they belong to a separate `models/` cleanup, not to this cut.
