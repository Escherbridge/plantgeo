---
type: research-report
---

# PUDL energy lane: validated metadata and capability proposal

Research date: 2026-11-02 America/Denver. Research only; no application code, deployment, or production data changed. Official source metadata and public table descriptions were checked. A subsequent bounded actual-Parquet pilot validated selected 2025 WA/OR/ID hydro/biomass cohorts from pinned v2026.10.0; see [actual-row evidence](actual-row-pilot.md). That pilot supersedes the initial metadata-only assessment, but is not full-dataset certification or an implementation acceptance receipt.

## Recommendation

Admit a distinct, separately releasable US energy-context lane, initially PNW-scoped for joins to PlantGeo's existing environmental footprint. Its first release can locate energy assets, describe historical generation, and disclose exposure context. PUDL alone cannot identify water leakage, predict an outage, establish wildfire ignition causation, or establish usable hydro/biomass energy at a proposed site.

## Capability matrix

| User goal | PUDL contribution supported by metadata | Required complementary evidence | Honest agent output and boundary |
|---|---|---|---|
| Water loss prevention | Plant identity/location and experimental cooling water volumes/temperatures support water-use context at generating facilities | Water distribution/irrigation network, inflow/outflow and pressure telemetry, meters, storage change, consumptive use/evaporation, timestamps and sensor QA | Identify evidence needed for a water balance or assets in a stressed basin. Cooling withdrawal is not leakage; withdrawal minus discharge is not automatically avoidable loss. No leak localization from PUDL |
| Wildfire prevention | Generator locations, technology/fuel and operating status provide energy-asset exposure inventory | Power-line/feeder geometry, equipment condition/fault and ignition records, vegetation clearance, fuel moisture, weather and intervention evidence; biomass harvest sustainability and access for fuel-treatment ideas | Report assets near observed hazards and evidence-backed research options. Proximity is not electrical connectivity or ignition probability. No claim a biomass market or inventory prevents wildfire |
| Outage prevention | Annual utility-state reliability history and generation inventory support retrospective vulnerability context | Live outage telemetry, network topology, protection state, demand and dispatch/reserves, maintenance, restoration and validated event model | Compare historical reliability on like definitions and expose missing live data. Never present annual SAIDI/SAIFI as current outage status or property-specific outage probability |
| Hydroelectric generation | Existing hydro assets, nameplate capacity, operating status and historical generation establish baseline | Catchment linkage, flow/level/head, reservoir operations, environmental flows, water rights, turbine curves and access constraints | Historical generation and conditional scenario with explicit inputs. Gauge distance alone does not prove upstream connectivity; nameplate MW is not available energy. Keep pumped storage distinct from conventional hydro |
| Biomass energy generation | Existing biomass-capable generators, primary/secondary fuels, reported or allocated fuel consumption and generation reveal demand context | Feedstock inventory, sustainable recoverable fraction, moisture/heat value, competing uses, seasonality, transport and conversion efficiencies, emissions/permitting | Screen potential offtakers and compute a declared-input scenario. Existing capacity is not spare demand; bioenergy is not inherently carbon neutral; no feedstock availability inferred from generator fuel code |

## Candidate exact tables and keys

1. `out_eia__yearly_generators` and optional `out_eia__monthly_generators`: key `report_date, plant_id_eia, generator_id`; columns verified include `latitude`, `longitude`, `state`, `capacity_mw`, `summer_capacity_mw`, `winter_capacity_mw`, `energy_source_code_1` through `_6`, `prime_mover_code`, `technology_description`, `operational_status`, `generator_operating_date`, `generator_retirement_date`, `net_generation_mwh`, `capacity_factor`, `data_maturity`. These convenient outputs combine reported, imputed, calculated and harvested values. Do not call every row a directly measured generator observation. Latitude/longitude refer to the plant site; do not claim exact turbine or intake geometry. Prefer upstream EIA identity plus release, not potentially mutable PUDL surrogate IDs.
2. `out_eia923__monthly_generation_fuel_combined`: key `plant_id_eia, report_date, prime_mover_code, energy_source_code`. Useful columns include `net_generation_mwh`, `fuel_consumed_units`, `fuel_consumed_for_electricity_units`, `fuel_consumed_mmbtu`, `fuel_consumed_for_electricity_mmbtu`. Keep unit-bearing quantities distinct; validate the pinned schema and documentation because some field prose has inconsistent physical-unit wording. Join at table grain before aggregating to avoid multiplying plant fuel across generators.
3. `out_eia923__monthly_generation_fuel_by_generator_energy_source` is an optional derived view, not an independent measured source. Allocation provenance must survive to the response. Do not add its totals to the plant-grain combined table.
4. `core_eia861__yearly_reliability`: key `utility_id_eia, state, report_date, standard`. Verified fields include `saidi_w_major_event_days_minutes`, `saidi_wo_major_event_days_minutes`, `saifi_w_major_event_days_customers`, `saifi_wo_major_event_days_customers`, `data_maturity`; loss-of-service variants also exist. Preserve standard and event inclusion when comparing; never join a whole utility's reliability to every plant and sum it.
5. `_core_eia923__monthly_cooling_system_information`: key `plant_id_eia, report_date, cooling_id_eia`. Candidate research-only enrichment. Official documentation says cleaned but not normalized, temporarily published and removable without notice, latest described year 2025. 2008â€“09 represent annual averages while later years are monthly. Exact water-volume columns and normalization must be validated from pinned artifact schema before admission; they were not established in this pass.
6. `core_eia__codes_energy_sources` supplies classification labels and units. Pin it with the same release and use its classifications for hydro, pumped storage, wood/wood waste, biogas and other biomass rather than name matching. Multi-fuel generators must not count full capacity once per fuel.

## Agent contract

Proposed tools, names illustrative rather than existing APIs:

- `get_energy_assets_near_site`: selected day, bounded radius/bbox, technology filter; returns native identity, plant-site coordinates, actual distance, inventory reporting period, release and missing coordinate count.
- `get_energy_generation_history`: native asset ID and monthly/yearly interval; returns reported versus allocated generation/fuel, units, maturity and coverage rather than interpolated daily production.
- `get_energy_environment_context`: joins already-served environmental evidence to energy assets with individual source dates, spatial relationships and missing evidence; outputs exposure facts, not prevention efficacy.
- `get_utility_reliability_history`: utility/state and annual interval with `standard` plus major-event treatment mandatory in comparisons. No property-service assertion without independently validated utility territory mapping.

Return `requested_day`, actual reporting-period start/end, `data_available_at` when evidenced, ingestion time, pinned source release/table/checksum, nearest-before/after period and distance, `claim_kind` (reported/allocated/imputed/scenario), known caveats and explicit missing evidence. A present-day release containing 2020 revised data is not proof those values were knowable in 2020. If release/publication vintage cannot support an as-of query, refuse that interpretation instead of inventing a publication date. Monthly or annual values must not masquerade as daily observations.

Existing integration contract: `agent/tools.py` registers bounded warehouse tools; lines 1093â€“1096 require nearest-day results not be quoted as selected-day values. `src/lib/server/services/AGENTS.md:156` requires requested/served day, signed offset and spatial distance provenance. The agent bridge and map must use the same published energy release and requested-time semantics. Literature strategy tools can supply research evidence but cannot upgrade inventory/exposure facts to causal prevention claims.

## Validation gates and US-first sequence

1. Resolve one immutable stable release, record its release date and table schema descriptors/checksums. Do not bind production to mutable `nightly` or merely `stable` aliases. Reject missing/changed keys or columns; keep last-good release available.
2. Before an ingest lane is enabled, conduct a capped actual-Parquet pilot covering US hydro and biomass records, with a focused WA/OR/ID environmental join sample. Profile period coverage, non-null geometry, identifiers, duplicates and units. Measure bytes read and query latency; a remote LIMIT is not a reliable byte cap. Use object sizes/row-group metadata and explicit byte/time budgets.
3. Verify uniqueness at each declared key, no unplanned join fanout, geospatial coordinate ranges and state/bbox plausibility, classification against release-matched code tables, and operating/retirement consistency. Unknown coordinates remain unknown and are counted rather than placed at a centroid silently.
4. Reconcile aggregated generation against the input table at identical plant/prime-mover/fuel/month grain, with declared numeric tolerance. Do not blanket-reject negative net generation (pumped storage/consumption may legitimately be negative). Flag extreme capacity factors for review rather than silently clipping them. Verify MW versus MWh and fuel physical units versus MMBtu with fixture cases.
5. Exercise temporal leakage, annual/monthly boundary, out-of-coverage and nearest-neighbor refusal cases. Verify reference year never becomes a fabricated daily measurement. Verify missing dates, nulls, allocations and provisional maturity remain visible through Parquet API, agent bridge and UI.
6. Test use-case refusals: no pipe telemetry => no leak diagnosis; no line topology => no feeder connectivity; no live outage data => no current outage answer; no head/flow => no hydro output estimate; no feedstock => no available biomass energy claim.
7. Publish immutable internal Parquet plus validated availability/reference catalogue under distinct energy lane identity; promote pointer only after verification. Register independent refresh/check/report duties in the existing sole executor, initially disabled. This is independently deployable capability, not a second scheduler. Query serving must use owned validated artifacts and never trigger upstream ingest on an agent request.
8. Refresh on upstream release change with bounded metadata polling; stage revised historical partitions and audit differences. Historical backfill is an explicit bounded one-time pilot/import, not permanently scheduled history replay. Rollback only the energy pointer/lane. Keep environmental ingestion operational policy independent.

## Official references and captured evidence

- [Generator viewer](https://data.catalyst.coop/preview/pudl/out_eia__yearly_generators): metadata and field descriptions, not authenticated row preview.
- [PUDL stable dictionary](https://docs.catalyst.coop/pudl/en/stable/data_dictionaries/pudl_db.html): authoritative table grain and caveats; pin version for implementation.
- [EIA resource metadata](https://github.com/catalyst-cooperative/pudl/blob/main/src/pudl/metadata/resources/eia.py): exact generator schema.
- [EIA861 resource metadata](https://github.com/catalyst-cooperative/pudl/blob/main/src/pudl/metadata/resources/eia861.py): annual reliability key and metrics.
- [Cooling-system dictionary](https://docs.catalyst.coop/pudl/en/latest/data_dictionaries/pudl_db.html#core-eia923-monthly-cooling-system-information): experimental status and temporal caveats; latest is mutable.
- [EIA923 resource metadata](https://github.com/catalyst-cooperative/pudl/blob/main/src/pudl/metadata/resources/eia923.py): generation/fuel grain and outputs.

Raw bounded web captures are retained in the local ignored `.omc/research/` directory: `pudl-usecase-search-2026-11-02.txt`, `pudl-resource-metadata-2026-11-02.txt`, `pudl-target-metadata-2026-11-02.txt`, `pudl-water-generator-fields-2026-11-02.txt`, `pudl-viewer-probe-2026-11-02.txt`. These are discovery evidence from mutable documentation, not a pinned dataset validation receipt. The public source links above remain the reproducible source entry points in a fresh checkout.
