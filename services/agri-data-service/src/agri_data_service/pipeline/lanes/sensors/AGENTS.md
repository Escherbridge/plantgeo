# `pipeline/lanes/sensors/` — the sensors layer package

`merge.py` holds `merge_sensors_day`, `DirectSensorsMerge` and `DirectSensorsError`, moved out of
`pipeline/direct/sensors/adapter.py` by `p4-extract` (track `config_driven_ingestion_20260926`,
Phase 4) with no behaviour change. Its module docstring argues the merge unit: the whole
(sensor_id, observed_day) block, replaced when the incoming report is at least as new.

The lock-holding `DirectSensorsForwardAdapter` and its absence retraction did NOT move: it prints a
stderr event, which a lane module may not (FR-38). It re-exports the three names for its legacy
importers (`DEPRECATED_ALIASES.md`, deleted at 7D-n of the supersession cleanup lane).

**`merge.py` is NOT used by the `sensors.nws` strategy below.** The legacy block-merge exists
because the legacy writer can read back the previously published partition; a config strategy
cannot (`pipeline/runner/contract.py::DayContext` hands it day lists and digests, never rows), so
`nws.py::settle` recovers the same anti-regression property generically instead, through
`Written.expected_unit_ids`/`present_unit_ids` and the runner's own S11 "fewer units never written"
rule. See `nws.py`'s module docstring for the one way this is NOT equivalent to the legacy merge.

| module | what |
|---|---|
| `merge.py` | the legacy direct writer's block-merge (p4-extract; unused by the config strategy) |
| `source.py` | the `s-sensors` strategy's own roster+poll sweep for one day (Wave O: bypasses the governed `ProviderClient`, mirroring `pipeline/lanes/watersheds/source.py`'s identical choice for a dynamically-sized walk `plan_requests` cannot pre-plan) |
| `nws.py` | the `sensors.nws` config-lane `STRATEGY` (s-sensors): wraps `source.py` in the runner contract, dark behind `lanes/sensors-direct-forward.toml`'s `executor = "legacy"` |

Imports: `foundation`, `ingest`, `warehouse`, `pipeline/parquet`, `pipeline/lanes/_shared` and this
package only. Never another layer's package and never `pipeline/direct/**`
(`tests/test_direct_extraction_shims.py`). **Monkeypatch `merge.py` directly, never the adapter's
re-export; monkeypatch `source.py`'s own `fetch_sensors_day_snapshot` (or, for the retention
arithmetic alone, `source.py`'s imported `fetch_station_roster`) for the strategy's tests, never
`pipeline/direct/sensors`.**

## Open items from the `s-sensors` sketch (dark, `executor = "legacy"`)

- **`nws.toml` needs no change.** `nws.py::fetch` bypasses the governed `ProviderClient` entirely
  (Wave O), so the provider file's one declared `stations` endpoint is reused only to give every
  planned unit a valid endpoint key; no `path_template` for `/stations/{id}/observations` was added.
- **The runner's own turn clock is not threaded into `fetch_sensors_day_snapshot`.** `nws.py::fetch`
  calls it with no `now=`, so the rolling-retention check always reads the real wall clock rather
  than the turn's (test) clock. Harmless for production (the real clock IS the real clock) but means
  no `run_turn` flow test can deterministically pin the exact retention boundary end-to-end; see
  `tests/lanes/sensors/test_nws.py` for how the boundary is pinned directly against `source.py`
  instead, with `run_turn` flow tests driven through a monkeypatched snapshot.
- **The roster is not a stable partition, unlike `water_gauges`' fixed tiles** (`nws.py`'s module
  docstring, "H2"): `settle`'s `expected_unit_ids` is this turn's own discovered roster, so an
  ordinary roster shrink (NWS retires a station) can read identically to a failed poll under the
  generic "fewer units never written" rule. Flagged for the per-lane adversarial review before G6.
- **No `volatile_columns` declared** — evidence, not an omission: no `SENSORS_SCHEMA` column is
  stamped from this strategy's own fetch/retrieval instant (`nws.py`'s module docstring).
- **No addition owed to `tests/lanes/test_direct_feature_id_parity.py`**: `source.py::
  _measurement_rows` mints the same `direct:` prefix `pipeline/direct/sensors/rows.py::_feature_id`
  already uses, never a new one.
