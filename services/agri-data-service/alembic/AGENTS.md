# Alembic migrations

Alembic owns the `agri` schema. The live history contains one revision,
`20260912_0000`, which executes `db/agri_baseline.sql`. Runtime processes must
not create schemas, tables, extensions, or migration stamps.

The baseline expects the extensions listed in
`agri_data_service.db.extensions.REQUIRED_EXTENSIONS` to be installed by the
operator. It is forward only; recover by restoring a verified backup.

When the relational schema changes, create a normal forward revision from the
current head and update the readiness and test head pins in the same change.
Do not add environmental observation or forecast payload storage to `agri`;
those datasets are governed Parquet.
