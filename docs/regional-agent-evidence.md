# Regional intelligence evidence and strategy assessment

The map assistant needs access to the same governed environmental observations as the map,
including layers that are not currently visible. Source access and source completeness are
separate concerns: connecting a tool cannot repair a missing publication or validate an
unpublished forecast or intervention model.

## Evidence sequence

The server inventories the deployed tools and surfaces, collects local evidence, samples
historical conditions, retrieves spatial comparisons, and supplies explicit strategy screening
criteria before model synthesis. The user-facing audit records the performed reads and their
outcomes. It is an execution and evidence record, not a transcript of private model reasoning.

Every observation remains attached to its requested day, resolved publication day, location,
and applied read bounds. Per-layer viewed days can differ. Historical observations are not
stale merely because their dates are old, and nearby values cannot silently replace the answer
at the selected location or date. A refusal, transport failure, unqueried source, unpublished
day, and governed absence carry different meanings.

Spatial comparison locations are candidates for comparison. Geographic proximity alone does
not make a location an ecological analogue. Compare available climate, soils, water,
vegetation, terrain, and management characteristics and state any missing dimensions before
transferring guidance. Observations in another region do not measure the effect of a treatment
at this site. Sparse temporal samples do not establish a trend, seasonality, or return period.

## Strategy screening references

These primary sources inform the questions the assistant should investigate. They are general
practice guidance, not measurements at the selected site, and do not establish local benefits.

| Candidate | Questions to resolve before recommending implementation | Primary guidance |
| --- | --- | --- |
| Silvopasture | Is a managed tree, forage, and livestock system appropriate for the land use? Are grazing rotation, tree protection, and regeneration feasible? Which local climate, soil, and water constraints remain unmeasured? | [US Forest Service National Agroforestry Center: Silvopasture](https://research.fs.usda.gov/centers/nac/silvopasture) |
| Biochar | What soil amendment goal is being addressed? What are the soil conditions and pH? What feedstock and production conditions define the product? What local assessment or trial is needed to establish fit? | [USDA Northwest Climate Hub: Biochar](https://www.climatehubs.usda.gov/hubs/northwest/topic/biochar) |

The Forest Service describes silvopasture as deliberate integration of trees, forage, and
livestock, with rotational grazing and planning for tree regeneration. A wooded site alone
does not supply those management prerequisites. The USDA Climate Hub explains that biochar
properties depend on feedstock and production temperature and that product selection should
follow the soil amendment goal. Drought alone cannot establish that a particular biochar is
appropriate or that fuel reduction is the best response.

Screen alternatives using the available evidence and disclose missing inputs. Keep recommended
actions separate from practices worth investigating. Do not manufacture a ranking, treatment
effect, expected benefit percentage, or high confidence to fill a report. Fuel reduction remains
one possible strategy and needs its own evidence of local fit.

## Operational limits

The environmental bridge retains the data service's Parquet readers, availability contracts,
read admission, row caps, temporal windows, and typed refusals. It never retries an environmental
read against PostgreSQL. Species authoring uses a separate scoped lookup and is not exposed by
this bridge. Web guidance, when configured, supplements local observations with explicit source
citations and cannot substitute for an unavailable local measurement.

Verification must cover the live Next.js tool loop, data-service tool dispatch, selected-day
preservation, bounded history and regional comparisons, report persistence, and rendered audit.
Mocked integration checks establish these contracts; they do not certify live warehouse
coverage or the ecological quality of a particular model response.
