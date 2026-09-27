# Brief: reconcile strategies into the canonical registry

You own the strategy registry of an appendable environmental-strategy knowledge base. Inputs are the
97 original strategies plus every `candidate_strategies` entry the chunking agents emitted. You decide
identity (same intervention or not) and family membership. Everything downstream keys on your ids,
so stability and precision matter more than speed.

## Read first (working directory)
- `DESIGN.md` sections 2, 3, 5 (CandidateStrategy) and 7 (registry rules).
- `strategy_schema.md` (StrategyRecord and enums).
- `briefs/facets.md` seed family list.
- Inputs: `extracted/*.json` (97 originals), `facets/F*.json` (goals + proposed_family + facets for
  the 97), `chunked/*.json` -> `sources[].candidate_strategies` (read them via a Python script; there
  are too many to read by hand).

## Rules
1. Existing ids never change. A candidate with `matches_existing` merges into that record: append its
   `sources` citations (dedupe by source_id + excerpt), add new rates to `application_rate` (join with
   " | " and keep each rate's source in `notes`), union list fields (land_use, region, soil_conditions,
   scale, materials, equipment, benefits, risks_limitations), keep the higher evidence_strength, union
   goals (stated beats inferred).
2. A candidate without `matches_existing` is compared against the registry by meaning, not by name.
   True duplicate (same intervention, same context) -> merge as in rule 1, record the candidate id
   in `merged_from`. Regional, crop-specific or rate-specific variant -> a separate strategy in the same
   family. New intervention -> new strategy (keep the candidate's id unless it collides or is not
   kebab-case).
3. Merge the known exact duplicate pair: `in-woods-biochar-production` (keep) +
   `in-woods-biochar-production-soil-application` (merged_from).
4. Families: start from the seed list and the facet agents' `proposed_family`; create a new family only
   when 2+ strategies share an intervention type no seed covers. Every strategy has exactly one
   family_id. Aim for 20-40 families.
5. Do not invent evidence. Every citation you keep must already exist in an input (excerpts were
   machine-verified upstream).

## Output (write with a Python script, json.dump, ensure_ascii=False)
- `registry/strategy_registry.json`: `{"strategies": [ StrategyRecord + "goals", "family_id",
  "merged_from": [...], "origin": "extracted" | "candidate", "needs_facets": bool ]}` -
  `needs_facets` is true for every strategy without an F1-F3 facet entry (new strategies). For the
  97, copy goals/fire_phase from facets/F*.json.
- `registry/families.json`: `[{"family_id", "name", "description", "member_strategy_ids"}]`.
- `registry/reconcile_log.json`: one entry per candidate: `{candidate_id, slice_id, decision:
  "merged_into_existing" | "merged_duplicate" | "new_strategy" | "new_variant", target_id, reason}`.

Do NOT run tests or validators; do not touch files outside `registry/`.
Final message (under 250 words): counts (strategies, families, merges, new), the 10 largest
families, and any identity call you were unsure about.
