# Agri data service application contracts

## Production database target

The `receiver_writer` and `published_reader` HTTP profiles target the canonical PostgreSQL
database named `plantgeo`. Railway-generated PostgreSQL URLs use the plain `postgresql` scheme;
`Settings` normalizes that scheme to `postgresql+asyncpg` before validating the target.

Keep the database-name check in the profile resolver. It makes an accidental reference to the
legacy Aevani database named `railway` fail during Sanic startup with an explicit configuration
error instead of surfacing as repeated opaque `/ready` failures. Command-scoped and local DSNs
remain portable and continue to use the shared completeness validator without this production
target restriction.
