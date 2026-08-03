# Pending migrations

SQL that is written and reviewed but deliberately **not yet** in `drizzle/`.

A migration only belongs in `drizzle/` once it can be applied, because landing
one there couples three things that must move together:

1. `drizzle/meta/_journal.json` gains an entry,
2. `src/lib/server/db/migration-contract.ts` must be re-pinned to that entry —
   `src/__tests__/security/readiness-migration-contract.test.ts` asserts the
   contract matches the journal's newest entry, and
3. the migration must already be **applied in production**, because
   `/api/ready` requires the pinned row to exist in
   `drizzle.__drizzle_migrations`. Pinning an unapplied migration makes the
   readiness probe 503, which fails the Railway healthcheck and blocks every
   deploy.

`drizzle-kit migrate` applies *all* pending migrations, so it also applies
anything another branch or session left in the tree. Park work here rather
than risk that.

To land one: move the file into `drizzle/` with the next index, run
`drizzle-kit generate`/`migrate` against production, update the contract with
the real hash, then push.

## fire-perimeter-tile-properties.sql

Redefines `geo.fire_risk_tiles` so the `fire_risk` MVT layer carries the
properties the WFIGS ingester actually writes (`incident_name`, `gis_acres`,
`percent_contained`, `severity`, `fire_cause`, `poo_state`, `discovered_at`)
instead of `risk_level`/`name`, which ingested rows never carry. Numeric casts
are regex-guarded so a malformed value nulls that column rather than failing
the tile. Geometry/envelope logic and the WHERE clause are unchanged from
`0001_handy_riptide.sql`, and the MVT layer name stays `fire_risk` because the
client style binds to it.

Until this lands, perimeter hover tooltips show only `severity`;
`src/lib/map/hover-fields.ts` already tolerates the other fields being absent.
