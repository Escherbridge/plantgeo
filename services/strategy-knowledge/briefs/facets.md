# Brief: goals + facet texts for existing strategies

You are preparing existing strategy records for a vector store. Each strategy is embedded as four
short "facet" documents (MiniLM reads at most ~190 words per document, so each facet must stand
alone and stay under 170 words). Precision beats volume: facets are grounded ONLY in the record's own
fields and the raw source text it cites.

## Read first (working directory)
1. `DESIGN.md` sections 1, 3 and 6 - scope, the goals vocabulary (stated vs inferred), and the exact
   output shape.
2. `strategy_schema.md` - field meanings and enums.
3. Your assigned records in `extracted/<group>.json` (listed in your prompt).

## Per strategy
- `goals`: map each applicable goal (DESIGN section 3) to "stated" when a cited source explicitly
  claims that outcome for this practice, "inferred" when it is a reasonable application the sources
  do not claim. Check the cited raw text (`raw/<source_id>.txt`, use Grep on the excerpt) when unsure.
  Scope is broad environmental enrichment - erosion control, soil health, water, carbon, biodiversity,
  nutrients, remediation, biomass circularity, drought adaptation, productivity, air quality,
  wildfire resilience - so most strategies carry 2-5 goals.
- `fire_phase`: copy from the record ONLY if `wildfire_resilience` is among the goals; else omit.
- `proposed_family`: pick from the seed list below when one fits; otherwise propose a new kebab-case
  family and say so in your final message.
- `facets` (paraphrased, each self-contained, each opening with the strategy name):
  - `overview`: what it is, where it fits, the mechanism.
  - `how_to`: ordered steps, rates, timing, equipment - numbers verbatim from the record.
  - `fit`: land uses, soils, slope, climate/region, scale, and when NOT to use it.
  - `outcomes`: benefits, risks and limitations, and the evidence behind them (say plainly when the
    evidence is expert guidance or a single trial).
  Never invent a rate, a region or an outcome the record and its sources do not support.

## Seed families
post-fire-mulching, post-fire-erosion-barriers, post-fire-reseeding, post-fire-hazard-and-infrastructure,
post-fire-weed-control, hydrophobic-soil-treatment, fuel-reduction-thinning, prescribed-and-managed-fire,
fire-risk-monitoring, biochar-production, biochar-soil-application, biochar-charging-cocomposting,
biochar-remediation, compost-production, compost-application, manure-and-digestate-nutrients,
cover-cropping, tillage-reduction, grazing-management, conservation-structures, agroforestry-buffers,
soil-testing-and-monitoring, soil-chemistry-correction, crop-rotation-diversification,
mulch-and-residue-cover, invasive-annual-grass-control, reforestation, biomass-energy

## Output
One file, `facets/<slice_id>.json`, exactly the DESIGN.md section 6 shape, one entry per assigned
strategy (none skipped). You may assemble it in a Python script (json.dump, ensure_ascii=False).

## Rules
Do NOT run tests, builds, linters or validators. Do NOT browse the web. Touch no file but your output.
Final message (under 150 words): count per family, any new family you proposed, and any record whose
data was too thin to write a grounded facet (name the facet).
