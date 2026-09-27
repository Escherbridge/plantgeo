# Eval sets

Each file is a JSON list of `GoldenQuery` (see `evaluation.py`), scored by `strategy-kb eval --golden <file>`
or `evaluate()` directly. `kind` tags provenance; `findings`/`passages` also select the search endpoint
(`search_findings`/`search_passages` instead of `search_strategies`).

- `golden_queries.json` — the original 24 hand-written queries.
- `paraphrases.json` — 3 reworded paraphrases of each golden query (72 items), same `expected_any_of`.
- `lay_language.json` — farmer vocabulary ("sour", "hardpan", "hillside", "burnt", "washed out", "salty",
  "won't grow anything") with no jargon.
- `ph_contrast.json` — raise-pH vs lower-pH pairs (lime vs elemental sulfur, both directions) with
  `forbidden_at_3` on the wrong-direction strategy.
- `family_coverage.json` — one query per strategy family the original golden set never tested (18 families,
  checked against `.cache/corpus/strategies/families.json`).
- `findings_coverage.json` / `passages_coverage.json` — coverage for `search_findings` (expects a
  `finding_id`) and `search_passages` (expects the source's `source_id`, since passages have no stable
  pre-computed id to label blind).
- `context_query.json` — the S3 production shape: a directionless `query` ("improve soil chemistry", "fix soil
  chemistry" — the two golden/held-out queries with no pH cue of their own) paired with a `context_query`
  carrying the pH direction in lay words, contrasted both ways. Committed 2026-09-27 so `CONTEXT_QUERY_WEIGHT`
  (`search.py`) is measured against a real eval file instead of only an uncommitted scratch set (AGENTS.md
  "Retrieval"); replace with a held-out variant once the S5 harness records real `user_question` values.

## HELD-OUT: `agent_queries_heldout.json`

The verbatim `query` arguments real agent runs sent to `search_environmental_strategies`, taken from the
stored transcripts under `.omc/research/agent-evals-20260926/*/`. Expected ids are the members of each
scenario's own hand-authored `expected_family_ids` (never the transcript's `retrieved_strategy_ids` — that
would be circular, scoring retrieval against itself). **Do not tune retrieval (query_intent, fusion
weights, vocabulary) against this file.** It exists to catch overfitting to the other, tuning-eligible sets:
score it only to confirm a retrieval change generalizes, and treat a regression here as a stronger signal
than a gain on the tuning sets.

**`expected_any_of` and `forbidden_at_3` must never overlap** (`GoldenQuery` rejects it at load, added
2026-09-27): the file used to list `elemental-sulfur-soil-acidification` as both a right answer and a
forbidden one for two soil-chemistry items, which let a wrong-direction top hit score as a hit and a
violation at once. "fix soil chemistry" carries no pH direction in its own text (no `context_query` is set
on any item here yet), so it no longer forbids either amendment; "fix acidic sour pasture soil low pH
improve forage yields" states its direction and keeps sulfur forbidden.
