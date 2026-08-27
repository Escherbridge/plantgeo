# Vegetation production proof scripts

`vegetation_ingest_status.py` reads the raw `geo.features` vegetation population in a read-only
transaction. Its changed-cell-day counts collapse raw scene rows to the same `(cellKey,
publisher-named day)` grain the promotion transform uses, then test presence against the governed
NDVI source filters. Empty populations keep nullable bounds rather than turning an operational
absence into an exception.

`vegetation_source_inventory.py` brackets the object-store scan with independent read-only governed
source censuses. A changing source makes the report non-clean. Schema classification is deliberately
closed: only the exact current schema and the pinned coordinate-less predecessor have names;
anything else is `unknown`, and unknown/read-error state exits nonzero so its day cannot become a
destructive rewrite manifest by implication.

`audit_weather_observations_exact.py` is the credential-free current-weather completion proof. Run
it from this service directory; it starts PostgreSQL in read-only mode, excludes every object day
before the weather registry floor, and compares the governed settled window through all four zoom
tiers. A non-clean report exits nonzero and never repairs, marks, prunes, or writes anything.
