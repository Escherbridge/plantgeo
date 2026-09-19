# pipeline (L3)

Reads observed partitions, builds leakage-gated features, runs the estimators and writes
`kind=forecast` partitions with their receipts. `strategy_selection.py` and
`strategy_label_mapping.py` live here because they read and write local artifact bundles.

Import rules: may import `foundation`, `method` and `warehouse`; may NOT import `planes` or
`interface`.

Every read is bounded at the boundary: date range, zoom rung, row limit, horizon count. Every
feature respects its producer's publication lag, because leakage is an algorithmic bug
(`engineering-principles.md` section 3).
