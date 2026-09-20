# Evacuation Zones direct writer

This package owns the independently scheduled Oregon evacuation-zone snapshot producer. UI FIRE
grouping does not merge it with detections, perimeters, or burn severity; each producer retains its
own source, cadence, failure policy, and publication contract.

`source.py` fetches the bounded upstream population, `rows.py` validates and normalizes it,
`adapter.py` binds it to Parquet publication, and `forward.py` owns the bounded turn, parser, and
`WRITER_CONTRACT`. `watermark.py` and `registration.py` are deliberate static-snapshot additions:
the source has no trustworthy cheap change timestamp, so content determines whether a new version
is owed. `__main__.py` is the supported module entrypoint.

Ordinary boundary failures should use the shared pipeline operational error. Retain a specialized
exception only when the forward or watermark flow distinguishes that semantic state in control
flow and a test pins the distinction.
