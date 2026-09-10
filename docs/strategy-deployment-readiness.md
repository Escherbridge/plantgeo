# Strategy deployment and species-selection readiness

Assessment date: 2026-09-10. This records measured capability and an implementation plan, not a validated intervention ranking. Environmental observations remain on the Parquet path; conversations and user feedback are application metadata.

## What the product can support

PlantGeo can support regional screening, evidence collection, comparison of site constraints, and a documented human decision. It cannot currently demonstrate that one strategy will outperform alternatives at a particular site. The strategy and carbon-effect services explicitly return unavailable until validated evidence is published; an LLM narrative does not change that boundary.

| Decision | Current usefulness | What is required before a stronger conclusion |
| --- | --- | --- |
| Identify drought, observed weather and historical fire context | Useful when the reader returns dated, geographically supported evidence | Disclose missing days, incomplete history, observation versus publication dates, sampling support, and serving failures |
| Compare possible outdoor intervention types | Useful as an explained feasibility discussion | Land-use objective, current vegetation, terrain, soil constraints, water availability, management capacity and conflicting evidence |
| Choose species and planting material | Incomplete: no reviewed species/provenance catalogue is connected to the live advisor | Accepted taxonomy, intended role, regional native status where relevant, cultivar/seedlot provenance, local constraints and cited establishment evidence |
| Promise yield, survival, carbon gain or fire-risk reduction | Unsupported by the current strategy evidence | Measured treatment/comparator outcomes, site and management context, duration, uncertainty and independent validation |
| Deploy a strategy autonomously | Not an implemented capability | A reviewed operational plan, authority for the actual action, resource constraints, monitoring and revision process |

“Suggested candidate,” “feasible with missing checks,” and “supported by measured outcomes” must remain different outputs. Helpful-chat feedback is not ecological outcome evidence. Do not convert an internal LLM confidence label into a calibrated success probability.

## Confirmed quality findings

The regional source sweep established 39 exact NASA climate comparisons and 12 exact ERA5-Land soil comparisons. It also found six upstream-valid August 5 soil cases still unpublished. Weather had 12 exact same-time temperature/wind/precipitation comparisons and three unresolved humidity differences against a later provider retrieval. Original weather ingestion response bytes were not retained, preventing a definitive attribution of those differences.

The repaired cumulative MTBS map reader retained ten previously disappearing fires. Across successful cases, 28 distinct fires matched source attributes and sampled geometry masks. Intermittent serving faults remained, including a private 14-second deadline failure. Later instrumentation showed a scan dominated by waiting; it did not establish the transfer mechanism or certify cold-load reliability.

The separate ingestion task is addressing newer provisional MTBS coverage with explicit snapshot/revision semantics. Completed cohorts, provisional current records, capture time and ignition dates must not be merged into a falsely dated historical series. The current-snapshot work and older historical gaps are not certified complete by this document.

Static SoilGrids properties currently have a live point-query path and legacy cache, but the current UI has no admitted first-party raster release. Historical COG/PMTiles artifacts were found in pipeline records and need identity, availability and coverage verification; their interpolated reprojection must remain labeled as derived display data. The proposed replacement is a versioned property/depth publication whose map and clicked values share the same release. Preserve native support, units, no-data and uncertainty; do not inflate point samples into continuous coverage. Moisture, temperature and weather-derived fields remain time-dependent.

Geographic and depth support also limit recommendations. The audited soil-property bundle covers only 0–5 cm, which does not establish deeper rooting conditions for trees. A nearest stream gauge should retain distance and watershed relevance rather than be treated as the site's water supply. A successful regional cell read does not establish parcel conditions. Require a coverage check before applying this regional evidence outside its supported geography.

## Agent and MCP integration boundary

At audit time, the live Next.js advisor has report-generation tools and optional web search. Its environmental context is assembled before generation. The Python service separately exposes ten bounded environmental tools through MCP stdio and its agent implementation; those tools are not automatically connected to the browser advisor.

The audit found that the live MTBS context still used a legacy external reader rather than the shared Parquet map reader. The ingestion task owns aligning that context with the Parquet reader. Generic Python feature tools also need lane-specific temporal behavior: exact observation days, carried observations, cumulative history and versioned snapshots cannot share an unlabeled exact-day assumption.

Before wiring these tools into the live advisor, require:

1. A reviewed tool allow-list and schema adapter with server-owned identity, location and per-layer date context. The Python analysis endpoint's single selected day cannot silently replace the browser's multiple layer dates.
2. Bounded reads with cancellation, result-size and call-budget limits, and preserved unavailable/partial/no-data states.
3. Requested day, served publication/snapshot day and actual observation time returned separately, with spatial support and source revision.
4. Visible tool activity derived from executed events, not invented progress or private reasoning. Preserve citations and evidence with saved assistant messages.
5. Golden map/tool/narrative comparisons for a mixed-date view, an unpublished day, a carried NDVI observation, an MTBS nonpublication day, and coarse versus detailed support.

## Species data priorities

Aevani presents four systems with distinct information needs: [hydroponics, aquaponics, silvopasture and agroforestry](https://aevani.com/). Outdoor native restoration must not be treated as interchangeable with cultivated food production or controlled-environment systems.

| Priority | Source combination | Decision contribution |
| --- | --- | --- |
| 1 | [USDA PLANTS](https://plants.sc.egov.usda.gov/topics) and [NRCS Plant Materials releases](https://www.nrcs.usda.gov/plant-materials/cp/releases) | Accepted identity, regional status, reported requirements and attributed planting-material/establishment evidence |
| 2 | [SSURGO](https://www.nrcs.usda.gov/resources/data-and-reports/soil-survey-geographic-database-ssurgo) and [ecological site descriptions](https://www.nrcs.usda.gov/getting-assistance/technical-assistance/ecological-sciences/ecological-site-descriptions) | Soil component/horizon constraints, reference communities and disturbance context |
| 3 | Actual seedlot provenance and [Seedlot Selection Tool](https://seedlotselectiontool.org/sst/) | A cited climate-matching workflow after candidate species are identified; not genetic suitability proof |

[TRY](https://www.try-db.org/TryWeb/Database.php) traits, [FEIS](https://research.fs.usda.gov/feis) species/fire literature and [GBIF](https://techdocs.gbif.org/en/openapi/v1/occurrence) occurrence records can enrich candidates. Occurrence is neither native status nor proof of suitability. Trait correlations are not measured intervention effects. Retain taxonomic and coordinate uncertainty, observation dates, study context and explicit missing values.

Verify reuse terms before production ingestion. [PRISM](https://prism.oregonstate.edu/orders/) requires commercial arrangements despite free public access; TRY includes restricted exceptions and GBIF records have mixed licences. Public web access does not establish an unrestricted bulk API or redistribution right. [NOAA climate normals](https://www.ncei.noaa.gov/products/land-based-station/us-climate-normals) are another baseline to evaluate, with station support and period explicitly retained.

Hydroponics and aquaponics need a separate crop/cultivar and operating-envelope catalogue: measured water chemistry, temperature, oxygen, nutrients, light, system configuration and maintenance capacity. Outdoor soil or species-range layers do not supply those measurements. Use edition-specific technical guidance, such as the [FAO aquaponics publication linked here](https://www.fao.org/newsroom/story/Seven-rules-of-thumb-to-follow-in-aquaponics/en), with source rights and current system applicability verified.

## Recommendation record and feedback loop

Each candidate should identify the goal and strategy, accepted taxon and cultivar/seedlot if known, intended role, supporting site evidence, conflicts, missing inputs, source revisions, and a practical verification step. Before a deployment recommendation, separately check current local invasive/noxious status, livestock or other relevant toxicity, applicable movement and seed-transfer restrictions, and seed availability. Native status alone does not answer those questions. Catalogue products require actual species and lot contents; a broad marketing label must not stand in for taxonomy or trial evidence.

Start with transparent exclusion and feasibility rules supported by named sources. Show reasons and evidence completeness separately; do not synthesize a success probability from arbitrary weighted scores. Missing drainage, rooting depth or water chemistry is an unanswered question, not zero risk.

Record deployments separately from chat feedback: chosen candidate, location and area, actual species/lot, baseline, implementation date and intensity, management inputs, comparison where available, and repeated outcomes. Survival, establishment, yield, maintenance effort and ecological response are distinct outcomes. User ratings, copy actions and shares measure product usefulness, not treatment success.

## Release acceptance

Conversation history/resume, explicit copy/share, assistant feedback and actual activity events are being implemented in the QA lane. The data lane owns MTBS publication and static-soil work. A source inventory or passing code tests does not mark an ingestion complete: verify publication, map rendering, selected-date tool results and narrative agreement after each relevant release.

Keep saved conversations private by default. Sharing must be an explicit user action; do not expose a conversation or precise location through a public activity feed as a side effect of rating or copying it.
