# Sensors direct writer

This package owns the independently scheduled NOAA NWS station-observation producer. `source.py`
polls the bounded rolling provider window, `rows.py` conforms station observations, `adapter.py`
performs the durable station-day merge, and `forward.py` owns the turn, parser, and
`WRITER_CONTRACT`. `__main__.py` is the supported module entrypoint.

There is no separate products/support module because this is one stream and the shared behavior is
small. `--max-days` bounds the calendar buckets returned by one poll, not a historical backlog.
Ordinary boundary failures should use the shared pipeline operational error; a specialized type is
reserved for a tested semantic branch rather than a lane-specific wrapper name.
