# Watersheds direct writer

This package owns the independently scheduled NHDPlus watershed snapshot producer. `source.py`
fetches and validates the source population, `rows.py` conforms geometries, `adapter.py` binds the
snapshot to publication, and `forward.py` owns the turn, parser, and `WRITER_CONTRACT`.
`watermark.py` is a deliberate static-release addition: it derives source currency from the same
bounded source snapshot. `products.py` holds lane constants and `__main__.py` is the supported
module entrypoint.

The expensive source snapshot may be reused within one turn, but it is never replaced by a
PostgreSQL observation fallback. Ordinary boundary failures should use the shared pipeline
operational error; specialized errors require a distinct, documented, tested semantic branch.
