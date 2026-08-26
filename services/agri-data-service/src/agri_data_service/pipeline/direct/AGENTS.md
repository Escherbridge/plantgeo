# Source-direct Parquet producers

Modules here fetch upstream products and publish registered Parquet schemas without staging
ingested rows in PostgreSQL. PostgreSQL may still supply the shared session-scoped lane-day
advisory lock during the transition; it is not a data sink for these writers.

## `water_gauges.py`

The NWIS instantaneous-values producer partitions by the publisher-named day: the first ten
characters of `updatedAt`, before converting the timestamp to UTC. Records whose parser had to
substitute the wall clock are not source observations and must be dropped before this transformer.

The nominal base grain is `(site_number, observed_at)`, but four reconciled historical days contain
duplicate physical rows at that grain in both PostgreSQL and Parquet. A completed partition keeps
every such row and its provenance. A repeated source grain refreshes source fields only when it maps
to exactly one existing row; a match to multiple historical rows is ambiguous and fails without
changing or dropping either. Unmatched published rows are retained byte-for-byte and unseen grains
are appended. New direct rows truthfully use `geometry_linked=false`, a null availability time, and
the direct fetch instant as `ingested_at`.

Publication goes through `gap_fill.fill_one_lane_day`: z13 is written and pruned, z9/z5/z0 are
derived and marked, and the z13 completion marker lands last. An object-store failure may therefore
leave z13 incomplete. The same process replays its pre-mutation intended table; a later process
reads every physically present row, adds the current fetch only when every matching grain is
unambiguous, rewrites one complete z13 part, and lets the shared finalizer prune the residue. It
never grain-deduplicates an incomplete partition: that would make an interrupted generation
indistinguishable from the legitimate duplicate source rows already proven by reconciliation.

Every successful tick re-reads z13, proves the complete duplicate-preserving table, and checks
every incoming source field at every incoming grain. Completion-marker status alone is not forward
writer evidence.
