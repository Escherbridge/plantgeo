---
type: qa-evidence
date: 2026-09-14
status: bounded-probes-reconciled-full-qa-open
author: /root/qa_inventory
coordinator: /root
base_commit: 0f16e40dae3cce1d3b6d4ac00138254a968d974f
---

# Agent and slider parity: session 4 investigation

The [session 3 API receipt](api-samples-session3-20260914.json) contains 37 distinct layer/day cases, each requested twice, against one bbox at rung 5. It proves those captured day-reader responses and their requested/served dates; it does not exercise the typed agent bridge, point-distance support, temporal-neighbor tools, grouped depth surfaces or browser-to-agent date propagation. This investigation defines the next bounded reads. No probes, tests, model calls or runtime edits were performed here.

## Existing selected-day contract

[Regional workflow](../../../../src/lib/server/services/regional-analysis-workflow.ts):34–38 resolves a surface's day from its own viewed-layer reading, warehouse-layer mapping or explicit source alias. Only an unselected source falls back to the last sorted viewed day, then server current day. Lines 266–281 pass that day as `day` for local and regional tools, and `as_of_day` for drought/fire history. The selected-date audit at line 135 records `day` or `as_of_day`; observed, valid and served provenance dates remain separate. A browser-level mixed-date capture is still needed to demonstrate the actual input flow.

[Agent surface mapping](../../../../services/agri-data-service/src/agri_data_service/agent/surfaces.py) groups three temperature metrics, three moisture depths and four soil-temperature depths. These groups cannot be validated by checking a single physical stream. [Agent tools](../../../../services/agri-data-service/src/agri_data_service/agent/tools.py):1718 reads every grouped lane separately; daily lanes preserve exact partition dates and static/release lanes retain a distinct resolved `served_day`. Coverage at line 1358 reads the verified availability indexes. Temporal neighbors at line 1465 are at most the nearest covered date on either side within the explicit search window, not replacement observations for the selected date.

Metadata may appear as an observed audit read, but it cannot ground environmental measurements: [existing report regression](../../../../src/__tests__/services/regional-analysis-workflow.test.ts):218 explicitly rejects coverage, publication-neighbor and nearest-cell metadata as warehouse measurement citations. No new defect is asserted from that audit label alone.

## Highest-priority bounded calls

Call the registered tools directly, without invoking the model. The [bridge route](../../../../services/agri-data-service/src/agri_data_service/routes/agent_tools.py):42 returns the registry without reading a lane or constructing a model client. Its POST at line 63 invokes only one registered tool under a 12-second deadline with 32 KiB request and 2 MiB result caps. Use `GET /api/v1/agent-tools/` to retain the deployed schema, then `POST /api/v1/agent-tools/call` with JSON `{ "name": "<tool>", "arguments": { ... } }`. These POSTs are read operations. Preserve non-200 refusals and do not retry them as a different day.

Use the coordinator's already configured Parquet service URL. For point tools below, use `longitude=-123`, `latitude=48`, `radius_meters=25000`, `feature_count=3`; this point lies inside the prior bbox but is a different spatial query. Compare state and date contracts, not equality with the previous bbox's row counts. If calling local Python functions instead, `agent.tools.run_context()` binds the configured Parquet source and an in-memory ledger; do not call `species_information`, agent chat, graph execution or model clients.

| Priority | Exact tool and arguments beyond the common point parameters | Required evidence |
| --- | --- | --- |
| 1 | `observation_coverage_on_day`: `surface_name="climate-field-shortwave-radiation"`, `day="2026-06-01"`; then `surface_value_near_point` for the same surface/day and `2026-05-31` | June 1 remains a hole or typed unavailability; May 31 was published in the captured map reader. Neither response silently answers June 1 with May 31. Coverage tool takes only surface/day, not point parameters. |
| 1 | `surface_value_near_point`: `surface_name="weather-observations"`, days `2026-09-06` and `2026-09-14` | Captured hole versus published day; explicit dates and local search support. A fresh read may differ if publication advanced, requiring timestamped reconciliation. |
| 1 | `surface_value_near_point`: `surface_name="sensors"`, days `2026-09-06` and `2026-09-09` | Preserve the serving system's current governed absence versus published data while retaining the separate sensor false-absence investigation. Contract agreement does not certify the upstream absence as scientifically true. |
| 2 | `surface_value_near_point`: `surface_name="soil-field-moisture"` and `"soil-field-temperature"`, `day="2026-09-05"` | Exactly three/four declared lanes; each lane's requested day, served day, state, values, units and spatial support. Missing depth stays unknown rather than collapsing into the successful depth. |
| 2 | `surface_value_near_point`: `surface_name="watersheds"`, days `2026-08-07` and `2026-09-14` | Resolve the static reference at/before the selected date and preserve the original release date. A later requested date cannot become a new daily measurement. Pair with a later-dated map release-reader probe; the prior day endpoint alone is not this parity test. |
| 2 | `observation_temporal_neighbors`: `surface_name="climate-field-shortwave-radiation"`, `day="2026-06-01"`, `neighbor_days=7` | Exact applied search interval, signed offsets and bounded neighbors; no claim that a neighbor is June 1 data. No common point parameters apply. |
| 2 | `drought_history_at_point`: `longitude=-123`, `latitude=48`, `as_of_day="2026-09-08"`, `weeks_back=2` | History ends at the selected day, preserves weekly validity and does not borrow later drought releases. No feature/radius parameters apply. |
| 3 | `surface_value_near_point`: `surface_name="soil-survey"`, `day="2026-08-28"`; then surface `"interventions"` at that day | Unwritten soil-survey and unadmitted intervention environmental lane stay typed unavailable/refused. Public social feature reads are a separate application contract. |
| 3 | `forecast_summary_for_cell`: `longitude=-123`, `latitude=48`, `radius_meters=25000` | Exact `forecast_parquet_lane_not_published` refusal, no fabricated forecast, cell or fallback. No `day` parameter is exposed by this tool. |

Run sequentially with the route's bounds, retaining response bytes/hash, tool arguments, timestamp, completion/truncation and result or refusal. Avoid a full nearby-feature or history census. After these cases, expand point/rung coverage only where discrepancies require it. Independently verify a mixed UI selection (for example moisture September 5 versus weather September 14) reaches the respective tool calls before accepting end-to-end parity.

## Static, land and forecast limits

The [land reader](../../../../src/lib/server/services/land-context/reader.ts):89–115 still returns `unknown_coverage` when the [placeholder candidate reader](../../../../src/lib/server/services/land-context/parquet-reader.ts):59 returns no admitted candidates. Results carry `isCurrentReferenceOnly=true`; no temporal observation axis exists. These in-process tools share reader functions with the map, have no model cost, and must preserve unknown coverage and public-office-only contact policy. Their registration prose describing PostgreSQL is stale relative to the current Parquet placeholder, not evidence of a working alternate backend. Do not treat no candidates as proven parcel absence, a selected-date answer, or completed land QA.

A concrete catalogue-description mismatch exists: [forecast wrapper](../../../../services/agri-data-service/src/agri_data_service/agent/tools.py):1897 advertises published forecasts, uncertainty bands and resolved cells, while its actual implementation at line 1010 always returns `forecast_parquet_lane_not_published`. The exported tool schema takes that description to the model. A narrowly scoped follow-up can make the tool description state the actual refusal/admission boundary and pin the exported schema; implementing forecast data is outside this finding. No runtime change was made in this read-only lane.

Botanical interval tools use a separate admitted release and temporal vocabulary and were not among the 37 physical day cases. They require their own release/filter/interval and neighbor evidence after the matrix above, rather than coercion into a daily surface name. Full rendered sliders, agent report citations, canceled/failed calls, no-date semantics and browser workflows remain unpassed.

## Authorized follow-up within session 4

After the read-only findings above, the coordinator authorized the bounded forecast description correction. The exported wrapper docstring now declares `forecast_parquet_lane_not_published`, unavailable values/cell and the governed admission gate. The agent directory documentation records the rationale; the existing HTTP catalogue test now asserts that the actual exported description includes the refusal code and unavailable guidance. Function parameters, return behavior, readers and provider paths are unchanged. Only formatting was run; tests and independent review remain pending under the coordinator's integrated sweep.

The coordinator also requested a reviewable executable probe draft. Repository-local `.omc/research/runbook-20260914/agent-session4-matrix.json` defines 16 calls; `read-session4-agent.py` defaults to printing the plan and requires `--execute` to read the fixed previously probed Parquet host. It rejects redirects, reads the deployed schema before dispatch, enforces its read-only tool allowlist, runs sequentially without retries, caps bodies with a 2 MiB plus one-byte truncation sentinel and uses a 15-second client socket timeout. Exact request/response bytes, hashes, URL, timestamps, context and incomplete/error dispositions are retained in a new timestamped directory. The draft was not executed by this author; root owns execution and final interpretation.

## Executed probe reconciliation

The coordinator subsequently executed all 16 calls into `.omc/research/runbook-20260914/session4-agent-20260914T195709241763Z`. This author independently reconciled the retained request bytes, deployed schemas, response bodies and receipt; no additional remote calls were made. The [machine-readable result](agent-parity-results-session4-20260914.json) retains every case's source path, response hash, date/state summary, bounds, errors and verification fields.

| Binding | SHA-256 |
| --- | --- |
| Original run `receipt.json` | `fb9972488d8874d1ae1710a13d521295de2d79b0dc6302e638ab67c8959a50d9` |
| Deployed `catalogue.json` | `7895c5e471736f2b26543b292cc8b85f1464613dbfca6f2da38fb3c12e86c00b` |
| Canonical `agent-parity-results-session4-20260914.json` | `fd793239f486ab7458a075b059830d1c31cd091162d9204efa67d44045f49cdf` |

All 16 response and request hashes matched their receipts, response byte counts matched, requests matched the matrix, required/allowed argument names matched the deployed registry, and returned tool names matched. All returned HTTP 200 with complete JSON result objects. There were no transport failures, incomplete HTTP bodies, bridge errors or schema-skipped calls. Two result bodies correctly carried typed errors: `surface_not_served_from_parquet` for interventions and `forecast_parquet_lane_not_published` for forecasts. HTTP success does not convert these refusals into data availability.

| Case group | Captured result and interpretation |
| --- | --- |
| Shortwave cutoff | May 31 is published with one nearby result dated and served May 31. June 1 is `day_not_written`, served day null, no features. Coverage separately says `not_published`, `is_covered=false`, latest May 31, source ceiling June 24. These distinct state vocabularies agree on a missing selected day. |
| Weather | September 6 remains `day_not_written` with no served day. September 14 is published and served September 14, with zero features in the 25 km point search. The latter is not an upstream absence and cannot be compared numerically with the prior larger bbox. |
| Sensors | September 6 is served as governed absence. September 9 is published with three returned records dated and served September 9 and an explicit truncation flag. The absence body records the original September 7 `00:08:07.658460Z` gap-fill run `parquet-gap-fill:0ef87f10-aab0-4362-8424-8fd085185047`, stating that its warehouse export found zero rows and did not contact the upstream source. This preserves the narrower original claim and does not certify upstream emptiness; the later positive-reading conflict remains open in the sensor investigation. |
| Grouped soils | All three moisture depths and all four temperature depths are present, published, requested/served September 5. Every returned feature's observed day is September 5. Each lane returns three rows with `features_truncated=true`; this validates bounded lane/date representation, not full populations or spatial conservation. |
| Watersheds | Requests on August 7 and September 14 both serve the August 7 static release. Returned feature release dates stay August 7, and the older source observation instant stays separate. Both responses preserve centroid distance and exact containment flags; one sampled polygon contains the point while neighboring polygons do not. Both three-feature lists are explicitly truncated. |
| Soil survey | Agent static-release resolution returns `day_not_written`, no served day and no features. The prior raw physical day endpoint returned `lane_never_written`. This is an unavailable-result vocabulary difference between release resolution and raw day reading, not evidence that soil-survey was published or zero-valued. The wider classification semantics remain outside a strict one-to-one state comparison. |
| Shortwave temporal neighbor | Search May 25–June 8 returns only May 31, offset -1 and distance 1 day. No June 1 value or after-side observation was substituted. Neighbor `observation_count` is publication metadata, not a local measurement count. |
| Drought history | Requested/scanned span August 25–September 8 remains anchored to September 8. The three weekly releases are August 25, September 1 and September 8, each with severity class 1 at this point. The tool reports no scan-budget narrowing. This does not establish continuous daily observations. |
| Intervention and forecast placeholders | Both preserve their exact typed refusal. Forecast cell is null and forecast values are empty; no admission or forecast availability is implied. |

Ten lane results explicitly hit the three-feature cap: sensors, seven grouped soil lanes and the two watershed calls. Their HTTP bodies are complete but their feature populations are bounded. No full spatial/population result or equality with previous rung-5 bbox counts is claimed.

The shortwave May 31 feature also retains `allowed_client_exposure=false`. This is a known unresolved admission field recorded in [the ingestion/serving contract](../../../../docs/data-ingestion-and-serving-contract.md), line 81, where the historical reader carries rather than gates on it. The result does not close exposure approval or justify silently filtering/relabeling provenance. Record it for the owning admission work; this read-only reconciliation makes no runtime policy change.

Within these bounded probes, no selected-date substitution, grouped-lane loss or future static-release leakage was observed. The deployed catalogue still belongs to the pre-deployment candidate; the local forecast description correction awaits its own release verification. Browser propagation, mixed selected dates, remaining surfaces, botanical intervals, land placeholders, report citation behavior and full QA remain open.
