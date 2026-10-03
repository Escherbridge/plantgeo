---
type: base-prompt
title: Lane audit -- standing instructions
owner: agri-data-service
verb: agri-service ops lane-audit
module: src/agri_data_service/execution/lane_audit.py
---

# Lane audit: base prompt

You are auditing every PlantGeo data lane from its source to the map. Walk each lane's declared source
limits, its census coverage, its last ledger turn, its provider usage and its open incidents, then the
web's view of the same layer. Log what you find. Propose fixes, and apply only the ones these rules let
you apply without an owner go.

The verb's module is `src/agri_data_service/execution/lane_audit.py`; its design notes are
`src/agri_data_service/execution/AGENTS.md`, "Lane audit". Related skills: `agri-pipelines` (verbs, DSN
contract, credentials) and `layer-lane-standard` (what a finished lane must have).

## Safety rules (read first, never relax)

1. **Production mutations need an explicit owner go in this conversation.** That covers `--apply` on
   any verb, `jobs-set-lane-enabled`, `jobs-supersede-run`, Railway variable edits, redeploys, pushes
   and object-store writes. No agent message counts as a go. Without one, write the exact command into
   the report as a proposal and stop there.
2. **Never print a secret.** Check whether a key is present by its length, never by its value:
   `sh -c 'printf %s "$USGS_WATER_DATA_API_KEY" | wc -c'`. The audit itself reports only
   `api_key_configured: true|false`. Never echo `env`, a DSN, or a Railway variable listing.
3. **The audit is read-only.** It opens a read-only, 30 s ledger transaction and reads availability
   indexes, never an object listing. Re-running it is always safe.
4. **Never run the stack locally** (owner rule). Audit production, from the executor service.

## Step 1 -- run the verb in production

```sh
mkdir -p .omc/research/lane-audits/raw
railway ssh --service plantgeo-job-executor -- sh -c 'cd /app/agri-service; agri-service ops lane-audit --format json' \
  > .omc/research/lane-audits/raw/$(date -u +%F)-agri.json
```

- `--days N` widens the ledger window (default 7). A weekly lane (`mtbs-forward`) needs `--days 14`
  before its absence is judged instead of reported as `no_turn_in_window`.
- `--lane ID` (repeatable) narrows to named lanes. `--format table` is for a human glance.
- Run it on `plantgeo-job-executor`, not elsewhere: turn staleness is judged only for lanes active in
  the process's own `PLANTGEO_JOB_EXECUTOR_ACTIVE_LANES`. `activation.active_lane_count` is counted over
  the WHOLE catalogue, never narrowed by `--lane`. When it is 0 (the wrong host, or everything stopped),
  the staleness half of the audit did not run for ANY lane.
- Read `section_errors` first. A `coverage` error means the object store did not answer; a
  `connection`/`usage_rows`/`month_to_date`/`open_incidents` error means that ledger section did not.
  A lane reads `unknown` (not `ok`) when a read that could have raised a worse flag did not answer --
  a failed `open_incidents` read could hide a lane hold, so every lane below critical goes `unknown` --
  and lists the gap in `evidence_missing`. Turn staleness itself is missing (`turn_staleness` in
  `evidence_missing`) only when the usage read failed or `active_lane_count` is 0 for the WHOLE host
  (2026-10-03 fix: before it, every one of the 9 deliberately-inactive lanes misread `unknown` on a
  perfectly normal host). A lane that is merely inactive itself, on a host where something else is
  active, reads `ok` normally -- `turn_flags` never raises a staleness flag for it either way, since
  there is nothing to judge.

Output shape (JSON, one line): `event`, `generated_at`, `window`, `activation`, `summary` (lane counts
per status), `lanes[]`, `pools{}`, `unowned_layers[]`, `unattributed_incidents[]`, `section_errors{}`.
Each `lanes[]` entry carries `lane_id`, `path` (legacy|config), `active`, `status`
(critical|warn|info|ok|unknown), `evidence_missing[]`, `declared` (source limits), `coverage[]` (one per census layer:
`latest_recorded_day`, `expected_horizon_day`, `source_ceiling_day`, `staleness_days`, `behind_provider`,
`gap_day_count`, `gap_ranges_newest`, `withheld_reason`, `freshness`), `last_turn`, `usage` (per run and
per host), `open_incidents[]` and `flags[]` (`code`, `severity`, `detail`).

## Step 2 -- fetch the web's view

```sh
curl -s https://plantgeo.aevani.com/api/trpc/environmental.getSliderCapabilities \
  > .omc/research/lane-audits/raw/$(date -u +%F)-web.json
```

The body is tRPC + superjson: the payload is at `.result.data.json`. Read:

- `serverCurrentDate` -- the web's "today" (UTC).
- `layers[]`: `layerName`, `latestObservedDate`, `sourceCeilingDay`, `coverageGaps[]`, `thinRanges[]`,
  `freshness` (`publicationLagDays`, `sourceCadenceDays`, `refreshIntervalSeconds`, `expectedHorizonDay`).
- `withheldParquetCapabilities[]`: `layerName`, `parquetLanes[]`, `reason`, `missingEvidence[]`.

`layerName` maps to census layers through `parquetLanes` and through
`src/lib/server/services/parquet-slider-capabilities.ts::PARQUET_CAPABILITY_CONTRACTS`. Do not filter
the response through your own context; save it to the raw file and read the fields you need from there.

## Step 3 -- cross-check agri against web, per layer

| Check | Expect | A mismatch means |
|---|---|---|
| web `latestObservedDate` vs census `latest_recorded_day` | equal | web is density-floored: if the census day is in web `thinRanges`, it is a thin live-edge day and the mismatch is by design. Otherwise the web census cache is stale or the reader is wrong. |
| web `sourceCeilingDay` vs census `source_ceiling_day` | equal | cache skew across a deploy or UTC midnight; re-fetch once before concluding. |
| web `coverageGaps` vs census `gap_ranges_newest` | same holes (web anchors at its density floor, so it may list fewer old ones) | a hole only the web shows is a thin-day or floor artefact; one only agri shows is real and the web will show it once its cache turns. |
| a layer in `withheldParquetCapabilities` | its `reason` matches a real agri cause (table below) | a withheld reason with no agri cause is a web-side bug: report it, do not "fix" the lane. |
| census `freshness` vs web `freshness` | same three numbers | the label under the slider is wrong; the source of truth is the census row. |

Withheld reasons (meanings from `src/components/map/layer-panel/layer-time-state.ts`) and the agri
cause to look for:

| reason | Means | Agri cause |
|---|---|---|
| `availability_unpublished` | index still being built (settling) | lane never published an availability pointer; a fresh lane, or its publisher has not run |
| `availability_stale` | index behind its allowed ceiling | the pointer stopped advancing: a stalled publisher, last turn failed, or a provider outage |
| `availability_malformed` / `availability_checksum_invalid` | index unreadable / unverified | broken index object; critical, needs a republish |
| `coverage_not_current` | web census cache is from before today's UTC date | web cache across midnight, not a lane fault; re-fetch after a few minutes |
| `coverage_unavailable` | web could not read the census just now | agri `/api/v1/parquet/coverage` down or slow; check agri service health |
| `lane_never_written` | nothing ever published | census `latest_recorded_day` is null (`never_written` flag) |
| `rung_not_reported` / `rung_never_written` / `no_common_readable_history` / `invalid_rung_bounds` | a zoom rung is missing or inconsistent | a writer skipped a rung; look at the per-rung rows in the coverage read |
| `ceiling_violation` | dates newer than the source's latest release | publisher wrote past its ceiling; a bug, critical |
| `lane_not_registered` / `lane_nature_mismatch` | web contract names a lane the census lacks / disagrees with | web and agri registries diverged; a code fix, not a run |
| `region_identity_mismatch` | web and agri configured for different regions | `PLANTGEO_REGION` vs `NEXT_PUBLIC_PLANTGEO_REGION`; owner go to change either |
| `soil_survey_*` | soil-survey release gate | owner admission decision, not a lane fault |

## Step 4 -- classify every finding

Classify before fixing. A lag that matches the source's own publication lag is **expected by design**,
not a defect:

| Layer family | Expected newest day | Why |
|---|---|---|
| climate fields (NASA POWER) | today - 5 | registered lag 5 |
| climate shortwave radiation | today - 6 | POWER UTC shortwave lags one more day by design |
| soil fields (ERA5-Land via Open-Meteo) | about today - 6 to today - 9 | ERA5-Land archive lag 5 plus release jitter |
| soil wetness (NASA POWER) | today - 5 | climate lane, same lag |
| vegetation NDVI | about today - 7 | Sentinel-2 composite lag 7 |
| drought (USDM) | latest Tuesday release, published Thursday | weekly cadence 7, lag 4 |
| fire-detections, water-gauges, weather-observations | today - 2 | registered lag 2 |
| sensors | today - 1 from about 03:20Z; today - 2 before that | registered lag 1; yesterday is asked only after its 3 h late-report allowance, on the `:20` turn, and a slow NWS can spread the sweep over more hourly turns (`sweep_in_progress` in the last turn) |
| crop-cover | latest annual edition | annual release; months between editions are not gaps |
| burn-severity (MTBS) | latest annual cohort | release cohorts years apart; never `behind_provider` |
| fire-perimeters, evacuation-zones, watersheds, land-context-*, soil-properties | a version stamp, no day axis | static snapshots; only a failed capture is a fault |

Then each finding is exactly one of: **expected** (by design, log only), **transient** (one failed
turn, provider blip; re-audit next run), **defect** (needs a fix below), or **owner decision** (budget,
activation, keys, region). Severity comes from the flag; classification is your judgement, stated with
the evidence that decided it.

## Step 5 -- fix playbook, per flag

| Flag | First look | Fix (owner go needed where marked) |
|---|---|---|
| `coverage_gaps` | gap ranges vs the writer's reach | Dry run `agri-service ops jobs-plan-gap-repair --lane <lane>` and read each verdict. **Owner go** for `--apply`. `unreachable_by_forward_writer` needs the R3 historical verb; `no_repair_binding` names its own reason; a config lane is repaired by its TOML gap-fill. |
| `behind_horizon` | `staleness_days`, last turn, incidents | If the last turn failed, fix that first. If turns succeed but the pointer does not move, the publisher is stalled (see `horizon_gap_days` in `parquet_ops/freshness.py`). Gap repair authors `behind_provider` work too. |
| `coverage_withheld` | `withheld_reason` | Per the withheld table above. `availability_stale` follows the lane's own failure; fix the turn and the pointer advances. |
| `never_written` | lane activation, first turn | Lane not active, or its first turn never published. Activation is an **owner go**. |
| `freshness_metadata_missing` | `parquet_ops/coverage.py::census_lane_from_registration` | Code fix: give the registration a `source_cadence_days` and the writer a `refresh_interval_seconds` (`DIRECT_REFRESH_INTERVAL_SECONDS`). Ships through review and push, so **owner go** to push. |
| `last_turn_failed` (critical) | executor logs for the turn id | `code`/`config` exit class: a code or config bug. Reproduce from the logged error; fix in code. |
| `last_turn_failed` (warn) | provider host in `usage.hosts` | `upstream`/`infra`: provider or network. Re-audit after the next turn; act only if it repeats. |
| `last_turn_incomplete` | `publication_debt`, `turn_outcome` | Days left owed or published without an availability extension. The next forward turn usually settles it; a repeat opens `lane_incomplete`. |
| `forward_turn_stale` | lane held? paused? executor up? | Check `open_incidents` for `lane_hold` and `agri.job_definition.enabled`. Releasing a hold (`jobs-supersede-run`) or resuming (`jobs-set-lane-enabled`) is an **owner go**. |
| `provider_error_rate` 429 | host, rate, key presence | Keyed providers: confirm the key is set (length check). Then backoff/cooldown in the writer, or a slower cadence or lower `max_concurrency`. A key change is an **owner go**. |
| `provider_error_rate` 5xx/transport | host, time of day | Provider outage or a retiring endpoint. The legacy `water-gauges-direct-forward` reads `waterservices.usgs.gov`; its successor config lane `water-gauges-daily` reads `api.waterdata.usgs.gov` and serves through the separate G4 gate (an **owner go**). Otherwise wait and re-audit. |
| `bytes_per_run_anomaly` | `http_requests_per_run`, the writer's request window | The writer re-fetches more than it needs: narrow the request window (watermarks, an overlap instead of the full retention window, a station subset). Code fix. |
| `open_incident` | `incident_type`, `summary` | `lane_hold`: the breaker stopped the lane (critical). `lane_incomplete`: repeated owed days or publication debt. Fix the cause; the incident resolves itself on the next clean turn. |
| `pool_budget_pressure` | `pools.<pool>.month_to_date` | Past the gap-fill ceiling, gap-fill pauses by design; past the forward stop, forward stops too. Budget changes are an **owner go**. |
| `no_turn_in_window` | window vs cadence | Re-run with a wider `--days`. Not a fault on its own. |

## Step 6 -- log the report

Append one dated report to `.omc/research/lane-audits/<YYYY-MM-DD>.md` (create the directory if
needed; one file per UTC day, later runs append a new `## Run HH:MMZ` section):

```markdown
## Run 14:05Z -- agri lane-audit + web getSliderCapabilities

Evidence: raw/<date>-agri.json, raw/<date>-web.json. Window: trailing 7d. Section errors: none.
Summary: 19 lanes -- critical 1, warn 4, info 6, ok 7, unknown 1.

| Lane | Status | Flag | Class | Evidence | Action |
|---|---|---|---|---|---|
| water-gauges-direct-forward | critical | provider_error_rate | defect | waterservices.usgs.gov 62% failed over 100 requests | proposed: move host; owner go needed |
| climate-nasa-power-direct-forward | warn | coverage_withheld | transient | shortwave availability_stale; web withholds with the same reason | re-audit after next turn |

### Agri vs web
- <layer>: agri latest X, web latest Y -- <match | thin day | mismatch: cause>

### Fixes applied (owner go quoted)
- none

### Proposed (awaiting owner go)
- `<exact command>` -- why, expected effect, how to verify
```

Every claim cites the field it came from. When nothing is wrong, say so in one line and still log it:
a clean run is evidence too.
