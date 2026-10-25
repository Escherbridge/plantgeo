# `pipeline/lanes/sensors/` — the sensors layer package

`merge.py` holds `merge_sensors_day`, `DirectSensorsMerge` and `DirectSensorsError`, moved out of
`pipeline/direct/sensors/adapter.py` by `p4-extract` (track `config_driven_ingestion_20260926`,
Phase 4) with no behaviour change. Its module docstring argues the merge unit: the whole
(sensor_id, observed_day) block, replaced when the incoming report is at least as new.

The lock-holding `DirectSensorsForwardAdapter` and its absence retraction did NOT move: it prints a
stderr event, which a lane module may not (FR-38). It re-exports the three names for its legacy
importers (`DEPRECATED_ALIASES.md`, deleted at 7D-n of the supersession cleanup lane).

Imports: `warehouse` only today. Never another layer's package and never `pipeline/direct/**`
(`tests/test_direct_extraction_shims.py`). The `s-sensors` slice builds the strategy beside it.
**Monkeypatch `merge.py`, never the adapter's re-export.**
