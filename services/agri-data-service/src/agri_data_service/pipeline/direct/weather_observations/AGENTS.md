# Weather Observations direct writer

This package owns the independently scheduled Open-Meteo current-conditions producer. `source.py`
polls the rolling grid, `rows.py` conforms observations, `adapter.py` performs the durable daily
merge, and `forward.py` owns the turn, parser, and `WRITER_CONTRACT`. `recovery.py` is a deliberate
rolling-source addition: it replays retained provider evidence that a later poll can no longer
reconstruct. `support.py` contains shared turn mechanics and `__main__.py` is the supported module
entrypoint.

`--max-days` bounds the at-most-two buckets from one poll, not an archive walk. Ordinary boundary
failures should use the shared pipeline operational error; specialized errors require a tested
semantic branch that changes recovery or publication behavior.

## The current-conditions fetch is keyed, retried and budgeted (2026-10-03)
`source.py` resolves every point's send through `ingest/provider_client.py::FORECAST_ENDPOINT` +
`ingest/open_meteo_endpoint.py::open_meteo_product_request`, NOT `ingest/open_meteo.py::current_weather_url`
directly: when `OPEN_METEO_API_KEY` is set, the poll goes to `OPEN_METEO_FORECAST_CUSTOMER_BASE_URL`
(paid, metered to the `open-meteo-paid` pool by `foundation/observability/usage.py`'s host rules);
unset, it is byte-identical to the legacy keyless URL (`_current_weather_parameters` holds the exact
query shape and key order, deliberately without `cell_selection`). `recovery.py` still builds
checkpoint identities from `current_weather_url` directly and is unaffected either way.

Every point's fetch is wrapped in `ingest/upstream_retry.py::retry_upstream`
(`WEATHER_CURRENT_RETRY_POLICY`), so a 429 or 5xx now gets a bounded, Retry-After-honouring retry
instead of failing the point on the first try. `WeatherPollRequestBudget` bounds the TOTAL retries
one poll may spend across every sample point (default `WEATHER_POLL_RETRY_BUDGET_DEFAULT` = 150, one
per point on average) so a provider-wide throttle cannot multiply into an unbounded number of sends;
a point's own first attempt is never charged against it. See `source.py`'s module docstring for the
full reasoning and `tests/direct/test_weather_observations_source.py` for the flow tests (host
switch, key redaction, Retry-After clamp, budget exhaustion) driven through a real `httpx.MockTransport`.
