# Idaho Wildfire Prevention and Loss-Reduction Report

**Prepared:** July 22, 2026  
**Decision horizon:** Immediate 72 hours, remainder of the 2026 fire season, and 2027 mitigation planning  
**Scope:** Idaho; current official conditions plus PlantGeo's validated local warehouse

## Executive judgment

Idaho's wildfire-prevention posture should be elevated but spatially targeted. The
strongest current evidence supports three different operating modes:

1. **Southern Idaho — sustained prevention priority.** Official drought data show
   extensive severe-to-exceptional drought, the seasonal outlook identifies high
   fine-fuel loading and carryover, and PlantGeo's coarse climate screen shows a much
   drier, warmer, and less humid January-April 2026 than its 2023-2025 comparison.
2. **Central and north-central Idaho — rapid-transition priority.** Lightning and
   rainfall may be followed quickly by hot, dry, gusty conditions. The correct response
   is holdover-fire detection, initial-attack readiness, and protection of evacuation
   corridors rather than a static statewide risk label.
3. **North Idaho — event-triggered readiness.** Late-June rain improved the seasonal
   outlook, but short-term hot, dry, windy periods can still create critical fire-weather
   windows. Use daily weather/restriction triggers rather than maintaining the same
   posture as southern Idaho.

PlantGeo's only governed forecast run must **not** be used for Idaho decisions. It is a
rejected seven-day wind forecast for a 55,660-m NASA POWER point at 40 N, 105 W near
Denver. Only 3 of 14 hindcast origins passed and interval coverage was 63.3 percent.
It has no Idaho spatial relevance and no published output.

## Evidence snapshot

| Evidence | Finding | Decision meaning |
|---|---|---|
| NIFC National Fire News, July 22 | National Preparedness Level 5; four Idaho large fires totaling 10,199 acres. Upper Smith was listed at 0 percent containment. | Avoided ignitions and fast initial attack have extra value while national resources are heavily committed. This is an operational inference, not a model prediction. |
| USDM/Drought.gov, valid July 14 | 80.9 percent of Idaho was in D1-D4 drought, including 23.1 percent in D3 and 8.4 percent in D4; about 1.2 million residents were in drought. January-June was Idaho's 20th driest such period since 1895. | Treat drought as fuel and water-supply context. It raises concern but does not predict an ignition by itself. |
| NWS Boise `AFDBOI`, issued 6:18 a.m. MDT July 22 | The forecast through Friday night called for numerous central-Idaho mountain thunderstorms Wednesday, possible gusty storms Thursday, then very hot, much drier conditions with 20-35 mph higher-elevation gusts Friday. | Check for lightning holdovers after storms and reassess staffing before the drying/wind transition. This product is an initial snapshot, not a standing work order. |
| NWS Spokane `FWFOTX`, issued 3:14 a.m. PDT July 22 | North Idaho was forecast hot and dry Wednesday, with an isolated thunderstorm threat Thursday and hot, dry, windy conditions Friday-Sunday; the discussion identified critical fire-weather concerns Friday and Saturday. | Use event-triggered readiness in north Idaho and revalidate the product every shift. |
| NIFC July-October outlook | Northern Idaho returned to normal potential in July after wetting rain; central Idaho is highlighted for above-normal potential in August. Southern Idaho retains above-average fine-fuel loading/carryover. | Use different seasonal priorities for north, central, and south Idaho; refresh when the August outlook is issued. |
| PlantGeo NASA POWER screen | 31 coarse 0.5-degree point cells, eight accepted daily climate signals, April 30, 2022-April 30, 2026. | Useful for antecedent climate screening only; it is not live fire weather and is too coarse for parcel or treatment selection. |
| PlantGeo USDM screen | Weekly validated polygons through July 14, 2026. D3-D4 proxy cells were concentrated in southern Idaho; one central proxy cell was D3 and no northern proxy cell was D3-D4. | Supports a south-first prevention emphasis, but official statewide/county products remain authoritative because the local screen uses coarse centroids and an approximate Idaho boundary. |

### PlantGeo antecedent climate comparison

The table compares January-April 2026 with the mean of the same months in 2023-2025.
Regions are latitude bands over 31 coarse NASA POWER centroids, not administrative
boundaries.

| Screening region | Precipitation change | Mean temperature change | Relative-humidity change | Interpretation |
|---|---:|---:|---:|---|
| South | -28.4% | +3.66 C | -9.64 percentage points | Strongest local pre-season drying signal; consistent with the official southern Idaho concern. |
| Central | -3.0% | +3.25 C | -6.68 percentage points | Warmer and drier air despite near-baseline precipitation; combine with current lightning/wind forecasts. |
| North | +25.8% | +2.28 C | -2.50 percentage points | Wetter antecedent conditions support a lower seasonal posture, but not complacency during hot/windy episodes. |

Wind averages were near or below the short local baseline in all three regions. That
does not reduce short-term fire-weather concern: seasonal averages cannot substitute
for current gust and humidity forecasts.

The exact read-only SQL, including the proxy geometry and aggregation rules, is in
[the reproducibility query](sql/idaho-antecedent-climate-screen-2026-07-22.sql).

## Action plan

### 1. Immediate: next 72 hours

**Owner:** agency duty officers, county emergency managers, utilities, contractors,
and land-management operations leads.

- Check the [IDL restriction-zone map](https://www.idl.idaho.gov/fire-management/fire-restrictions-finder/)
  and the applicable NWS/GACC fire-weather product at the start of every shift and again
  before afternoon field work. Do not infer one restriction stage for all Idaho.
- Apply zone-specific work controls to welding, grinding, blasting, chainsaws, mowing,
  and other internal-combustion operations. Where Stage 2 applies, enforce its 1 p.m.-1
  a.m. work prohibitions and required patrol periods. During closed fire season, verify
  spark arrestors and required suppression tools before work starts. The detailed stage
  rules are maintained by [IDL](https://www.idl.idaho.gov/fire-management/fire-restrictions-finder/understanding-stages/).
- In south Idaho, concentrate prevention patrols and public messaging around human-
  ignition corridors, dry fine fuels, and WUI access routes. Do not derive exact patrol
  polygons from PlantGeo yet; it lacks ignition history, roads, assets, and fuels.
- In central Idaho, patrol for lightning holdovers after thunderstorms,
  then escalate detection and initial-attack readiness before the forecast hot/dry/windy
  transition. In north Idaho, activate the same measures only for the identified short
  weather window.
- Recheck current incident counts, containment, and local initial-attack availability in
  [NIFC National Fire News](https://www.nifc.gov/fire-information/nfn) before each
  operational briefing. National Preparedness Level 5 is a resource-context flag, not
  by itself an automatic work-cancellation trigger.

Each organization should formally approve and populate this proposed go/no-go matrix
before using it for discretionary ignition-producing work. Until that approval is
documented, existing authorized procedures control. The current legal restriction,
order, or closure always controls; an adopted matrix may only make the local decision
stricter. At each briefing, record the actual name and backup for every authority role
below.

| Shift trigger | Default work decision | Required decision authority |
|---|---|---|
| Activity is prohibited by the current IDL stage, land-manager order, or closure | Do not start, or stop and secure the site. There is no local operational override. | Jurisdictional fire/land-management authority; law enforcement where the order requires it |
| Red Flag Warning covers the work zone, or the applicable NWS/GACC product identifies a critical fire-weather period during the planned work window | Postpone discretionary spark/heat-producing work. Essential work may proceed only when lawful and under a documented control, suppression, and patrol plan. | Jurisdictional duty officer or fire warden **and** the organization's named operations manager |
| No warning prohibits work, but lightning/outflow is forecast or local initial-attack staffing is below the organization's pre-set minimum | Move or shorten the work window, add patrol/detection coverage, or postpone if the staffing floor cannot be restored. | Named agency/utility duty officer in the daily operating plan |
| No applicable prohibition or critical weather trigger, and the documented staffing minimum is met | Proceed under the current restriction-stage and closed-fire-season controls. | Named site supervisor, recorded on the permit/briefing |

The initial products behind this report were [NWS Boise `AFDBOI`](https://marine.weather.gov/product.php?issuedby=BOI&product=AFD&site=LOT),
issued 6:18 a.m. MDT July 22 with a short-term period through Friday night, and
[NWS Spokane `FWFOTX`](https://forecast.weather.gov/product.php?issuedby=OTX&product=FWF&site=NWS),
issued 3:14 a.m. PDT July 22 with daily detail through Thursday and an outlook through
Tuesday. The checked text and headers are preserved as checksummed evidence excerpts:
[Boise `AFDBOI`](evidence/nws-boi-afdboi-2026-07-22T1218Z.md), SHA-256
`83BCE18CCB9EE04CEE725C80DEE2F06BAE0C5EB9AC479BB9A27608BD3ADD5F6A`, and
[Spokane `FWFOTX`](evidence/nws-otx-fwfotx-2026-07-22T1014Z.md), SHA-256
`8692E95746815F2806744307C0F934276BF7F413DCA48D34E4086006EC52B680`.
The official pages update: replace this initial snapshot whenever a newer product,
warning, or GACC briefing is issued.

**Measure:** 100 percent of high-risk work permits and daily operational briefings
record the restriction zone, current stage, forecast product timestamp, work window,
suppression equipment, and post-work patrol assignment.

### 2. Near term: next 7-30 days

**Owner:** counties, fire districts, homeowner associations, utilities, and WUI property
owners.

- Prioritize home-ignition-zone work in the southern drought footprint and around
  communities with constrained evacuation routes. Follow Idaho's current evaluation:
  establish a noncombustible 0-4 ft zone; maintain grass and remove dead/ladder fuels
  from 4-30 ft; and space vegetation/remove dead fuels from 30-100 ft. Move firewood and
  combustibles at least 30 ft from structures and maintain clearance around fuel tanks.
  See the [IDL Idaho Wildfire Home Protection Zone evaluation](https://www.idl.idaho.gov/wp-content/uploads/2026/03/FMH-821-Attachment-2-Idaho-Wildfire-Home-Protection-Zone-Evaluation-Homeowner.pdf).
- Inspect and maintain existing fuel breaks, roadside treatments, utility corridors,
  and evacuation-route treatments before adding new acreage. BLM reports that the
  maintained 603-acre Pothole South fuel break reduced fire intensity and enabled safer
  engagement during the 2026 Sailor Cap Fire; continued maintenance was central to the
  result. See the [BLM case study](https://www.blm.gov/blog/2026-06-24/nine-miles-prevention-fuel-break-turned-fire).
- Confirm two evacuation routes where feasible, enroll residents in local alerts, stage
  medications/documents/water/N95s, and establish a filtered clean-air room. Use the
  [Idaho Office of Emergency Management wildfire guidance](https://ioem.idaho.gov/wildfire-preparedness-and-safety/).
- Enforce the applicable closed-season and BLM ignition prohibitions, including
  fireworks, exploding targets, incendiary/tracer ammunition, and steel targets in dry
  vegetation. See the [2026 BLM Idaho prevention order](https://www.blm.gov/sites/default/files/docs/2026-05/2026%20BLM%20Idaho_Fire%20Prevention%20Order_MCSigned.pdf).

**Measures:** percentage of priority homes completing 0-4 ft work; miles of existing
fuel break/egress treatment inspected and maintained; percentage of communities with
two documented evacuation options; preventable-ignition count by cause and zone.

### 3. Program planning: fall 2026 through spring 2027

**Owner:** CWPP committees, counties, IDL, BLM/USFS partners, utilities, and grant leads.

- Put treatment projects into Community Wildfire Protection Plans or Hazard Mitigation
  Plans, emphasizing WUI edges, evacuation corridors, critical infrastructure, and
  cross-boundary continuity. Prepare the next [IDL Hazard Fuels Reduction grant](https://www.idl.idaho.gov/about-forestry/forestry-fire-grants/hazard-fuels-reduction-grants/)
  cycle rather than treating the closed FY26 cycle as available funding.
- Require maintenance funding and inspection intervals in every fuel-break proposal.
  Acres constructed without sustained maintenance are not a sufficient success metric.
- Evaluate treatments against observed fire behavior, structure loss, safe-engagement
  opportunities, and evacuation reliability—not just treated acreage.

## PlantGeo data actions required before predictive targeting

PlantGeo currently cannot rank exact Idaho treatment parcels or estimate ignition
probability. The warehouse contains NASA POWER climate and USDM drought but no persisted
Idaho fire, fuels, WUI, treatment, or exposure plane. The next data work should be:

1. Ingest timestamped incident/perimeter and detection data from authoritative NIFC,
   IRWIN/WFIGS or FIRMS sources, retaining corrections and availability time.
2. Add historical MTBS perimeters/severity and verified ignition cause where licensed.
3. Add LANDFIRE fuels/topography, maintained treatment/fuel-break footprints, and
   treatment dates/condition.
4. Add WUI structures, critical infrastructure, roads/egress constraints, jurisdictions,
   and fire-protection response coverage.
5. Persist NWS/GACC fire-weather forecasts, red-flag products, lightning, live IDL
   restriction zones, and observations with issued/valid/as-of timestamps.
6. Validate an Idaho-specific forecast with spatially separated and rolling-origin
   hindcasts before publication. Require calibration, interval coverage, false-negative
   review, and region-specific performance gates.

Until those layers and gates exist, PlantGeo should act as an auditable evidence
dashboard—not an automated dispatch, closure, or parcel-treatment decision system.

## Data quality and interpretation limits

- **Rejected forecast:** the existing Denver wind run is unusable for Idaho and is not
  a wildfire forecast.
- **Climate lag:** NASA POWER ends April 30, 2026; use it only as antecedent context.
- **Coarse support:** local cells are approximately 55.7 km point samples. The Idaho
  screen uses 31 centroids and an approximate state polygon.
- **Drought is not ignition probability:** USDM describes drought impacts, not the
  chance, timing, or severity of a specific fire.
- **Outlooks are relative:** "above-normal significant fire potential" is relative to
  climatology, not a deterministic severity prediction.
- **Dynamic evidence:** incident size/containment, restrictions, and short-term weather
  can change within hours. Recheck live official products before action.
- **No causal treatment model:** the available data do not estimate that a particular
  treatment will prevent a specific fire or loss.

## Sources and reproducibility

Local findings were computed read-only from PostgreSQL 16.14, database `plantgeo`,
Alembic `20260722_0007`, using `agri.signal_observation`, `agri.spatial_cell`,
`agri.drought_polygon_snapshot`, and the forecast/hindcast tables. The screen contained
362,576 accepted Idaho-proxy climate observations across eight signals. The final live
audit retained 14 rejected-run hindcasts and 98 values, with zero operational
publications, receipts, or values.

The climate screen is independently reproducible with
[`idaho-antecedent-climate-screen-2026-07-22.sql`](sql/idaho-antecedent-climate-screen-2026-07-22.sql),
SHA-256 `B32866A8F4723A7846D5061B5DD634E55A81BA132E0ED93656946E5D58E9B1E7`.
The query pins the exact 31 immutable source-release UUIDs used in the analysis. Their
payload checksums are retained in the
[release manifest](evidence/idaho-nasa-power-releases-2026-07-22.csv), SHA-256
`D83D40678537DBAB02557BAA6FA2CC264374305DF8D35D6CA3589ACF5373DE46`, so future
valid or superseding releases cannot silently change the result. It runs in a read-only
transaction and encodes these rules:

- Select 55,660-m centroids covered by the explicit proxy WKT
  `POLYGON((-117.25 42,-111.04 42,-111.04 44.5,-112.7 45,-114.5 46.7,-116.05 49,-117.05 49,-117.05 46,-116.5 45,-116.8 44,-117.25 43,-117.25 42))`.
- Assign south at latitude below 44.0 degrees, central at 44.0 to below 46.5
  degrees, and north at 46.5 degrees or above; the resulting cell counts are 12,
  13, and 6.
- Map `PRECTOTCORR` to precipitation, `T2M` to mean temperature, `RH2M` to
  relative humidity, and `WS2M` to wind speed. Sum January-April precipitation
  within each cell/year; average the other metrics.
- Give each cell equal weight within its region/year, then compare 2026 with the
  arithmetic mean of the corresponding 2023-2025 regional values. No spatial-area
  weighting and no imputation are applied.
- Exclude non-accepted, non-observed, or null normalized values. The checked result
  had zero null normalized values, 248 complete coverage receipts, and exactly
  `31 cells x 1,462 dates x 8 signals = 362,576` received observations.

Current official context was checked against:

- [NIFC National Fire News](https://www.nifc.gov/fire-information/nfn)
- [NIFC monthly seasonal outlook](https://www.nifc.gov/nicc-files/predictive/outlooks/monthly_seasonal_outlook.pdf)
- [Drought.gov Idaho](https://www.drought.gov/states/idaho)
- [NWS Boise `AFDBOI`](https://marine.weather.gov/product.php?issuedby=BOI&product=AFD&site=LOT)
- [NWS Spokane `FWFOTX`](https://forecast.weather.gov/product.php?issuedby=OTX&product=FWF&site=NWS)
- [IDL fire restrictions](https://www.idl.idaho.gov/fire-management/fire-restrictions-finder/)
- [Idaho Office of Emergency Management wildfire preparedness](https://ioem.idaho.gov/wildfire-preparedness-and-safety/)
