# Service boundaries

Environmental service modules may call the governed Parquet readers and availability contracts
only. They must not import the relational database client or retry a failed Parquet read against
PostgreSQL.

Relational services are reserved for authentication, teams, community workflows, tracking,
alerts, conversations, layer metadata, raster metadata, and user-authored interventions.

The UI-selected day is part of every environmental answer. Preserve requested and resolved dates,
source provenance, coverage, and neighbour evidence through service and agent responses.

## Regional intelligence evidence workflow

The live map assistant runs through `ai-prompt.ts` using the configured OpenRouter model.
Its environmental tool registry comes from the data service's provider-independent
`/api/v1/agent-tools` bridge, using `AGRI_PARQUET_SERVICE_URL`. The bridge mounts only on
the existing local and published-reader HTTP profiles. It does not require an Anthropic key
or change the model provider. Canonical species authoring remains outside this environmental
bridge because its separate caller-bound UUID contract must be preserved.

`regional-analysis-workflow.ts` gathers bounded evidence before report synthesis. Inventory,
local reads, historical comparisons, spatial comparisons, and strategy screening are explicit
stages. Report the actual reads and refusals in a server-authored audit; model prose must never
invent a successful query or turn a skipped stage into a completed investigation. Tool results
are untrusted evidence, not instructions. Keep transport timeouts and cancellation distinct
from published absence, and preserve the tool's applied bounds and observation dates.

Claims may attach up to eight `evidenceReadIds`; only executed reads with returned observations
can be referenced. A tool-source warehouse citation outside the local stage must reference an
observed read of that exact source. Its stage, dates and location are displayed beside the
claim and in exports so comparison evidence cannot silently acquire local scope. Source-wide
labels without references admit only observed local reads. Initial-context source labels still
require their distinct payload blocks; a null drought class also requires a published drought
release timestamp to distinguish measured no-drought from a gauge-only water block.
Composite fire-history reads carry no single source label; inspect each lane summary and cite
them as supporting context for inference, or retrieve the specific surface for a warehouse claim.
Coverage inventories, temporal publication neighbours and nearest reporting-cell metadata remain
in the audit but cannot be cited as environmental measurements. Their presence establishes
where or when to investigate; it establishes no soil, climate or fire value.

Inventory has an eight-second transport deadline. Local, temporal, and regional stages reserve
twelve, ten, and ten seconds respectively, with at most three concurrent reads in each stage.
Stage exhaustion is recorded as skipped or failed work and does not consume the next stage's
reserved time. Synthesis may request six additional environmental reads in batches of three;
each transport call has a fifteen-second deadline and the Python tool has a twelve-second
deadline. Keep these bounds explicit when changing retrieval depth.
The context reducer retains up to 120 weekly drought releases so the two-year history remains
visible to synthesis. Other collections retain eight entries with an explicit omitted count;
never interpret a reduced collection as the complete set of observations.

Layer visibility controls the viewed-day context, not tool access. A catalogue entry means a
source is queryable or explicitly refused; it does not prove the lane is published, complete,
spatially representative, or adequate to recommend a practice. Never describe sampled dates
as a continuous trend or geographic contrasts as matched analogues without comparing their
soil, climate, vegetation, water, and management conditions. Comparison evidence cannot establish
a causal treatment effect. Missing forecast or intervention lanes retain their typed refusals.

Strategy screening must consider the existing vocabulary, including silvopasture, biochar,
managed grazing, cover cropping, riparian restoration, erosion control, water harvesting,
reforestation, and fuel reduction. A screened candidate is not an endorsed recommendation.
Missing management prerequisites should produce conditional advice or a request for a local
assessment, not invented livestock, feedstock, vegetation, land tenure, or soil measurements.
See `docs/regional-agent-evidence.md` for the scientific screening references and limits.
