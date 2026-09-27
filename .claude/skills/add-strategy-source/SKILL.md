---
name: add-strategy-source
description: >
  Append a new source (URL of an extension guide, paper, report or article) to the strategy-knowledge
  corpus that backs the `strategy-knowledge` MCP server: fetch it, have an agent hand-chunk it and extract
  findings and candidate strategies, validate, reconcile new strategies into the registry with facets, index
  incrementally, re-run the golden eval, and push to the bucket. Use when the user says "add this source",
  "append to the strategy store", "/add-strategy-source <url>", or shares a new environmental-strategy URL
  for the vector store.
---

# add-strategy-source

The corpus is appendable by design: one new URL runs the same pipeline the original 25 sources ran, and only
that source's ids are re-indexed. Contract and rationale: `services/strategy-knowledge/AGENTS.md`
("Append pipeline (runbook)", "Validation", "Indexing"). Run every command from `services/strategy-knowledge`.

## Preconditions
- `services/strategy-knowledge/.env` holds the five `OBJECT_STORE_*` values (same names as
  `services/agri-data-service/.env`; never print them). `strategy-kb status` shows `object_store_variables_set`.
- The local cache is current: `uv run strategy-kb sync pull`. A conflict exits non-zero and transfers nothing -
  stop and ask the user which side wins (`--force-local` / `--force-remote`); never force on your own.

## Steps
1. **Fetch and register.** `uv run strategy-kb add-source <url>`. It prints the `source_id`, raw path, line count,
   the work dir `<work>` = `<cache>/work/<source_id>/`, and one agent slice per ~1,500 raw lines: it writes
   `<work>/slice_assignments.json` with slices `S1..Sn` (cut at blank lines; a short source is the single slice
   `S1`) and `<work>/strategy_ids.txt`. If the site is bot-walled (403, a "Just a quick check" page), do NOT try
   to get around it: read the page in the user's browser and store a paraphrased digest with one short verbatim
   excerpt, marked `kind: digest` (precedent: source `25-phys-...`).
2. **Chunk (agents, sonnet), one agent per slice** in `slice_assignments.json`, in parallel. Brief:
   `briefs/chunk_source.md`, with the mapping `add-source` printed: the agent's "assigned line ranges" are its
   slice's ranges, `briefs/strategy_ids.txt` = `<work>/strategy_ids.txt`, output `<work>/<slice_id>.json` with
   `"slice_id": "<slice_id>"` (e.g. `<work>/S2.json`, `"slice_id": "S2"`). Line N means `text.splitlines()[N-1]` -
   the Read tool's numbering. Agents write ranges and metadata only, never chunk text, and run no validators.
3. **Import and validate (you, not the agent).** Once per slice:
   `uv run strategy-kb import-chunked <work>/<slice_id>.json --assignments <work>/slice_assignments.json`
   (its JSON lists `slices_without_output` until every slice's file exists), then
   `uv run strategy-kb validate --source <source_id>`. Problems block; warnings (6-7-word excerpts, strategies
   without actions, a magnitude number not printed near its finding) do not. Fix data, not the validator. A
   slice's candidates are stored under its slice id and re-importing it replaces only them; a single-slice
   source's import replaces all of its candidates.
4. **Reconcile (agent, opus when the source adds more than a few candidates).** Brief `briefs/registry.md`, run
   against `<cache>/corpus/strategies/strategy_registry.json` + `families.json`: existing ids never change,
   `matches_existing` candidates merge citations, new interventions become strategies or family variants.
5. **Facets (agent, sonnet)** for every registry strategy without all four facets, per `briefs/facets.md`
   (goals and family are already settled by step 4 - copy them). The facet text MUST end up inside each
   registry record's `facets` field: a strategy without facets silently indexes only its summary.
6. **Index.** `uv run strategy-kb index --source <source_id>` (partial; `index_is_stale` stays true), then a full
   `uv run strategy-kb index` before publishing. Either exits 1 when it left a planned source out (stale or
   unregistered, named in the log): fix that source before publishing - such an index is never stamped full.
7. **Regression eval.** `uv run strategy-kb eval --golden eval/golden_queries.json`. Baseline 2026-09-26:
   hit@5 = 1.0 over 24 queries. A drop means the new source crowded out existing answers - report it before
   pushing. Add 1-3 golden queries for what the new source contributes, written in plain land-manager words.
8. **Publish.** `uv run strategy-kb sync push --with-index`, then tell the user the new `corpus_version`. The
   push uploads `corpus/sources.json` last and refuses the index (exit 1, reasons in `index_refusals`, nothing
   under `index/` touched) when the local index is partial or stale, when the corpus push kept remote changes,
   or when `index/LATEST` changed in the bucket since your last sync - pull, re-index, and push again; do not
   force past it without asking the user.

## Never
- Commit raw source text (`.cache/` is gitignored; raw text is copyrighted and lives only in the bucket).
- Cite or index the AI-synthesis source type as evidence.
- Hand-edit `strategy_registry.json` ids, or run `index` and `serve` against the same cache at the same time.
