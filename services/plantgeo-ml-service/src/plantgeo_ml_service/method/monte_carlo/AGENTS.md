# method/monte_carlo (L1)

One seeded forecaster per lane: fire detections (hurdle bootstrap), sensors, signal, NDVI
seasonal anomaly and water gauges. Every draw comes from an explicitly seeded numpy PCG64, so
the same artifact plus the same inputs plus the same seed reproduce a byte-identical partition.

Import rules: `foundation` only. `method/ml` is a forbidden sibling, and so are `warehouse`,
`pipeline`, `planes`, `interface` and every storage or web dependency.

A forecaster refuses on insufficient history rather than returning a flat ensemble; the caller
writes a governed absence for that day.
