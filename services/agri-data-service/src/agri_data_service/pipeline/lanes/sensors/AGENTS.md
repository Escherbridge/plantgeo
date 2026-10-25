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
`Written.expected_unit_ids`/`present_unit_ids` and the runner's own S11 partial-day rules (below,
"Coverage is stations that answered").

| module | what |
|---|---|
| `merge.py` | the legacy direct writer's block-merge (p4-extract; unused by the config strategy) |
| `source.py` | the `s-sensors` strategy's own roster+poll sweep for one day (Wave O: bypasses the governed `ProviderClient`, mirroring `pipeline/lanes/watersheds/source.py`'s identical choice for a dynamically-sized walk `plan_requests` cannot pre-plan) |
| `nws.py` | the `sensors.nws` config-lane `STRATEGY` (s-sensors): wraps `source.py` in the runner contract, dark behind `lanes/sensors-direct-forward.toml`'s `executor = "legacy"` |

Imports: `foundation`, `ingest`, `warehouse`, `pipeline/parquet`, `pipeline/lanes/_shared` and this
package only. Never another layer's package and never `pipeline/direct/**`
(`tests/test_direct_extraction_shims.py`). **Monkeypatch `merge.py` directly, never the adapter's
re-export. Drive the strategy's tests through `source.py`'s own `upstream_client` with a scripted
`httpx.MockTransport` and `NwsSensorsStrategy(now=clock.now)`, never `pipeline/direct/sensors`.**

## Coverage is stations that answered

`settle`'s `present_unit_ids` is every station whose `limit=1` poll returned (`source.py::_sweep`),
with or without a report for the day -- NOT the stations that contributed rows. Two consequences:

- **A failed poll refuses; a newer report rewrites.** "Partial" means exactly "a poll failed", the
  case S11 was built for (`pipeline/runner/writer.py::_coverage_refusal`): `fewer_units`/
  `coverage_lost` keep the day as written, never erasing the station it served last turn. A fully
  answered sweep reaches the digest rule, so a station's 23:53 report replaces its 22:53 one. Keyed
  by stations-with-rows, a day where any station had not reported yet was partial forever and
  `_decide_unverified_rewrite` rewrites a partial day only on strictly MORE units -- a newer report
  from the same station set was never written (the frozen-winner bug).
- **It must be paired with `absence_recheck_days = 5`.** With 6, the oldest window day is clamped
  to start at `now - 6 days`; stations whose last report that day precedes the turn's clock time
  drop out of present, read as `coverage_lost`, and refuse a day behind the edge on every turn.
  With 5 every window day starts after `now - 6 days` and is polled whole.

**A roster shrink freezes only in-window days; it never wedges.** The roster is rediscovered every
turn, unlike `water_gauges`' fixed tiles. The runner already compares this turn's
`present_unit_ids` against the PREVIOUS receipt's (`writer.py::_coverage_refusal`, re-checked under
the lane-day lock in `ObjectStoreLaneWriter.write_day`), so a retired station makes the days
already written with it refuse as `coverage_lost` until they age out of the 5-day window; newer
days have no receipt and write fresh. `DayContext` needs no previous identity set for this.

**A degraded roster refuses the sweep.** `fetch_station_roster` reports a failed state as
`unavailable_states` instead of raising. A sweep over it would be missing whole states from BOTH
expected and present -- and under the rule above would look COMPLETE -- so `source.py::_sweep`
raises `UpstreamError` and the unit becomes `strategy_error`.

**`limit=1` and 64 KiB, restated from legacy.** One un-limited 5-minute station-day is 312
features / 1.2 MB, past `ingest/sensors.py::OBSERVATION_BOUNDS`' 1 MiB; the too-large error was
isolated as a failed poll, silently dropping the station. `source.py` restates
`_limited_observation_url` and `SENSORS_OBSERVATION_BOUNDS` (never imports `pipeline/direct/**`).

**Checkpoint-ineligible by construction.** `plan_requests` sets no `requested_parameters`, so
`SourceResponse.checkpoint_eligible` is False (`pipeline/runner/contract.py`). Keep it so: `fetch`
ignores the handed client, so a replayed checkpoint could never stand in for this sweep.

## Open items from the `s-sensors` sketch (dark, `executor = "legacy"`)

- **`nws.toml` needs no change.** `nws.py::fetch` bypasses the governed `ProviderClient` entirely
  (Wave O), so the provider file's one declared `stations` endpoint is reused only to give every
  planned unit a valid endpoint key; no `path_template` for `/stations/{id}/observations` was added.
- **The turn clock is injected.** `NwsSensorsStrategy.now` (default: the real UTC clock) is what
  `fetch` hands `fetch_sensors_day_snapshot`, so `run_turn` tests pass `ManualClock.now` and
  exercise the real retention arithmetic end to end.
- **`history_floor = "2026-07-29"` is declared now**, matching the literal registration in
  `pipeline/parquet/lane_registry.py` (`SENSORS_STREAM`), so the mirror parity check runs before G6.
- **No `volatile_columns` declared** — evidence, not an omission: no `SENSORS_SCHEMA` column is
  stamped from this strategy's own fetch/retrieval instant (`nws.py`'s module docstring).
- **No addition owed to `tests/lanes/test_direct_feature_id_parity.py`**: `source.py::
  _measurement_rows` mints the same `direct:` prefix `pipeline/direct/sensors/rows.py::_feature_id`
  already uses, never a new one.
