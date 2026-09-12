# Agri relational schema

`agri_baseline.sql` is the complete greenfield definition for the retained
relational control and lookup schema. It is a schema-only PostgreSQL dump and
is executed by Alembic revision `20260912_0000`.

Retained data belongs to one of these groups: scheduler control, source
catalogue, expert labels, spatial cells, or compact species and site profile
lookups. Environmental observations, forecast fields, historical promotion
artifacts, and publication payloads belong in governed Parquet.

Generate a replacement baseline only from the reviewed target schema with
`pg_dump --schema-only --schema=agri --no-owner --no-privileges`. A fresh
database must pass the static baseline contract and the catalogue parity test.
Never edit an applied baseline in place outside an explicitly authorized
greenfield reset.
