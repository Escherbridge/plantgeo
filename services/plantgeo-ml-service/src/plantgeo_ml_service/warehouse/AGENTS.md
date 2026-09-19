# warehouse (L2)

The pinned schemas of the Parquet streams this service consumes and writes, the availability
documents it publishes, and the clock each lane keys to.

Import rules: may import `foundation` and `method`; may NOT import `pipeline`, `planes` or
`interface`.

Every contract here is a COPY of agri-data-service's, held honest by a parity test rather than an
import (spec section 4). `tests/test_parquet_parity.py` asserts both directions.

## `streams.py` -- one Arrow schema, grain sort key and codec per stream

Six observed streams are copied verbatim: `signal`, `fire-detections`, `vegetation`, `drought`,
`burn-severity`, `weather-observations`. A seventh, `fire-risk`, is ORIGINATED here (spec FR-5) and
has no sibling yet; `p2d-fire-risk-registration` lands the matching `warehouse/schemas/fire_risk.py`
on the agri side later, and this schema is what that one must agree with.

**`forecast_schema_for()` is derived, never registered.** A lane declares its OBSERVED columns and
nothing else; the forecast side is those columns, in order, plus the six provenance fields. Carrying
provenance nullable on the observed side would make six unconditionally-NULL columns, and a column
that is unconditionally NULL is not provenance, it is a placeholder. The three provenance grain
columns (`issued_on`, `horizon_days`, `quantile`) finish the sort key, because a forecast partition
holds many rows per cell-day and an ordering that leaves ties is not reproducible evidence.

**`base_non_null_columns` rides on the schema here, not on a tier derivation.** The sibling keeps it
on `warehouse/parquet/tiers.TierDerivation` because that module also performs the coarsening. This
service never derives a rung; it only has to refuse a base-rung write that nulls `cell_id` or
`observation_checksum`. The parity fixture compares this field against the sibling's
`base_non_null_columns(stream)` so the two cannot drift apart.

**`fire-risk` nulls a refused score rather than writing zero.** `probability` and `risk_score` are
nullable and `refused_reason` carries the why. A fabricated zero reads as "no risk here", which is
exactly the false claim the FR-5 publication gate exists to prevent. `stratum` and
`model_artifact_sha256` are non-null on every row, so a scored cell and a refused cell are equally
attributable.

## `lanes.py` -- the clock, not the adapter

Only the CONTRACT half of the sibling's `pipeline/parquet/lane_registry.py` crosses: slug, history
floor, publication lag, cadence, nature and forecaster stem. The adapters and watermark resolvers do
not, because this service exports nothing.

`settled_through(as_of)` is the whole leakage guard in one line: a feature issued on `as_of` may read
through `as_of - publication_lag_days` and no later, whatever the bucket happens to hold. A partition
newer than that exists only because some process ran ahead of the lane's own clock, and reading it
would make a backtest score better than the live lane ever can.

## `availability.py` -- the documents, not the publisher

The generation Parquet schema, its seventeen `availability.*` metadata keys, the terminal row, the
pointer, and the content-address arithmetic that binds a pointer to exactly one generation. The
publisher that WRITES them is `pipeline/availability_publisher.py`; the split keeps an L2 document
free of a bucket client.

**The required rungs are the whole ladder.** A day is selectable only when every rung of it reached
one terminal state, from one source receipt, with one absence reason. A publisher that indexed three
of four rungs would make a day selectable at a resolution it never wrote.

**Provenance is derived from a row's SHAPE, never declared in a column.** The index schema is frozen
at version 1, so a provenance column would not survive the round trip the sibling re-reads and
compares. Published, holding rows, and naming no part is the manifest-trusted shape; everything else
is digested.
