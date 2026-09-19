# foundation (L0)

Canonical JSON, digests, finiteness guards and credential-custody helpers. Stdlib only.

Import rules: may import nothing first-party except `plantgeo_ml_service.foundation` itself, and no
third-party package at all. The AST contract in `tests/test_layer_import_contract.py` enforces it.

`canonical.py` and `contracts.py` are deliberate COPIES of agri-data-service helpers, not imports:
the two services deploy independently (spec FR-3, section 4). `tests/test_canonical_parity.py` loads
the sibling's module by file path and asserts identical output, so a drift fails this service's sweep
rather than corrupting a checksum silently.

## The object-key grammar (phase 2A)

`parquet_paths.py` and `parquet_markers.py` are the warehouse layout: the first owns the object KEY,
the second owns what is inside the two marker objects. Both are stdlib only, both are copies, and
`tests/test_parquet_parity.py` pins them against agri-data-service's originals.

**Why they sit at L0 at all.** Every later file names an object key, and a key is pure string
arithmetic over a date and a slug. Putting it in `pipeline` would make the one thing both a writer
and a reader must agree on depend on a bucket client.

**Why this service keeps in one module what the sibling spreads over four.** agri-data-service splits
the grammar across `foundation/parquet/paths.py`, `foundation/parquet/zoom.py`,
`pipeline/parquet/objectstore.py` (`availability_lane_root`) and
`pipeline/parquet/availability_documents.py` (the pointer and generation keys), because its writer
owns the availability layout. This service has no such history and one module is under the size
ceiling, so the whole grammar is here and the parity ADAPTER absorbs the difference
(`tests/parity_parquet_adapters.py`). Nothing in the shipped code is shaped by that seam.

**`zoom=` sits ABOVE `year=`.** Polars and DuckDB prune a whole tier by directory before reading a
byte. Zoom is orthogonal to the day, not a second version stamp: the day still says which version
of the data a key holds, and each tier of one day describes that same day at a different resolution.
Zoom is required on every builder; a default would quietly write four tiers into one prefix.

**A day prefix holds four object names, not two.** The part file, the governed-absence marker, the
completion marker, and the derived-empty completion marker. Completion is ASSERTED by an object
written after the last part rather than inferred, because a container replaced mid-write leaves a
prefix of the parts and every one of them looks new. `classify_partition_day` is the single
definition every reader in this service resolves through, so a census and a reader cannot disagree.

**Emptiness has its own name at every rung except the base.** Only a rung DERIVED from a non-empty
base can honestly say "the rows existed and none survived my resolution". A base rung holding no
rows is a governed absence and has its own marker; `classify_partition_day` and
`ObjectStore.write_completion_marker` both enforce that split.

**The availability lane root holds three more names the writer never builds by hand.** The bootstrap
marker (`availability/bootstrap/_BOOTSTRAPPED.json`), the pending retry claim
(`availability/pending/day=<iso>.json`) and the quarantined claim (`...day=<iso>.quarantined.json`)
are all copied here because the sibling's retry sweep enumerates them and this service must write
keys that sweep recognises. The quarantine suffix is deliberately UNPARSEABLE to
`try_parse_availability_retry_path`: the segment between `day=` and `.json` is not an ISO date, so a
sweep walks straight past a parked claim and one unreadable day can no longer starve a lane's
retries. Neither retry parser normalises backslashes, unlike the three partition parsers -- the
sibling's does not either, and a copy that accepted one more shape than the original is a drift.
