# The forecast lanes (phase 2B)

Why the four `kind=forecast` modules in this directory are shaped the way they are.
`covariate_vectors.py`, `analog_ensemble_daily.py`, `monte_carlo_daily.py` and
`forecast_lane_bootstrap.py` are one vertical slice of spec FR-4, FR-4a and FR-6; the bucket I/O
they sit on is described in `AGENTS.md` beside them, which stays the entry point for
`object_store.py`, `observed_reader.py` and `availability_publisher.py`.

## The shape of one run

```
observed partitions -> covariate vectors / lane history -> forecaster -> rows
    -> base rung + three derived rungs, each with a completion marker
    -> immutable run receipt -> bootstrap receipt + marker (first time only)
    -> availability generation -> compare-and-set on _LATEST.json
```

Nothing before the last arrow makes a day selectable. That ordering is FR-4a stated as control flow,
and it is why `write_and_publish_forecast_rows` is one function rather than four call sites.

## `covariate_vectors.py`

Re-expresses the sibling's deleted `sql/execution/select_covariate_vectors.sql` over Parquet. That
file read one row per `(observed_date, feature_index)` from `agri.covariate_daily_features` and left
the pivot to its caller; the three properties it carried across are the ones reproduced here.

- **The order IS the vector.** `COVARIATE_SIGNAL_ORDER` is the pinned position list and
  `COVARIATE_SCHEMA_VERSION` moves with it. A matrix built under one order and a query vector built
  under another are different features at the same index, and nothing downstream would notice.
- **Partial stays partial.** A day missing any pinned signal is DROPPED and counted in
  `dropped_day_count`, never imputed. A mean over the survivors is a vector that reads as measured
  and was not; the SQL function it replaces returned `NULL` with `input_count <
  expected_input_count` for exactly this reason.
- **Standardization moments are recorded, not recomputed.** `feature_means` and
  `feature_standard_deviations` ride on the matrix so a later query vector is standardized the same
  way the history was. A feature whose spread is at or below `MIN_FEATURE_STANDARD_DEVIATION` is
  scaled by `1.0` and named in `zero_variance_features`: dividing by float noise would turn a
  constant column into the dominant distance term.
- **`cell_id` and the cell coordinates are propagated verbatim** and never merged; a per-cell frame
  that carries two identities is refused rather than reduced.

The upper bound of a covariate window is `settled_through(signal, issued_on)`, never `issued_on` and
never today. `refuse_unsettled_rows` re-checks it on rows assembled any other way, so the leakage
guard holds for a caller that did not come through `read_covariate_window`.

## `analog_ensemble_daily.py`

`method/ml/analog_ensemble.py` is used UNCHANGED. Its `find_analogs` already carries the structural
horizon guard (an analog's whole successor path must lie before the query day) with the published
Delle Monache et al. 2013 exclusion window applied on top, so this module adds no leakage rule of
its own; it only supplies history, a query vector and a target series.

**A zero-analog step is refused, not published.** `generate_anen_forecast` falls back to a flat
persistence value when no analog had a successor at a step. Writing that as p10/p50/p90 would
publish three identical numbers as if they were an ensemble spread. Those steps are dropped and, if
a whole series produces none, the series becomes a `no_analog_successors` refusal.

**What the observed columns mean on a forecast row.** Section 3 of `layer-lanes.md` forbids a
forecast inheriting an observation's provenance, and the schema is `forecast_schema_for(observed)`,
so every observed column must still carry something honest:

| column | forecast value | why |
|---|---|---|
| `observation_count` | `0` | a forecast row rests on zero observations; the analog count would read as measurement support |
| `newest_observed_at` | the series' own maximum | a propagated OBSERVED fact about the history the run was issued from |
| `coverage_fraction` | `NULL` | nothing was covered; the column is nullable and says so |
| `allowed_client_exposure` | exposed only if every source row was | the same `all` gate the coarse rungs use |
| `cell_id`, coordinates, `support_key`, `signal_name`, `normalized_unit` | verbatim | the grain is identical or the lane has drifted |
| `observation_checksum` (vegetation) | the forecast row's OWN fingerprint | the column is non-null at that lane's base rung, and an inherited checksum would be borrowed lineage |

`issued_on` is the ANCHOR day the projection was made from (the newest complete covariate day), not
the run date. `ensemble_size` is the analog count of that step, so a p50 from five analogs is not
reported as a p50 from twenty.

`forecast_run_id` digests the artifact-or-hyperparameter sha, the issue day and the seed. The seed is
recorded on every row even though the analog search consumes no draws: the column is the lane's
contract, and a reader comparing an AnEn row with a Monte Carlo row must find the same six columns.

## `monte_carlo_daily.py`

**`MONTE_CARLO_MODULES` is written out.** A stem is looked up in that dict and nowhere else; no
module name is assembled from a lane's data at call time. The map holds all five stems
`layer-lanes.md` names, including `sensors` and `water_gauges`, so the set of modules this service
can execute is one literal.

**`STEM_LAYERS` is smaller than the map, deliberately.** `sensors` and `water_gauges` have no lane
contract and no pinned observed schema in this service yet. A lane must be bound to a stream before
anything writes it, so those two refuse with a named error rather than having a stream invented for
them. Closing that gap is a registration task, not a change here.

**One adapter per lane, because the five forecasters do not share an interface.** They predate this
contract and each takes its own history type. The adapters translate Parquet rows into that type and
translate the result back into `forecast_schema_for(observed)`; the forecasters themselves are
untouched. Two lanes (`signal`, `fire-detections`) export no per-row checksum, so `_row_fingerprint`
digests the row's own exported columns for the forecaster that requires one. It is an input to the
method, never a published column.

**Each module's refusal is carried, not caught and replaced.** An
`InsufficientFireHistoryError`/`InsufficientSignalHistoryError`/`InsufficientNdviHistoryError` becomes
one `ForecastRefusal` naming the cell, the series and the module's own message; the lane then writes
no row for it. A run that refuses everything publishes nothing and returns a receipt saying so.

**The signal lane has two forecasters.** When `ml/artifacts/analog_ensemble/<sha>.json` exists the
lane runs AnEn; otherwise it runs `method/monte_carlo/signal.py`. The receipt's `forecaster` field
states which, so a row's method is never inferred from its shape.

## `forecast_lane_bootstrap.py`

**The ladder is written, never indexed on faith.** `write_forecast_day` writes all four rungs of
`ZOOM_TIERS` from the base rows and gives each its own completion marker. The coarse rungs are
derived by `floor_to_resolution`, a copy of the sibling's `warehouse/parquet/tiers.py` arithmetic
including the snap: Polars evaluates `value / pitch` by different paths depending on frame length
and host, so an edge coordinate is pulled onto the nearby integer BEFORE flooring. Without it a
lattice origin bins into one cell on a one-row frame and another in bulk.

**The forecast derivation is the observed one plus the provenance grain.** `FORECAST_TIER_DERIVATIONS`
restates each lane's registered `TierDerivation` with the six provenance columns added to
`key_columns`, so no coarse cell ever merges two runs, two horizons or two quantiles into one row.
Columns declared `null` (a `cell_id`, an `observation_checksum`) are nulled AFTER the group at their
declared dtype: no merged row can honestly name an identifier unique to one base cell.

**The source ceiling comes from the provider frontier.** `forecast_source_ceiling` is
`settled_through(lane, issued_on) + horizon_days`. Never `date.today()`: a ceiling taken from the
wall clock advances on its own, so a lane would read as fresh because a run was recent rather than
because its source moved. It also satisfies the availability contract's own rule that a row's day
may not exceed its ceiling, which a bare frontier would violate for every forecast day.

**A forecast lane's index is issue-scoped.** An observed lane accumulates days forever; a forecast
lane's newest run supersedes the last one, so the generation published here holds THIS issue's days.
That is a property of the product, not a shortcut: yesterday's horizon-30 row for a day today
forecasts at horizon-29 is a worse answer, and leaving both in one index would offer a reader two.

**The two bootstrap documents are byte-compatible copies.** `parquet_ops/availability_coverage.py`
reads `_BOOTSTRAPPED.json` with ONE GET to decide whether a lane has any availability history, and
re-serializes it before comparing. `bootstrap_marker_payload` and `bootstrap_receipt_payload`
therefore render field for field as `pipeline/parquet/availability_documents.py` and
`availability_requests.py` do, including `availability_provenance_summary`, which is copied here only
because this service's `warehouse/availability.py` does not yet carry it. Both objects go up through
`put_immutable`, so a replay adopts its own bytes and a different bootstrap is refused rather than
beginning a second history for one lane.
