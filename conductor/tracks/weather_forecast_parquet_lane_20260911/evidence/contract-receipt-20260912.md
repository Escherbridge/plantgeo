---
type: evidence-receipt
track: weather_forecast_parquet_lane_20260911
recorded_on: 2026-09-12
status: planning_contract_frozen_admission_open
source_commit: 64f4f892bd2b744cc097c7f76a1f239997b80f52
source_tree: f8697da694a3f9fbbf28e70ff7581bd59546bfd6
---

# Forecast evidence and next-gate contract

This receipt freezes the obligations and dependency order for the next gate. It
does **not** admit a provider, close F0, or claim a published forecast. Both tracks
remain planned. Evidence is repository inspection at the source commit above;
there was no provider probe, data acquisition, service request, database access,
object-store access, writer, scheduler, UI runtime change or deployment.

The companion [experience receipt](../../weather_forecast_experience_20260911/evidence/contract-receipt-20260912.md)
defines modes, cards, rendering and acceptance targets against this contract.
Provider-specific facts still require a separately authorized source-admission
lane and independent scientific/data-contract review before implementation.

## Evidence reconciliation

| Evidence at the source commit | What it establishes | What it does not establish |
| --- | --- | --- |
| [Current weather schema](../../../../services/agri-data-service/src/agri_data_service/warehouse/schemas/weather_observations.py), product note and `WEATHER_OBSERVATIONS_GRAIN`; [direct source](../../../../services/agri-data-service/src/agri_data_service/pipeline/direct/weather_observations/source.py), `poll_current_conditions` | The live lane identity is sampled current-condition model estimates; its grain is latitude, longitude, observed instant. The direct path reuses the current-condition parser. | The endpoint name `/forecast` does not give these current polls model-run/valid-time forecast identity. A schema's historical Postgres provenance note does not authorize reviving that writer. |
| [Older lane document](../../../../docs/lanes/weather-observations.md), opening scope; current schema's product note | NASA POWER and ERA5-Land history belong to the distinct signal/climate archive. The shared weather name is overloaded. | Neither archive coverage nor temperature-history publication proves availability for the weather toggle on the same day. |
| [Slider capability](../../../../src/lib/server/services/parquet-slider-capabilities.ts), weather registration at line 99 and return at lines 707–708 | Weather is a daily-series Parquet reader with `forecastHorizonDays: 0` and `forecastVariants: []`. | This daily availability contract does not yet describe published runs and valid-time windows. No positive horizon can be inferred from source marketing or endpoint naming. |
| [Python weather reader](../../../../services/agri-data-service/src/agri_data_service/planes/weather_observations.py), `OBSERVED_KIND` and observed-day filtering; [TypeScript reader](../../../../src/lib/server/services/parquet-trpc-readers.ts), lines 1822–1856 | The Python reader selects observed partitions and one zoom rung. Non-current dates use an exact day; current-day reads use yesterday/today with a three-hour freshness filter. | Current-day freshness is not historical exactness; neither path pins a forecast model run or valid-time window. |
| Current weather schema, fields and tier derivation; TypeScript reader, lines 1188–1206 | Base support is raw points; coarse support is declared aggregate-cell means. The schema has speed/direction, no `u`/`v`, and coarse direction is deliberately null. | Coarse sampled cells are not a native model grid, and scalar speed aggregation is not vector-preserving forecast publication. |
| [Parquet envelope](../../../../src/lib/server/services/parquet-envelope.ts), lines 83–138; TypeScript reader, lines 997–1030 | Published, governed-absence, day-not-written and lane-never-written states map to ready/absent/not-generated, with upstream-unavailable also in the result contract. | Forecast outside-domain, stale-run and per-variable valid-interval availability still need their own contract. |
| [Upstream expansion plan](../../upstream_dataset_expansion_20260806/plan.md), Phase 4 status and persistence follow-up | Repository evidence records built ensemble ingest/reduction and synthetic quantile validation, with unconfirmed live member counts and schema-gated persistence. | Synthetic receipts are not source admission, immutable Parquet publication, or real-data acceptance. Old Postgres migration/cron proposals are not forecast dependencies to execute. |
| [Ensemble adapter](../../../../services/agri-data-service/src/agri_data_service/ingest/open_meteo_ensemble.py), `ensemble_hourly_parameters`; [ensemble executor](../../../../services/agri-data-service/src/agri_data_service/execution/ensemble_forecast.py), `issue_time` | The adapter requires an explicit model; the plan's `issue_time` is midnight UTC derived from `issue_date`. | That derived midnight must not be copied into the new contract as a provider model initialization or release timestamp. No exact deterministic provider/model is admitted by the two forecast tracks. |
| [Historical approval](../../platform_experience_qa_20260911/evidence/weather-approval-20260912.md) and [browser receipt](../../platform_experience_qa_20260911/evidence/browser-weather-20260911.md) | Conditional local desktop unavailable-state approval, including separately recorded style-readiness and synthetic ready-to-outage checks. | Populated data, narrow mobile, real date transitions, forecast fields and hourly/daily selected-location cards remain unproven. |

Existing source files were read as evidence only. Their local constants or old
architecture are not automatically admitted forecast contracts. The process
outcomes in [the layer standard](../../../../docs/layer-lane-standard.md) remain
binding; its September 2 scheduler correction requires three logical durable
executor duties, not three new Railway crons.

## April 28 screenshot disposition

The two [forecast specs](../spec.md) and the experience spec name **2025-04-28**,
not 2026-04-28. The original screenshot and a response pair bound to that capture
were not located in the inspected repository evidence. The September browser
receipt instead records `2026-09-12` with an unavailable data service. It cannot
serve as the April regression's measured catalogue/reader reconciliation.

The remaining reconciliation packet must contain the original image or its
traceable reference/hash, capture time, selected date and timezone, viewport and
zoom, toggle/product identity, candidate commit/tree, capability request/response
and exact reader request/response from one pinned release. Preserve requested
and served day, availability/absence state, support, response counts and failures.
Then classify catalogue/reader agreement or disagreement; a screenshot of sparse
squares alone proves neither absent source coverage nor an ingestion defect.

The **reader track** owns exact response and date semantics, **gapless publication**
owns coverage/gap reconciliation, **Postgres retirement** owns legacy provenance
and custody, and **multiscale/platform QA** owns the visual reproduction. Their
receipt must resolve whether the day is supported, governed absent, a coverage
gap, or temporarily unreadable. A disagreeing catalogue/reader pair remains a
defect to that owner; forecast does not resolve it by substituting another product.

Source inspection adds a required regression case: both
`WeatherHistoryReport.tsx` (lines 124–137) and `LayerManager.tsx` (lines 480–496)
use `keepPreviousData` and permit placeholder data through the selected-day
guard. This is **not a reproduction or diagnosis of the screenshot**. The future
fixture must include loading placeholders and delayed old-day responses as well
as settled mismatches. Current-day `requestedDay` versus `servedDay` remains an
explicit reader follow-up in the historical approval receipt.

## Frozen logical contract for F0/F1

Names below are logical fields to carry into the later versioned schema, not
new runtime interfaces created by this receipt.

| Area | Required contract and refusal |
| --- | --- |
| Product admission | Exact provider, endpoint/product, redistribution/attribution terms, model family/version, deterministic variant, cadence, horizon, native grid/domain, variable definitions/units, quotas and missing-value conventions. Preserve provider documentation and bounded sample evidence in a later authorized packet. Unknown model initialization or unstable product identity fails admission. |
| Run identity | Immutable product/version + provider run ID + initialization UTC; provider issue/release UTC when supplied, otherwise explicit unavailable provenance. Separate fetch, admission and publication UTC timestamps; none substitutes for initialization. A blended product must expose a truthful versioned run identity or be refused. |
| Time and interval | Instantaneous values carry `valid_time`; aggregates/accumulations carry explicit start/end, provider endpoint convention and statistic. Normalize intervals as start-inclusive/end-exclusive only with a documented source mapping. Lead time is valid time minus initialization for instantaneous values; interval products also expose start/end leads. Preserve UTC; local timezone is presentation. Reject ambiguous units, overlapping accumulation sums or invented timestamps. |
| Variable catalogue | Temperature/apparent temperature, humidity/dew point, clouds, pressure, precipitation probability and interval amount, conditions code, wind/gust/direction are candidates, not promised fields. Each admitted variable declares unit, height/reference, statistic, interval semantics, valid range, conversion and supported render form. No probability or uncertainty is invented from a deterministic value. |
| Spatial/scalar support | `native_grid`, `sampled_point`, or `derived_field` per variable, with source/output resolution, CRS/grid identity, coordinates and represented footprint/domain mask. A sampled coordinate is not a native cell footprint. Derived fields additionally require versioned method, distance/support mask and validation; unsupported regions refuse. Aggregated samples retain sample-support meaning. |
| Wind | Retain eastward/northward `u`/`v` in m/s at a declared height and interval; declare earth-relative versus grid-relative basis and rotation. For meteorological direction **from** true north clockwise, use `u = -s sin(theta)`, `v = -s cos(theta)`. Aggregate compatible components using declared support/time weights, then derive magnitude/direction. Never scalar-average directions or interpret gust as vector-mean speed. Opposing vectors may cancel; calm/near-zero threshold and undefined direction are explicit, versioned admission parameters. |
| Missingness | Distinguish exact absence, outside domain, stale run, not-yet-generated interval, upstream unavailable and observed numeric zero. Preserve per-variable reason and coverage counts; null needs a reason. A missing required artifact blocks publication; an admitted source-declared absence is represented by an explicit completeness marker. Partial coverage never reports complete. |
| Immutable publication | Manifest binds hashes, required variables/support artifacts, completeness, run and availability windows. Advance one conditional pointer only after all requirements reconcile. Pin manifests for readers; preserve supersession, replay, interrupted publication recovery and rollback without mixed runs. Retention must protect references used by active readers and rollback. |
| Bounded readers | Field request binds product/version, run, variable, valid instant/interval, bbox and zoom/resolution. Point request binds actual selected coordinates, run, variables and window. Both return represented support, source/sample coordinates and distance, units, times, missingness, lifecycle and provenance. Cap rows/bytes/work; coarsen with disclosure, return explicit continuation, or refuse. Never silently truncate a complete-looking series. |
| Capability and agents | Register a distinct forecast product/variant and run-indexed available valid intervals only after publication. A positive horizon must reflect the published run's actual coverage. UI and agent pin the same product, run, place, window, units and support. No latest-run retry, historical/live fallback, viewport-centre substitution or neighbouring-time substitution. |

## Next gate and dependency order

**Next gate remains F0: exact deterministic source admission plus an independently
reviewed contract packet.** This receipt freezes deterministic-first sequencing
and required semantics; provider-specific blanks remain blockers. Ensemble
enrichment follows a separately admitted uncertainty contract and is not a
prerequisite for deterministic delivery.

1. Integrate this documentation only after independent review of its exact diff.
   Do not check off source admission, screenshot reconciliation or implementation.
2. A separately authorized F0 lane obtains the admission packet and the April
   reconciliation owners' evidence. Resolve provider quotas, run identity,
   source support and the numerical admission parameters below. Owner decisions
   must be recorded, not inferred from earlier upstream authorization.
3. F1 freezes manifests, variable/support/window schemas and source-specific
   budgets against that packet. Reader capability shape and X0 experience
   contract are agreed together; this contract handoff can precede publication,
   but cannot make X0 runtime changes available before F3.
4. F2 starts only after F0/F1 and explicit completion/transfer from gapless
   publication for `lane_registry.py` and `job_executor_service.py`. It later
   owes forward publication, repair that authors work, and coverage/run-status
   advances in the existing durable executor. No new cron/deployment is implied.
5. F3 follows F1/F2. The reader owner transfers
   `parquet-slider-capabilities.ts`; botanical-species-profile ownership must
   transfer `app.py`, agent graph/MCP/prompts/tools and the directory docs listed
   in this track's metadata. Existing acceptance of a botanical slice is not
   an implicit transfer of every shared file.
6. X0 runtime work follows F3's accepted response/capability contract and the
   reader/renderer transfers. X1 cards, X2 scalar and X3 wind may then proceed
   independently on their owned files; X4 integration waits for all three,
   F4 data acceptance and every shared-file transfer in experience metadata.
7. F4 and X4 require separate independent data/science and UI/accessibility/
   agent verdicts. A later production release requires its own bounded plan;
   documentation, local tests and owner receipts do not establish deployment.

The `depends_on` arrays and `shared_writes` in both metadata files remain in
force. Active publication, retirement, reader and multiscale gates have not been
closed here. Read their current receipts again before any future file transfer.

## Data acceptance budgets and evidence

These are **planning ceilings**, not measurements or provider promises. F0/F1
must validate them with the admitted source and record changes through review
before implementation; a failed target never permits silent widening.

| Scope | Initial acceptance ceiling / rule | Required later measurement |
| --- | --- | --- |
| Field reader | One scalar plus optional same-run wind; at most 65,536 represented cells and 4 MiB decoded response per field request. | Boundary-size requests, actual serialized/decoded bytes and selected resolution; explicit coarsening/refusal at cap. |
| Location reader | One selected location, at most 48 hourly rows and 10 daily rows per response, 16 admitted variables, 256 KiB decoded body. | Actual rows/bytes at maximum variable set; narrower source coverage disclosed; continuation for longer windows. |
| Reader memory | At most 256 MiB incremental process RSS per isolated bounded read. | Warm baseline and peak, representative dense/domain-edge/missing requests; concurrency total measured separately. |
| Provider work | Before F1 closes, fix calls/run, calls/day, concurrency, response byte cap and work-unit rows/RSS from the exact source quota and measured native partition. No generic API quota is asserted here. | Admission packet supplies numeric values and headroom; durable cooldown/Retry-After and retry budget evidence. These unresolved numbers block F1. |
| Retention | Before F1 closes, fix numeric run-count/age and storage-byte ceilings consistent with licence, cadence, active-reader lifetime and rollback window. | Size a complete run and peak staging + published + retained footprint; pinned-run expiry returns an explicit refusal. Unresolved numbers block F1. |
| Conservation and identity | Zero unexplained source/artifact count differences, mixed-run values, silent time/place substitutions or unreasoned nulls. | Source reconciliation; unit/interval fixtures; replay, interruption, rollback, missingness and run-supersession evidence. |

Run the future integrated checks once after the complete implementation batch,
using the repository's affected-test selection and applicable full quality
gates. This documentation lane requires diff/OKF/link/metadata validation and an
independent review only; it produces no runtime or Python full-quality receipt.
