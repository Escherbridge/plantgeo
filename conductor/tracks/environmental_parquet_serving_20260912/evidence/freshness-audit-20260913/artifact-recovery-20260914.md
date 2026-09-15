---
type: evidence-receipt
status: partial_recovery
recorded_on: 2026-09-14
author_task: /root/qa_inventory
coordinator: /root
---

# Original audit artifact recovery

This recovery restores retained September 13 command-output evidence. It makes no new production measurement and does not change the historical audit's scope or findings. Root separately restored `audit.md` and the track-root `freshness-resolution-plan-20260913.md`; this task did not overwrite either document.

## Captured source and reconstruction

Source: `.omc/state/freshness-fanout-20260913-1428/forward-recovery/events.jsonl`, SHA-256 `dda8f4f5d295a01abff9a08870787c2c32656799af4789422832b008dfa51510` at recovery.

- Line 73 is a successful `item.completed` / `command_execution` event containing three `Get-Content -LiteralPath ... -Raw` outputs: the original manifest, then `DEPLOYMENTS`, then `ACTIVATION`. The labels are command-output delimiters, not original file content.
- Line 102 is a successful raw read of the original `executor-tick.json`.
- Read the JSONL as UTF-8. Extract each raw JSON substring with `JSONDecoder.raw_decode`; do not parse and reserialize its object to restore formatting. Preserve the substring's CRLF line endings and add the original final CRLF. The encodings below were verified against hashes already present in the captured original manifest.

| Restored artifact | Encoding / bytes | SHA-256 | Proof |
| --- | --- | --- | --- |
| [deployments.json](deployments.json) | UTF-8 with BOM; 839 bytes | `2c54a78e03090431b854975f70a864395b1579c64f964f49e957ce876a71f69e` | Exact match to original manifest digest. |
| [activation.json](activation.json) | UTF-16 little-endian with BOM; 878 bytes | `5a7f20d06a56bfd659cf6b9f8cffd4020d32870953741d3ff2eef0e3ad8ffb9e` | Exact match to original manifest digest. |
| [executor-tick.json](executor-tick.json) | UTF-16 little-endian with BOM; 19,038 bytes | `1f418fb0563e6236677b79ef1a7d4fdbe22858b7d21d5d97399b4b480f8b1995` | Exact match to original manifest digest. |
| [manifest.json](manifest.json) | Captured raw JSON text, stored as UTF-8 without BOM; 1,645 bytes | `c538df347826dbe756bac56b81ef8b447da7a5b1a06f8d934af3886e995e64f9` | Original text and artifact digests retained. No original self-digest exists in the recovered manifest; byte-identical original encoding is not claimed for this file. |

All destination paths were checked absent before writing. All three original artifact digests were checked before any recovered file was written. The manifest content was not invented or updated to match current production. Its original `repository_head`, timestamp, deployment identities and scope remain historical.

## Still missing

No complete original was found for these references. They remain absent rather than being replaced by guessed JSON, partial tables or a current observation carrying an old date.

| Original artifact | Search evidence and remaining limit |
| --- | --- |
| `coverage.json` | `water-weather-history/events.jsonl:166` contains only a 54,944-character tail from an `rg` read, beginning inside a date value. It cannot reproduce the original 120-row document or original digest `3b157d8ed1d80277a4c11d660bbdf508cfbc52b9028325c8ef0b63b073866d3f`. |
| `coverage.csv` | `climate-freshness/events.jsonl:116` contains a formatted subset for shortwave and humidity, not the original CSV. Original digest remains `4592af58981b5165e372af9980a4c57474f8f539023f60b9860dfa3eef9da3bb`. |
| Track-root `fanout-launch-20260913.md` | No file history under `git log --all -- <path>` and no exact successful captured read found in the local freshness event logs. `.omc/state/freshness-fanout-20260913-1428/README.md` remains separate fanout evidence; it is not substituted for the missing document. |

Search covered relevant project-local freshness state/event logs, worktree file inventories and all Git refs. Some nested pytest-cache/temp directories denied file enumeration; no claim is made about their unreadable contents. No global session store, deletion recovery or fabricated reconstruction was attempted.

## Bounded read-only follow-up

The following are source-derived commands and keys for root's current audit, not executions performed by this recovery author. The authoritative deployed source revision and current operational observations belong in root's new receipts.

- `agri-service data parquet day --layer <physical-slug> --kind observed --zoom <0|5|9|13> --day <YYYY-MM-DD> --bbox=-125,42,-111,49` reads one bounded day. The equivalent HTTP path is `/api/v1/parquet/day` with `layer`, `kind`, `zoom`, `day`, `bbox` query parameters. Coverage HTTP is `/api/v1/parquet/coverage`; CLI `data parquet coverage` deliberately runs an independent whole-warehouse object census, so it is heavier than the HTTP availability path.
- Physical objects use `layer=<physical-slug>/kind=observed/zoom=<00|05|09|13>/year=YYYY/month=MM/day=DD/`. List only that day prefix and read its `part-N.parquet`, `_complete.json`, `_complete.empty.json` and `absent.json` objects as applicable. Prepend the configured `object_store_prefix`. Use marker-listed parts and compare actual bytes/digests/row counts rather than assuming `part-0` is the whole population.
- Availability root is `layer=<physical-slug>/kind=observed`. Its pointer is `availability/_LATEST.json` below that root; follow the exact `generation_key` and verify `generation_sha256`, row population and required rungs. `availability/_COVERAGE_ROLLUP.json` at warehouse root is only a pointer-bound cache and cannot independently prove objects or generations.
- Shared read-only APIs are `BotoObjectStoreBackend.from_credentials(settings.require_object_store())`, then `list_objects`, `size_of`, `get`; and `BotoAvailabilityStorage.from_settings()` with `read_latest_availability(store, lane_root=..., expected_lane=..., expected_required_rungs=(0, 5, 9, 13))`. The latter verifies pointer plus generation. `read_availability_pointer` alone explicitly does not verify generation bytes. No publisher/apply method belongs in this inspection.
- The existing `data availability-bootstrap` and `data availability-publish` commands validate externally pinned local input without network when `--apply` is absent. They do not discover missing history or establish physical proof. This follow-up does not authorize their `--apply` form or any direct-writer command.

Physical weather slug: `weather-observations`. Physical shortwave slug: `climate-field-shortwave-radiation`. Soil has eight physical streams: `soil-field-moisture-0-7cm`, `soil-field-moisture-7-28cm`, `soil-field-moisture-28-100cm`, `soil-temperature-0-to-7cm`, `soil-temperature-7-to-28cm`, `soil-temperature-28-to-100cm`, `soil-temperature-100-to-255cm`, `soil-field-vpd`. The three UI soil capability names are not interchangeable with those physical roots.

Source references: `interface/cli/parquet.py:58`, `:120`; `interface/http/parquet_routes.py:180`, `:234`; `foundation/parquet/paths.py:241`, `:280`, `:301`; `pipeline/parquet/objectstore.py:127`; `pipeline/parquet/availability_index.py:946`, `:1475`; `warehouse/schemas/availability_index.py:20`; `pipeline/parquet/coverage_rollup.py:43`. Paths in this paragraph are relative to `services/agri-data-service/src/agri_data_service/`.

## Source ownership and clocks

| Product | Declared scheduled owner | Clock and remaining proof |
| --- | --- | --- |
| Weather observations | `weather-observations-direct-forward`, internal executor `30 * * * *` | Current-condition polls, not a retrospective date fetch. Source module has no original-response checkpoint retention in the current inspected main tree. Do not replace September 6's missing current-condition record with another archive product or a fresh poll. |
| Eight soil streams | `soil-era5-land-direct-forward`, internal executor `50 * * * *` | Open-Meteo ERA5-Land archive, configured nine-day lag; September 5 is the September 14 planning edge. Prove all eight streams and all four rungs independently before assessing UI intersections. |
| Climate, including shortwave | `climate-nasa-power-direct-forward`, internal executor `40 * * * *` | NASA POWER meteorology uses five-day lag; shortwave uses a conservative, explicitly unmeasured 75-day lag. Shortwave history ends May 31 and its direct writer owns June 1 onward. July 1 is the September 14 planning ceiling, not proof that NASA has supplied it. |

Schedules are declared at `execution/job_executor_service.py:392`, `:411`, `:453`; production allow-list, held runs and current child results must be inspected separately. Null Railway cron schedules do not mean these internal schedules are absent. The lag/history contracts are `pipeline/direct/soil/products.py:66` and `pipeline/direct/climate/products.py:83`.

Existing freshness work in `.tmp/freshness-integrated-20260914` and its `.omc/state/freshness-integration-20260914/HANDOFF.md` is a separate candidate. Its retained-source recovery, publication reconciliation and source-selection changes must not be assumed present in the inspected main tree. Gapless publication owns durable repairs and operational recovery; environmental serving owns shared readers, and production acceptance owns sustained advance evidence.

No tests, application checks, remote probes, operational changes, source acquisition or publication were run by this artifact-recovery task.
