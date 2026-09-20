# Weather Observations direct writer

This package owns the independently scheduled Open-Meteo current-conditions producer. `source.py`
polls the rolling grid, `rows.py` conforms observations, `adapter.py` performs the durable daily
merge, and `forward.py` owns the turn, parser, and `WRITER_CONTRACT`. `recovery.py` is a deliberate
rolling-source addition: it replays retained provider evidence that a later poll can no longer
reconstruct. `support.py` contains shared turn mechanics and `__main__.py` is the supported module
entrypoint.

`--max-days` bounds the at-most-two buckets from one poll, not an archive walk. Ordinary boundary
failures should use the shared pipeline operational error; specialized errors require a tested
semantic branch that changes recovery or publication behavior.
