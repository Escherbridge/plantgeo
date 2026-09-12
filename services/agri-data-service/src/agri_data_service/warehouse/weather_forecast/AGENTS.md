# Forecast domain contracts

This L1 package holds immutable domain values shared by forecast publication and
serving. It does not fetch, admit a provider, register a live lane, or advance a
publication pointer. `ForecastRun` describes a fully admitted/published artifact;
timestamps supplied to the schema are assertions which a publisher must reconcile
against immutable provenance receipts. A schema-valid fixture proves no source admission.

## Identity and time

Model initialization determines lead time. Provider release may be unknown and is
never filled from fetch or PlantGeo publication. Fetch, admission, and publication
are separate mandatory instants. Values pin one run, sample coordinate, variable,
valid instant, and optional interval. All times are explicitly UTC; naive times,
numeric epoch coercion, and nonzero timezone offsets are refused. Localization
belongs to presentation. Forecast intervals end at valid time. An available value's
interval cannot precede model initialization. A lead-zero null may retain a
source-defined preceding interval to explain absence; this does not publish a
pre-run accumulation or hindcast.

## Support and missingness

The initial executable shape is deterministic sampled points. A sample represents
its coordinate, never an inferred native cell or interpolated surrounding field.
Even supplied source resolution requires an evidence reference and does not alter
represented support. Native-grid and derived-field claims fail closed until their
separate admission contracts exist. Readers report distance to the actual sample.

Null values require explicit missingness; zero remains a real measurement. Stale
runs, outside-domain requests, exact absence, upstream outage, and not-yet-generated
values remain distinct. `missing` represents a provider missing value. A reader
may report stale run status separately while retaining valid values; it must not
change an available row into a null merely to annotate its run's age.

Canonical units are declared in `variables.py`. Converting provider units and
missing sentinels is the adapter's responsibility, before constructing values.
Precipitation amounts, probabilities, and maximum wind gusts require their period; weather-code numeric
integrality does not establish a provider-specific codebook. Source admission must
bind that codebook. Immutable tuples and frozen models prevent post-validation
mutation; callers must revalidate input rather than bypassing validation with
Pydantic `model_construct` or `model_copy(update=...)`.

## Wind

Direction means where wind comes from, clockwise from north. Components are
`u = -speed * sin(direction)` eastward and `v = -speed * cos(direction)` northward.
Means average components, never degree values. Exact cancellation has zero speed
and no direction. The numerical calm tolerance only suppresses round-off direction
at cancellation; it is not a meteorological calm threshold. Empty and missing wind
inputs are refused, so callers must explicitly govern any partial-data aggregation.
