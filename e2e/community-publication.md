# Local community publication acceptance

This harness creates a uniquely named `plantgeo_community_test_<uuid>` database
on an explicitly supplied loopback PostgreSQL server. It never reads the
application's `DATABASE_URL`, and refuses remote hosts and admin URLs targeting
anything except `/postgres`. It drops only the database that it just created.
The synthetic contributor, expert, viewer, workspace, and feature records belong
only to that disposable database.

The database fixture extracts the community/auth table definitions,
`geo.sync_feature_geom_from_properties`, `geo_features_sync_geom`, and
`geo.intervention_tiles` directly from the checked-in baseline migration. It
adds the keys needed by this bounded schema. This is a real PostGIS integration
fixture, not a greenfield replay of the complete application/Alembic migration
chain. The service test injects a real Drizzle database into the production tRPC
callers; only ambient session loading and the unused global database import are
stubbed. Browser tests use real credential sign-in, HTTP tRPC procedures, and
the same PostGIS tile function.

The browser adapter serves tile HTTP responses on loopback port 3308. It is a
small local HTTP transport for the actual tile SQL, not the Martin executable.
Next runs on 3307 with a local database and explicit local service endpoints;
the child environment does not inherit application secrets. Basemap imagery and
unrelated environmental widgets use the existing empty fixtures. Community,
contribution, team, and auth requests remain real. Service workers remain enabled.

The publication test opens the reviewer in another tab in the same browser
profile. Its storage notification must refresh the already mounted map. The
test checks empty cached bytes before publication, nonempty revision tile bytes
after publication, and the named rendered feature's hover content without
moving, reloading, or manually refreshing that viewport. Re-authentication and a
reload happen afterward to check the contributor's own persisted outcome under
the contributor session. This does not establish cross-device push delivery.

Use a dedicated test cluster; the environment variable is intentionally required:

```powershell
$env:COMMUNITY_TEST_ADMIN_URL='postgres://community_test@127.0.0.1:55439/postgres'
npx vitest run src/__tests__/services/community-publication-postgis.test.ts --configLoader runner --maxWorkers=2
npx playwright test --config e2e/community-publication.config.ts
```

Ordinary Vitest and Playwright runs skip these external-service tests when their
explicit opt-in is absent. Include the PostGIS test in the final integrated
changed-surface sweep rather than running it after each edit. The browser config
starts and tears down its own Next and tile-adapter processes and its database.
The separately managed database cluster is left running for the owning task to
stop after acceptance. Screenshots and failure traces are under
`.tmp/community-browser-results` (outside the source lint surface). Copy selected
acceptance screenshots into the track's evidence directory when handing off a
verified candidate.

For the 2026-09-11 implementation lane, the user-supplied native PostgreSQL 16
credentials failed on both IPv4 and IPv6; its configuration was untouched.
Podman's socket was unavailable, and installed PostgreSQL 16 did not include
PostGIS extension files. An isolated PostgreSQL **17.5 / PostGIS 3.5.2** cluster
was therefore initialized in `.omc/state/community-postgis/data` and listens
only on `127.0.0.1:55439`. Acceptance against this cluster must be reported with
that version limitation. Initializing it required an approved sandbox escalation
because the sandbox account could not create PostgreSQL's restricted process.
No production server, deployment, or existing product database was changed.
