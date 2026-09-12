# Application database schema

`drizzle/0000_baseline.sql` is the complete greenfield definition for the
application-owned `public`, `geo`, and `tracking` schemas. The Drizzle journal
contains exactly that baseline, and `migration-contract.ts` pins its timestamp
and SHA-256 digest for readiness checks.

The `geo.features` and `geo.geometry` tables are reserved for transactional
features such as interventions. Environmental observations and rendered layer
payloads are served from governed Parquet and must not gain PostgreSQL fallback
queries.

For a future relational change, add one forward Drizzle migration and update
the migration contract in the same review. The migration must land with the
application code that requires it.
