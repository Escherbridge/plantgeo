---
type: evidence
status: active
recorded_on: 2026-09-15
---

# Session 20: Stale lanes, freshness instrument, false claims and the land-context spine

Starts from `57879289`. The owner redirected the run to three symptoms — herbaria not showing or
barely covering, land-context layers drawing nothing, and layer dates not reaching the current
day, climate especially — and asked for a multi-parallel long-horizon execution on `fable`, with
production mutations held for an explicit go. This record fixes the diagnoses and decisions; the
verification, review and deployment sections are appended at the join.

## Diagnoses, each verified live or in source

### Dates: three causes, only two are defects

Live `getSliderCapabilities` (server date 2026-09-15) placed nearly every lane at
`latestObservedDate == sourceCeilingDay`. That equality is near-tautological:
`parquet_ops/availability_coverage.py` reads the ceiling off the availability pointer, which
advances only when a day publishes, so a stalled publisher and a healthy one report identically.
Climate 5 d, soil 9 d and vegetation 7 d are real provider edges (NASA POWER, ERA5-Land,
Sentinel-2) and cannot reach today from archive products; `weather-observations` reaches today
because it is a live instant poll with no history, a different product rather than faster data.

`climate-field-shortwave-radiation` (107 d) was local. Its constant
`CLIMATE_SHORTWAVE_RADIATION_PUBLICATION_LAG_DAYS = 75` carried a comment reading "NOT measured
against the POWER live edge". Two independent live queries of NASA POWER, five PNW points, found
`ALLSKY_SFC_SW_DWN` real through 2026-09-11 with zero interior holes June–August, one day behind
`T2M`; the 2026-05-31 boundary was a property of a stale snapshot artifact. Separately,
`climate/forward.py` reported `published` on turns that wrote nothing when the only selected day
returned `source_unsettled`, which is the mechanism that hid the stall behind a green tick.
An independent review of the first correction returned CHANGES-REQUIRED on two points that the
revision lane owns: a 6-day lag exposes partial-fill cells to permanent silent loss (a cell can
trail the release edge by 13 days and be revised in, but a day stamped complete is never
re-selected), and the diagnosis did not explain why 32 already-eligible June days never wrote
under the old lag, so a production turn report is required before deploy.

`fire-perimeters` (11 d) is not in the live `PLANTGEO_JOB_EXECUTOR_ACTIVE_LANES` (11 lanes,
read from Railway today) and runs in shadow, which spawns no process and produces no evidence.
Before activating, the live WFIGS `_Current` feed clipped to `-125,42,-111,49` was probed with
DuckDB spatial: **99 perimeters, 41 invalid** (2 Polygon, 39 MultiPolygon); `ST_MakeValid`
repairs all 41 with geometry types preserved and area change from 0 to −22.6 %. The direct writer
refuses the whole snapshot on the first invalid polygon, justified by a comment citing a drizzle
file that no longer exists, while the baseline trigger that produced every existing version
repaired and flagged them and the active sibling `evacuation-zones` runs the same repair chain.
Activating as-is would fail every tick and latch after three.

`sensors` (held 7 d) was not an upstream failure. The 2026-09-09 deployment's own logs show NWS
answering 66,705 records with zero rejected; two of seven days (2026-09-05/06) carried
governed-absence markers written by the retired PostgreSQL adapter, the direct writer correctly
refused to overwrite them, and `sensors/forward.py` failed the whole bucket because two days
refused. About eleven hours of readings are permanently past NWS retention; roughly six days
remain recoverable by one poll. The tick logged `tick_unhealthy` with the exact remediation
command every 30 s for seven days and nothing consumed it. The owner chose to fix the code
first and release the breaker after deploy, accepting the hourly loss.

`watersheds` (39 d, `earliest == latest`) is a `static_lookup` whose single version day is
NHDPlus_HR's own load date; age here is release vintage, not staleness.

There is no Railway cron. `plantgeo-job-executor` is a poller with hardcoded crontab strings per
lane gated by the allow-list; generic gap-repair schedules were deleted 2026-09-12, so measured
gaps have had no owner since; the executor spawns lane commands with inherited stderr, so the
job ledger records only "exited with status 1".

### Herbaria: coverage is not a serving bug, and the instrument is wrong for the goal

The committed manifest receipt for generation `956c0be7…` reads `raw_occurrences 192,948`,
`occurrences 15,220`, `nonspatial_occurrences 177,728`: 92 % of the UBC release has no usable
coordinates, and the mappable remainder clusters near Vancouver (994 in one z11 bbox; Boise a
true zero). The Vancouver cluster (~49.26 °N) lies outside the platform envelope
`PACIFIC_NORTHWEST_COVERAGE_BBOX = "-125,42,-111,49"`; botanical uses its own wider envelope to
50 °N. Herbarium points cannot produce a vegetation-type map. The owner's call: stop acquiring
(no WTU), keep UBC serving, correct the "Documented Taxon Richness" framing so collection
intensity is not read as biodiversity.

`getLandFireEVT` in `src/lib/server/services/landfire.ts` had zero callers and was wrong three
ways, verified live: host HTTP 404, fuel-model table in the FBFM40 code space (91–204) against
EVT values (7008–9829), and a non-existent `EVT_NAME` attribute. The About page advertised it
"On request", as it did SSURGO and NHDPlus. A vegetation-type lane is designed at
`.omc/research/runbook-20260915-vegetation-type/PLAN.md`; §8 records the owner's decisions
(hybrid grain — composition lattice at coarse rungs, raster at fine rungs as SoilGrids; raise
`MAX_DERIVATION_ROWS` after its own cost analysis rather than accept ~800 m; no track until the
stale lanes deploy).

### Land context: a façade over zero rows, plus a bug nobody had named

`landContext.coverageStatus` and `resolveBoundaryAtPoint` return 200 with
`"no Parquet lane wired in yet; reference plane not yet admitted for reads"`, confirmed live.
The schema exists in production Postgres, the WKB decoder is written and unit-tested, and no
producer exists anywhere; only BLM is rights-cleared to acquire. The only writers of
`setSelection` were a click handler registered on the land-context fill layers themselves and a
mobile action that renders `null` without a selection: features draw only after a selection, and
a selection could only be made by clicking a drawn feature. Click-driven selection is the spec
(`pnw_land_contact_experience_20260911/spec.md:78-79`, "never substitute the viewport centre"),
so viewport rendering is a product change, not a fix; the deadlock and the silent toggles are
bugs under any product model.

## Lanes and partitions

| Lane | Partition | Change |
| --- | --- | --- |
| A1 sensors | `pipeline/direct/sensors/**` + tests | `absent` joins `missing` after merge validation; every-tier retraction with preserved provenance; exit 1 only when no day wrote |
| A2 shortwave | `pipeline/direct/climate/**`, `pipeline/direct/AGENTS.md`, `lane_registry.py`, tests | lag 75→measured; honest product outcome; review's two majors and four minors; production turn report |
| A3 freshness | `parquet_ops/**`, `execution/**`, tests | independent measured horizon beside the pointer ceiling; bounded source-direct gap-repair registration; stderr tail into the ledger |
| A4 fire-perimeters (read-only) | — | activation verdict and procedure |
| A5 About | `src/app/about/page.tsx`, `landfire.ts` | LANDFIRE row removed, SSURGO reworded to withheld, NHDPlus scoped to HUC12; module deleted |
| A6 land-context | `components/map/land-context/**`, `services/land-context/**`, store, router | bare canvas click enters selection; server-side WKB decode; per-family coverage notices |
| A7 fire-perimeters | `pipeline/direct/fire_perimeters/**` + tests | repair-and-flag geometry chain matching baseline trigger and evacuation-zones |
| A8 soil claims | `SoilDetails.tsx`, soil rows of `layer-registry.ts` | stop blaming USDA for an unpublished lane; truthful click copy |
| A9 weather/water | `pipeline/direct/weather_observations/**`, water-gauges writer + tests | same absent-refusal and all-or-nothing exit class as sensors |
| Coordinator | `MapView.tsx`, `LandContextPanel.tsx`, `map/AGENTS.md`, `docs/services.md` | canvas-click stand-down for the agent popup; truthful empty-panel copy; stale landfire references |

Authors ran no suite, lint or type checking; each predicted failures. One integrated sweep
(Python quality gates via the receipt workflow, frontend boundary/type/lint/vitest) follows the
complete batch, then independent review in a separate context, a source-identity hash check
against the reviewed bytes, and a deploy bound to the exact commit. Production mutations —
sensors breaker supersession and the fire-perimeters allow-list — wait for the owner's go after
the fixed code is live.

**No whole QA case and no runbook checklist item is promoted by this session's diagnoses.**

## Live state re-measured 2026-09-18

The owner reported that many layers had stopped ingesting for several days and that no date
receipts existed. Measured against that claim (evidence retained under
`.omc/research/runbook-20260915-session20/live-0918/`): every daily lane advanced exactly three
days in the three days since 2026-09-15 — climate ×7 to 2026-09-13 (lag 5, provider edge), soil
×3 to 2026-09-09 (lag 9), vegetation to 2026-09-11 (lag 7), fire-detections to 2026-09-16 (lag 2);
weather-observations, water-gauges and drought-areas sit at 2026-09-18. A 3,000-line capture of the
executor stream (01:02→07:20Z) shows hourly completions: climate 06:41, soil 06:50, vegetation
07:05 (`idempotent_noop`, nothing new at the edge), weather 06:31, water-gauges 07:18 after a
07:17 upstream timeout that the retry policy absorbed. The executor is therefore running at its
scheduled cadence, and lane A3 retracted the once-per-UTC-day reading with evidence: Railway
parses a JSON stdout line into structured fields with an empty `message`, so text greps over
`message` matched only the long 00:43Z turns; the job ledger shows a distinct `succeeded` run
for every hourly bucket. The +3 days in 3 days is a lag-bound daily lane advancing one settled
day per day. `tests/execution/test_lane_cadence.py` now pins that a succeeded bucket never
blocks the next.

Genuinely stopped: `sensors` — absent from the capability list entirely, `/api/v1/parquet/coverage`
reporting `withheld_reason: availability_stale` with null bounds at every rung, `tick_unhealthy`
naming it as the sole failing lane at 07:20Z; the code fix is in the tree and the breaker
supersession waits on deploy and the owner's go. `climate-field-shortwave-radiation` remains at
2026-05-31 (fix in tree). `fire-perimeters` remains at 2026-09-04 in shadow (fix in tree).
`burn-severity` has not moved off 2026-09-11 in seven days while its daily bucket reports
"already settled with status succeeded"; whether that is a genuine no-new-MTBS-capture or a
succeeded-without-publishing mask: A3 found it is the designed no-new-release path —
`burn_severity/stage.py:86` captures at most every 7 days and `daily.py:120` records
`checked_unchanged` when MTBS's content sha is unchanged. Not a stall.

The owner's "no date receipts" is accurate in a precise sense: per-lane completion reports exist
only in the transient Railway log stream, and the executor spawns lane commands with inherited
stderr so the job ledger records nothing beyond an exit status. Lane A3's stderr-to-ledger change
and the independent freshness horizon are the durable answer; the self-healing backfill
requirement the owner added is assigned to A3's gap-repair registration.

## Open owner decision — fire-perimeter repair audit durability

The A7 review accepted the repair-and-flag chain but named a fidelity regression relative to the
baseline: the PostGIS trigger persisted `geometry_repaired: true` in each repaired feature's
properties, whereas the direct writer's per-row flag and planar area ratio live only in the turn
report and a stderr event, because a real Parquet column would move three frozen contracts at
once (the L1 schema, the `.strict()` `firePerimeterRowSchema` in `parquet-trpc-readers.ts`, and
the schema-derived `DIGESTED_COLUMNS` whose encoder refuses booleans). Consequence: a perimeter
whose planar area moved up to −22.6 % on repair — usually overlapping or self-intersecting parts
being unioned, not lost ground — is served to the map and the agent tools with no marker. The
reviewer judged activation acceptable **provided the trade is recorded as an owner decision and
the three-contract column follow-up is ticketed**. Recommendation: accept for activation now
(the alternative is a lane that cannot publish at all, 41 of 99 live perimeters being invalid),
and charter the nullable `geometry_repaired` column as its own small reviewed change. Awaiting
the owner's word; nothing activates before deploy and the explicit go in any case.

## Review ledger (in progress, 2026-09-18)

Nine lanes were terminated mid-flight by a session rate limit on the night of 2026-09-15; the
authors were resumed from their transcripts and the reviews relaunched. Verdicts so far, each
from a separate context that authored none of the code under review:

| Lane | Verdict | Load-bearing findings |
| --- | --- | --- |
| A1 sensors | CHANGES-REQUIRED → fixed → **APPROVE** on re-review (155 passed / 5 skipped) | `outcome=incomplete` at exit 0 had no consumer (executor keys on return code, tees stderr only on failure, nothing reads `days_unwritten`); docstring asserted a monitor that does not exist. Fixed: honest docstring, `sensors_forward_bucket_incomplete` stderr event, partial-clear retry test with unioned tier report. The consumer itself is assigned to A3. |
| A2 shortwave | CHANGES-REQUIRED → fixed → **APPROVE** on re-review (105 passed). Carried follow-ups: a recheck of a published day that comes back all-fill routes through the absence branch before the unchanged gate and reports `raised` each visit (fail-closed, data safe); clock-hour rotation can skip one recheck when exactly 24 are queued. | 429 root cause, pause-join logic and the honest-outcome fix verified. Majors: the partial-day recheck rewrote an unchanged day every idle turn and made it `incomplete` to window reads while doing so; newest-first single-slot selection starves older rechecks in the trailing-cell regime. Also: eleven climate products, not eight. |
| A5 About + landfire / A8 soil / A10 richness | APPROVE (A8, A10); A5 follow-ups delivered (51/51), re-review pending | Every changed sentence traced to a live path; deletion clean; no-gate call on `soil-survey` concurred. Applying the method to untested rows found two more false claims: Open-Meteo "live reading behind a map click" (reads a published sample at the viewport centre), Sentinel-2 "Daily, 05:00 UTC" (hourly cron). Provider-fault branch in `SoilDetails` is dead code. |
| A6 land-context | **APPROVE** after five passes (112/112 on the six-file set, boundary and tsc clean; covers the coordinator's MapView dedupe). History: CHANGES-REQUIRED → fixed → re-review found a **CRITICAL** in the fix: `isScalarFieldInspectionAllowed` is `!suppressed.has(id)`, true for any unsuppressed layer including the basemap `earth`/`water` fills, so the shared predicate vetoed every land pixel and reinstated the deadlock; fixtures passed only by modelling ground as "no rendered features". Fixing: explicit owning-layer set, land-context pick before the ownership check, `earth` in fixtures. Coordinator decision: MapView keeps the shared predicate, so the agent popup's reach over plain ground changes — to be flagged at deploy | The bare canvas click listener stands down for no other click owner (soil pin capture, drawing-tool vertices, intervention/scalar features) — "one click, two meanings" reintroduced on the land-context side. Wire budget estimated before decoded geometry is attached (~2×). The coordinator's MapView stand-down had no test and its edit had flipped the file to LF (restored to CRLF). |
| A7 fire-perimeters | CHANGES-REQUIRED → fixed → re-review found a **CRITICAL** the tests could not see: the publish path still called `_report(population_rows=…)`, so the first activated tick would have written its version and crashed before its report, latching after three. Fixed as a one-line integrator edit → **APPROVE** on confirmation (mypy on the package clean, ruff clean, 132 passed / 5 skipped). The receipt gate's mypy invocation is `mypy src scripts` (whole tree), so the `[call-arg]` class is covered on every commit. Audit-durability trade stands as the recorded owner decision. | Logic could not be broken with the fixture or adversarial shapes; the report/event surface declared the only audit trail had zero tests. Audit-durability trade recorded above as an owner decision. |

Provenance correction found by review and confirmed in evidence: the served UBC v16.43 archive was
acquired from the Canadensys IPT (`ubc-permission-manifest.json`:
`data.canadensys.net/ipt/archive.do?r=ubc-vascular-specimens&v=16.43`), not from the Consortium
of Pacific Northwest Herbaria portal that the About page credited; the portal lists UBC as a
provider, and the portal URLs in the evidence all belong to the deferred WTU collection. The About
attribution is being corrected to name the actual source.

A9's finding, recorded: for weather-observations and water-gauges the absent-marker branch is
closure of the defect class rather than a live repair (the retired walkers' windows never
overlapped the forward writers'), but the all-or-nothing exit was live on water-gauges — one stale
gauge `updatedAt` dragging an old day into the bucket failed every other day's clean write.
`DirectWaterGaugesForwardAdapter` previously had no test coverage at all.

A9 (weather-observations + water-gauges) delivered its follow-ups (47 passed): the false monitor
sentence removed from both forwards, `<lane>_forward_bucket_incomplete` stderr events, the
rolling-window property that makes exit-0-on-any-written sound stated in both docstrings, and
partial-clear retry tests in both adapters. Its retraction event is being aligned to A1's shape
(union on retry, last event is the truth) before its independent review. The `landfire-fuels`
entry in `ingest/validation/source_manifests.py:351-362` is a `planned` interventions-plane
catalogue row, not a public claim, and is left in place.

Lesson recorded from A7: the new forward tests exercised `_report` by calling it directly, which
pins the definition but not its call sites inside `run_fire_perimeters_forward` (the turn always
fetches WFIGS first, so the tests cannot drive it). `mypy` caught the drift in one run; the
integrated sweep's mypy gate is the guard, and a lane whose turn cannot run offline should be
reviewed with mypy on the module, not tests alone. A9's first independent review is running.

A3 delivered (729 targeted tests, ruff clean, mypy not run by the author — the reviewer runs it):
independent freshness horizon kept off the frozen wire (`expected_horizon_day`, `staleness_days`,
`behind_provider` on the dataclass, excluded from `to_wire()`, with the exact freeze move written
in `parquet_ops/AGENTS.md`); bounded source-direct gap repair with `REPAIR_BINDINGS` for 23 census
layers and named exclusions, authored autonomously by a 6-hour `RepairAuthoringClock`; child
stdout/stderr teed into the ledger with exit-0 incomplete turns parsed onto the checkpoint;
`ProcessStartRelease` recording one real supersession per breaker-held run at process start
(env-disableable). The cross-file CLI registration `jobs-plan-gap-repair` was added by the
coordinator in `interface/cli/ops.py` and the module imports cleanly. **Two deploy-gate decisions
for the owner:** whether `ProcessStartRelease` ships enabled (it would release the sensors breaker
on deploy without the explicit go previously promised), and the MapView popup reach change above.

A9 (weather-observations + water-gauges): **APPROVE** on first review (163 passed / 5 skipped);
behavior-preserving on every path that succeeds today, the only live change being water-gauges no
longer exiting 1 when one publisher day refuses. Its MEDIUM was a cross-lane staleness: the three
forwards said "no consumer of the stdout report exists yet" while A3's `TurnReport` in the same
tree is that consumer. The coordinator aligned all three docstrings (sensors, weather, water) to
name the consumer and the reason the stderr event is kept, and added A9's clause that weather's
yesterday bucket is re-offered only within `MAX_OBSERVATION_AGE` of UTC midnight. The edits are
prose only; all three modules import and format clean.

A6 third round delivered (17 files / 118 tests, tsc clean): the click-owning set is now the
codebase's own `HOVERABLE_LAYER_IDS` registry (`hover-fields.ts:23-59`), tested membership-first
and suppression-second so basemap fills can never own a click; the drawn land-context pick runs
before the ownership check; every click fixture now carries an `earth` feature. The author's
before/after for MapView — that at HEAD the left-click agent popup was reachable only where no
rendered feature existed, i.e. off-globe, because the unfiltered `earth`/`water` fills sit under
every pixel and were never suppressed — is being independently verified before it is stated to
the owner as fact. Third-pass review pending.

A3 review: **CHANGES-REQUIRED** (303 tests pass, mypy clean on 71 files) with two HIGHs on the
autonomous parts. H1: the self-healing path authored nothing for a stalled publisher, because
availability gaps are closed against the lane's own `source_ceiling` and so a lane that stopped
advancing has no gaps by construction — the reviewer ran A3's own shortwave fixture through the
selector and got `complete`; `behind_provider` must authorize. H2: `ProcessStartRelease`
qualified on bucket time, granting two buckets mid-hour, and under container restarts (a child
OOM is itself a lane fault) it would release every held run on every restart, degrading the
breaker to pre-breaker behavior; the deployment id was in the evidence string only. **Coordinator
decision: `PLANTGEO_JOB_EXECUTOR_PROCESS_START_RELEASES_BREAKER` ships OFF**, so the sensors
release remains the explicit CLI supersession the owner was promised, and the auto-release becomes
an opt-in once fixed (bucket strictly before the start bucket; durable once-per-deployment).
Mediums: a ledger error could abort the planning tick; two persistently unfillable layers could
starve the other nine; repair turns polluted the forward lane's incomplete streak; `turn_report`
reached the checkpoint cursor unredacted; burn-severity reads permanently behind-provider. All
sent back to the author.

A6 third pass: CRITICAL resolved; **CHANGES-REQUIRED narrowly** — the two GBIF occurrence point
layers register clicks but sit outside `HOVERABLE_LAYER_IDS`, the only miss among every click
registrant in `src/`. Also directed as correctness: 13 registry layers own a click only on touch
(HoverTooltip's tap-pin is a no-op with a mouse), so arm 3 now mirrors HoverTooltip's own
pointer gate rather than vetoing desktop clicks over drought/watershed fills. The HEAD claim about
the agent popup is qualified for the record: on the two vector basemaps it fired only beyond the
PNW extract; on the satellite style it worked everywhere except under a place label.

A6 fourth pass: design approved (dedicated owners always own; tap-only layers own on coarse
pointers only; suppression last; the drawn land-context pick first); **CHANGES-REQUIRED** on two
small items — a committed `map-view-render-count` test pinning the old rule (a `sensors` point
owning a desktop click) is now red and must be rewritten to the new rule, and the three botanical
occurrence point layers were misclassified as tap-only for the same reason GBIF was (real click
handlers, never added to the registry's private dedicated six). The reviewer classified every
`map.on("click", …)` registrant in `src/`; after these two, nothing else is uncovered except the
documented drawing-tool gap. **Owner-facing behavior change, now concrete for the deploy gate:**
on desktop, a left click over a sensors point or a drought/watershed/weather fill — layers whose
only click behavior is a touch tap-pin — will open the agent popup (and, with a land-context
group on, make the point selection), where at HEAD on the vector basemaps it did nothing.

A3 fix round delivered (865 passed / 1 skipped / 1 xfailed; ruff and mypy clean): a stalled
but gapless lane now authorizes under a distinct `behind_provider` verdict spanning
`latest_recorded_day + 1 … expected_horizon_day`; the process-start release qualifies only on a
bucket strictly before the start bucket, is durably once-per-deployment through one
`agri.job_incident` row per `(deployment, lane)` written via the existing supersession SQL, and
**defaults to OFF**; a ledger fault during release rolls back and holds without aborting the
tick; repair authoring rotates (24 h) so two unfillable layers cannot starve the rest; repair
turns count against their own `:gap-repair` definition; unwritten-day details are redacted before
the checkpoint; irregular release cohorts state staleness but are never flagged behind. Re-review
pending. A6 fifth round delivered (six-file set 112 passed, remaining 13 files 81 passed, project
`tsc` re-run by the coordinator: clean); review pending. Once both approve, the single integrated
sweep runs over the complete batch.

A6 final: the click-owning sets are `CLICK_HANDLER_IDS_OUTSIDE_THE_SIX` (two GBIF + three
botanical point ids, registrants named), `TAP_ONLY = TOOLTIP_TAP minus those`, and
`DEDICATED = registry-minus-tap ∪ those`; the reviewer verified the set arithmetic cannot drop the
GBIF ids (they enter only via the explicit spread) and that the two botanical fills stay tap-only.
Carried follow-ups (non-blocking): move three inline `matchMedia` stub teardowns into `afterEach`;
hoist the duplicated `isCoarsePointer` into `src/lib/map/pointer.ts` when both callers are in
scope; widen `LAYER_IDS_WITH_A_DEDICATED_CLICK_POPUP` in `hover-fields.ts` so HoverTooltip stops
double-owning the botanical points on touch; drawing tools still publish no capture flag.

A3 re-review: **APPROVE** (313 passed on the requested dirs, mypy clean on 71 files). Every
round-one finding is closed in source: a behind-but-gapless lane authors a `behind_provider`
turn whose span is evidence only — the writer selects its own days from its own registered lag,
so the planner cannot manufacture a wrong absence; the process-start release is opt-in, keyed on
a bucket strictly before the start bucket, durable per `(deployment, lane)` through a
distinct incident-fingerprint namespace that no other reader can confuse with a run supersession,
and one transaction (a crash between the two statements loses both). The reviewer is explicitly
comfortable shipping it OFF beside the owner's CLI supersession for sensors, and recommends
keeping it OFF until one deployment cycle of autonomous repair authoring has been observed so the
two autonomous behaviors are not switched on in the same release. Carried follow-ups: a
per-layer authoring count so a layer re-authored for weeks is distinguishable from one authored
yesterday; a parity test between `CensusLane.publication_lag_days` and the writer's product lag;
document that an unset `RAILWAY_DEPLOYMENT_ID` collapses the marker key to `local` (one release
per lane ever on that ledger — the conservative direction); prefer an explicit
`irregular_cohorts` registration flag over inferring it from `cadence_days == 1`.

**All nine partitions independently approved.** The single integrated sweep over the complete
batch follows.

## Sweep, commit and the Linux receipt gate (2026-09-18)

The single integrated sweep over the complete batch: Python format, lint, mypy and full pytest
passed after eight lint findings were fixed at the sweep (three by `ruff --fix`, five by hand:
a `PLR0911` noqa with reason on the reviewed seven-return `_publish_locked_day`, two
annotation-only imports moved under `TYPE_CHECKING`, two compound asserts split); frontend
data-boundary, type check and lint passed; the frontend suite passed **201 files / 2,676 tests**.
The batch was committed as `a17650b4` from an explicit 96-path allowlist plus the three
coordinator records (98 staged; the concurrent session's 15 untracked evidence paths and
`.agentgraph/` deliberately left untracked). The pre-commit hook rewrote no source: the only byte
drift from the reviewed baseline was the coordinator's own RUNBOOK sweep line.

The Python quality receipt is produced from the committed tree inside the retained QA image by a
parameterized copy of the 2026-09-14 recovery runner. Its first run failed before creating a
container on a repo-root path computed one level too high (`parents[4]` → `parents[3]`); the
second run built the container and executed all four gates on Linux: format, lint and mypy
passed, pytest ran 4,798 with **two failures**, both in the new climate quota-pause tests, which
asserted the recorded cooldown equals the planned pause exactly and received `19.99992…`. The
sleep is `resume_at − monotonic()` two clock reads apart; Windows' ~15 ms monotonic resolution
returned identical reads and masked it, Linux' microsecond clock did not. Test-only fix
(`pytest.approx(…, abs=1e-3)`) committed as `b815e4a0`; the pause series and semantics are
unchanged. The receipt build is re-running against that commit. Lesson: a timing-derived float
must be asserted with a tolerance, and the Linux receipt gate is the place such a fixture fails.

The rebuilt receipt against `b815e4a0` passed all four gates on Linux (pytest 177.51 s), covers 900
digest inputs, and its tree digest equals the host tree's; the unchanged verifier accepted it on the
host. Receipt SHA-256 `e2b87486edd891ecb6e046e39bc1fcd440df5d6230081e9210178d35e7969e69` (1019 bytes), copied unchanged from
`agri-data-service` attempt `1364e27ef5d740bfba98a1a4a97662cd`.
