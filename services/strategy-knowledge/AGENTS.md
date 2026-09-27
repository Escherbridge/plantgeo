# strategy-knowledge

A standalone MCP server, indexer and CLI (`strategy-kb`) over a **retrieval-only, literature-grounded**
knowledge base of environmental support and enrichment strategies: soil health, water, carbon, erosion,
wildfire resilience, biodiversity, nutrients, remediation, biomass circularity, drought adaptation. It never
computes or implies causal effect sizes, never writes to Postgres, and never touches the migration-0013 label
plane (DESIGN.md section 1).

Code carries one-line docstrings (the "what"); this file carries the "why". Code points here as
`AGENTS.md section "<name>"`.

## Where the contract lives

The frozen contract is `.omc/research/wildfire-ag-strategies-20260926/DESIGN.md` (v1.1) plus
`strategy_schema.md` beside it. **`.omc/` is git-ignored**, so everything the code needs from the contract is
restated here, in `src/strategy_knowledge/vocabulary.py`, and in `briefs/`. The three briefs
(`chunk_source.md` v1.1, `facets.md`, `registry.md`) are **verbatim copies** of the research wave's briefs, so a
future sync is a plain diff; `add-source` prints the mapping from the brief's slice vocabulary to the append
pipeline's paths (see "Append pipeline"). Section numbers below ("DESIGN section 8") refer to DESIGN.md.

DESIGN v1.1 (pilot join, 2026-09-26) grew the Region enum by ten values (`us_midwest` ... `oceania`). The list
exists once, as the `Region` literal in `vocabulary.py`; one-hot `region_<value>` keys, validation,
`list_facets` and the site-profile `region` passthrough all derive from it.

## Module map

| module | what it owns |
|---|---|
| `vocabulary.py` | every closed enum as a `Literal`, the North American region set, facet labels, goals -> NRCS resource concerns, evidence ranks, excerpt bounds |
| `models.py` | pydantic records: StrategyRecord, RegistryStrategy, CandidateStrategy, Family, Chunk, Finding, SourceEntry, ChunkPlan, FindingsFile, PassageWindow |
| `config.py` | env + `services/strategy-knowledge/.env` -> `Settings`; object-store coordinates as `SecretStr`; candidate pool; `/mcp` allowed hosts |
| `corpus.py` | the local cache layout (mirror of the bucket prefix), load/save, sha256, `corpus_version`, freshness, the line convention, source-id pattern, strategy alias map |
| `storage.py` | three-way bucket sync pull/push (boto3) against a last-synced manifest, optional prebuilt index archive |
| `fetch.py` | URL -> text (trafilatura / pypdf) under a byte ceiling and deadline, `source_id`, raw-file header |
| `ingest.py` | `add-source`, `import-chunked` (validate + split + merge), `bootstrap` from the research directory |
| `validate.py` | port of `verify_extractions.py` + `validate_chunked.py`, plus the whole-file coverage rule |
| `materialize.py` | chunk plans + raw text -> balanced passage windows (trail + text <= 180 words) |
| `metadata.py` | DESIGN section 8 one-hot flattening, labelled facet documents, section trails, record hashes |
| `filters.py` | one filter expression -> Chroma `where` and a Python predicate |
| `lexical.py` | Okapi BM25 over one collection, light stemming, weighted expansion tokens |
| `query_intent.py` | lay vocabulary + pH direction -> capped soil-condition boosts, a pH demotion and BM25-only expansion tokens |
| `search.py` | strategy-level weighted reciprocal-rank fusion, capped boosts, facet collapse, family diversity, paging |
| `site_profile.py` | DESIGN section 10 rules |
| `embedding.py` | the injectable `TextEmbedder` seam; production MiniLM |
| `index.py` | preflight, build / incremental update of the three collections, full-vs-partial stamp |
| `knowledge_base.py` | the opened index + corpus; the eight operations as plain dicts |
| `queries.py` | validated request models shared by the MCP tools and the eval harness |
| `server.py` | the eight tools as one `ToolTable`, the MCP server over it (`MCPServer`, stdio); the lifespan opens the knowledge base, after an optional bucket pull |
| `http_app.py` | the HTTP transport: `/health`, `/ready`, `POST /v1/tools/{tool_name}`, MCP streamable HTTP at `/mcp`; uvicorn |
| `evaluation.py` | golden-query hit@k, MRR and forbidden@3, per kind and per family |
| `streams.py` | `reserved_stdout` |
| `cli.py` | `strategy-kb` |

## Vocabulary

Enums are `Literal` types so the MCP input schemas publish the allowed values to the calling model and pydantic
rejects anything else before a tool body runs. Value tuples (`REGIONS`, ...) are `get_args` of the literals,
never retyped. `EVIDENCE_RANK` is DESIGN section 4's `$gte` scale (ai_synthesis_only=1 ...
review_or_meta_analysis=6). `NORTH_AMERICAN_REGIONS` is the explicit subset (13 specific regions plus
`north_america_general`) the region hierarchy uses; a test pins it inside `REGIONS`.

## Corpus layout and versioning

The cache (`STRATEGY_KB_CACHE_DIR`, default `services/strategy-knowledge/.cache/`, git-ignored because `raw/` is
copyrighted text) mirrors the bucket prefix of DESIGN section 12:

```
raw/<source_id>.txt                      verbatim text (header lines included, no trailing newline)
corpus/sources.json                      {"schema_version", "sources": [SourceEntry]} incl. sha256 per raw file
corpus/chunk_plans/<source_id>.json      {source_id, raw_sha256, assigned_ranges, chunks, skipped, candidate_strategies}
corpus/findings/<source_id>.json         {source_id, raw_sha256, findings}
corpus/strategies/strategy_registry.json, families.json   (list, or {"strategies": [...]} / {"families": [...]})
index/<corpus_version>/chroma.tar.gz, index/LATEST        (bucket only; optional)
chroma/  materialized/  work/  sync_manifest.json         local only, never synced
```

- `corpus_version` = SHA-256 over each covered file's relative path and content hash, sorted by path. Raw files
  are covered through their sha256 in `sources.json`. It is computed, never stored, so "bump" means recompute.
  `test_corpus_version_is_a_content_hash` recomputes it independently from this definition.
- **Freshness (why plans record `raw_sha256`).** DESIGN section 12 says a raw file whose sha256 differs from
  `sources.json` invalidates its plans. But `add-source` on a changed upstream rewrites the raw file AND updates
  `sources.json`, after which the two agree while the plan's line numbers are wrong. So each plan and findings
  file records the raw hash it was made against; a source is usable only when raw == sources.json == plan ==
  findings. Stale sources are dropped from the index by `index` and reported by `validate`.
- **Line convention.** Line N is `text.splitlines()[N - 1]` and a file's line count is `len(text.splitlines())`,
  the numbering the Read tool prints (`corpus.split_raw_lines`). Never `split("\n")`: for a file ending in a
  newline (research raw files 24 and 25 do) it yields a phantom empty last line, and coverage would then report
  line N + 1 as uncovered. Coverage, materialization and excerpt windows all use the one function. On the
  current 25 raw files the two conventions differ only by that trailing newline (checked 2026-09-26: no form
  feeds, `\r`, ` ` or other `splitlines` separators). `add-source` still writes no trailing newline.
- **Source ids** match `^[a-z0-9][a-z0-9-]{2,120}$` (`corpus.SOURCE_ID_PATTERN`); all 25 research stems do,
  e.g. `17b-wsu-fs069e-global-climate-change`. Every path builder checks it, because a source id becomes a file
  name; `fetch` caps the host label at 40 characters so a generated `<yyyymmdd>-<host>-<slug>-<digest>` fits.

## Strategy aliases

`corpus.build_alias_map` is the one place that decides which superseded ids resolve to which registry id:
first the registry's `merged_from`, then every stored chunking candidate whose `matches_existing` names a
registry id (or one of its aliases) - the registry step (`briefs/registry.md` rule 1) merges those candidates
without recording them in `merged_from`, yet chunks and findings may link to the candidate's id. Validation,
indexing (linked ids are stored canonical) and the knowledge base (`get_strategy`, scope filters) all use it.

## Validation

`validate.py` ports both reference scripts. Changes in the port, and why:

- **Whole-file coverage.** For every source with any chunk plan, chunks + skipped ranges must cover lines
  `[1, line_count]` of its raw file exactly once. Lines no imported slice was assigned are named as their own
  problem ("lie in no imported slice's assignment"), so a missing slice is an error, not silence. `import-chunked`
  still checks each block against its own assigned ranges, as the reference did.
- **Assignment references.** With `--assignments`, a source the slice was assigned but the document left out is
  rejected ("assigned source missing from output", restored from the reference), and a block for a source the
  slice was not assigned is rejected rather than silently treated as a whole-file assignment.
- **Linkability after reconcile.** Before a registry exists, links and `matches_existing` are checked against
  the known-id file (`--known-ids`, a `strategy_ids.txt`) plus the block's own candidates. Once it exists, the
  known-id file is ignored: `matches_existing` may name registry ids + `merged_from` aliases, links may name those
  plus the matches_existing-derived aliases ("Strategy aliases"). At import a block's own candidates stay
  linkable (they await the registry step); in the stored corpus they do not, so a link to a candidate the
  registry step never reconciled is reported.
- **Excerpt bounds.** Finding and citation excerpts: under 6 words or over 25 is a problem; 6-7 words is a
  *warning* (DESIGN section 5 says 8-25, the pilot corpus was validated at >= 6, so it is not retroactively
  rejected). Warnings never fail `validate` or `import-chunked`.
- **AI-synthesis source.** A candidate citing a `24-` source is a problem, as in the reference; a registry
  strategy may carry such a citation beside a primary one (strategy_schema.md rule 3).
- **`actions` only warns** (`SOFT_STRATEGY_FIELDS`). A strategy grounded in a research abstract often has no
  operational steps to cite; making `actions` required would push agents to invent steps or drop real
  strategies. `validate` lists the warning so a later source's steps can fill the gap.
- **Magnitudes are "as reported".** DESIGN section 5 says `magnitude` holds verbatim numbers; the words around
  them may be the agent's. So every number token of a finding's `magnitude` (`\d+([.,]\d+)*`) must appear among
  the number tokens of the finding's raw window (its lines, 3 before, 2 after - the excerpt window); a missing
  one is a *warning* naming the token. The server and tool descriptions say "magnitude as reported (numbers
  verbatim from the source)", not "verbatim magnitude", because the text around the numbers is not verbatim.
- **Conflicting matches.** A candidate one slice repeats with two different non-null `matches_existing` targets
  is a problem: folding them would silently pick one (see "Append pipeline", candidate folding).
- `fire_phase` is no longer a required strategy field: under the broadened scope (DESIGN section 3) it exists
  only on records carrying `wildfire_resilience`, and a record carrying `fire_phase` without that goal is a
  registry problem. Candidates only count it (the registry step owns the fix).
- Added: chunk ranges beyond the raw file, duplicate chunk/finding/strategy ids, variable roles, candidate list
  enums, facets over 170 words, `family_id` missing from `families.json`, sources.json enum and id checks.
- Validation reads raw JSON dicts, not models, so a bad enum is reported as a problem instead of raising.
- **Registry `search_terms`** (optional list of non-empty strings) must be non-empty for every member of
  `SEARCH_TERMS_REQUIRED_FAMILIES` (`soil-chemistry-correction`: pH, salinity, sodicity; `tillage-reduction`:
  compaction). Those needs are where land managers' words ("sour", "salty", "hardpan") and the facet text
  disagree most, and the eval sets measured the misses there ("Retrieval").
- **Text addressed to an AI** (prompt injection) is a *problem*, never a silent drop: a chunk's raw lines,
  title, summary and keywords; a finding's claim, conditions, magnitude and excerpt; a candidate's or registry
  strategy's free text, facets, citation excerpts and search terms; a family's name and description; a
  source's title, summary and publisher. `INSTRUCTION_TEXT_PATTERNS` is deliberately narrow ("ignore previous
  instructions", "you are an AI", "as an AI language model", an `assistant:` speaker label, chat-template
  tokens, "new instructions:", "do not tell the user"). `system:` at a line start is *not* flagged: PDF line
  wraps produce it ("...fit in your\nsystem: planted following..." in the SARE text). Raw source text cannot be
  edited, so the remedy for a real hit is a skipped range with a reason, or dropping the source.
- **Source URLs must be https** (`url`, `final_url` in `sources.json`); an `http://` URL is a problem, since
  every citation the agent shows links there.

## Materializer

Windows exist because MiniLM truncates at 256 tokens (~180 words). The 180-word budget covers the **whole
embedded document**: the section trail that prefixes a passage (`a > b > c`, separators counted as words) is
subtracted first, so trail + window text <= 180. A trail is cut to its last 60 words (the corpus maximum is 30).

A sentence runs to terminal punctuation followed by whitespace, a blank line, or the end; abbreviations
over-split, which only moves a window boundary. A sentence over the budget (tables, garbled PDF text) is cut
into near-equal word runs. Windows are a **balanced linear partition**: greedy packing at the budget gives the
fewest windows `n`; a binary search then finds the smallest capacity that still packs into `n`, and windows are
packed at that capacity. So 90/90/20 becomes 90 + 110, never 180 + 20 (a 20-word window embeds as noise), and no
tail is left that a merge could absorb under the cap. Window text is the verbatim raw span with whitespace
collapsed; its id is `<chunk_id>#W<index>` and it carries its own line span. `noise` chunks are indexed (the
chunk brief asks for them when they have any retrieval value) but `search_passages` excludes them by default.

## Metadata

DESIGN section 8 exactly, plus keys the tools need (all scalar):

- **Facet documents** are `"<strategy name> (<facet label>): <facet text>"` (labels: overview, how to, fit,
  outcomes), so every facet vector names its strategy; the raw facet text is kept in `facet_text` metadata and is
  what `search_strategies` shows as the matched-facet snippet.
- `lu_untagged` / `region_untagged` = true when a record has no land_use / region. Chunk and finding tags are
  sparse, and "untagged is not unsuitable" (DESIGN section 10); a Chroma `where` cannot test for an absent key,
  so the absence is materialised.
- `linked_<strategy_id>` = true on findings and passages, for the `strategy_id` scope filter. Linked ids are
  stored canonical at index time ("Strategy aliases").
- `keywords` (`|`-joined): chunk title + keywords, finding variable names, strategy materials + equipment +
  NRCS code. BM25 indexes them next to the document; dense search does not see them. The strategy **name is not
  a keyword**: every facet document already starts with it, and listing it again made BM25 count it twice.
- `search_terms` (`|`-joined, strategy facets only): the strategy's family name and description, then its
  registry `search_terms`. BM25-only, like `keywords`; written at index time from `families.json`, so a family
  description ("Adjusting soil chemistry with lime, gypsum or elemental sulfur...") is finally searchable. An
  index built before the key existed simply lacks it: `CollectionView.load` reads it with a default and ranks
  without it (the published `5e1f53dc...` index serves unchanged, measured below).
- `phase_<phase>` only when the record carries `wildfire_resilience` (DESIGN section 3).
- `record_hash` (content hash excluding `corpus_version`) makes re-indexing an unchanged record a no-op;
  `facet_origin` marks a strategy with no authored facets whose summary was indexed as `overview`.
- Display scalars: `name`, `title`, `section_path`, line spans, `strategy_ids`, `finding_ids`.

## Filters

`filters.py` builds one expression and compiles it twice: to a Chroma `where` for dense search and to a Python
predicate for BM25, so both retrievers see the same candidate set. Values within a field are OR'd, fields are
AND'd. land_use always also admits `general` and untagged records (DESIGN section 10). **Region is a
hierarchy:** a region filter admits the requested region, `general`, untagged records, `global` always, and
`north_america_general` when the requested region is North American (`vocabulary.broader_regions`). It admits
ancestors only: a `north_america_general` request does not pull in `pnw_inland` records. The same rule applies to
`site_profile.region`, which becomes the region filter. The echo lists what was admitted
(`region_also_admits`).

A `strategy_id` scope matches the canonical id OR any alias that resolves to it: the index stores canonical ids,
but a source indexed before a later merge still carries the old id until it is re-indexed. `$and`/`$or` with
one member is unwrapped because Chroma rejects it. `NoneOf` (`$nin`) is only used on keys every record of the
collection carries (content_type, relevance), because absent-key semantics of `$nin` are not something to rely
on.

Explicit `soil_conditions`, `scale`, `category`, `fire_phase`, `min_evidence` are hard filters: the agent asked
for them. site_profile-derived soil/slope/burn/precipitation conditions are **boosts** (DESIGN section 10).
An explicit `land_use` / `region` overrides the one derived from `land_cover` / `region`; the response says so
in `site_profile.overridden_by_explicit_filters`.

## Embedding and Chroma

The service embeds text itself and passes vectors (`embeddings=` / `query_embeddings=`), with
`embedding_function=None` on every collection call. Chroma 1.x persists a collection's embedding function in its
configuration and raises on a mismatch at `get_collection`; owning the vectors keeps the seam injectable (tests
use a hashing embedder, production uses `ONNXMiniLM_L6_V2`) and puts the model check where DESIGN section 8 wants
it: collection metadata `embedding_model`, `corpus_version`, `schema_version`, compared on open
(`IndexMismatchError`, CLI exit 2). The ONNX model downloads on first use to `~/.cache/chroma/onnx_models/`;
`serve` warms it in the lifespan so the first tool call does not pay for the download.

Verified against the installed chromadb **1.5.9** source:

- `chromadb.PersistentClient(path=..., settings=chromadb.config.Settings(anonymized_telemetry=False))`
- `client.create_collection(name, configuration={"hnsw": {"space": "cosine"}}, metadata={...},
  embedding_function=None)` - 1.x configuration form; the pre-1.0 `metadata={"hnsw:space": ...}` is legacy,
  and `modify(metadata=...)` rejects an `hnsw:space` key
- `client.get_collection(name, embedding_function=None)`, `client.list_collections()` (returns `Collection`
  objects), `client.delete_collection(name)`, `client.get_max_batch_size()`
- `collection.add(ids, embeddings, documents, metadatas)`; metadata values str/int/float/bool (1.x also accepts
  lists; this service keeps scalars per DESIGN section 8); a metadata dict must be non-empty
- `collection.get(where=..., include=["metadatas"])`, `collection.delete(ids=[...])`,
  `collection.delete(where={key: {"$eq": value}})`
- `collection.query(query_embeddings=[vector], n_results=n, where=..., include=["distances"])`
- `where`: exactly one top-level key; `$and`/`$or` need >= 2 members; operators `$eq $ne $gt $gte $lt $lte $in
  $nin` (`$gte` operand must be numeric)
- `collection.modify(metadata=...)`; `collection.count()`. Whether `modify` merges or replaces metadata is not
  relied on: every stamp writes every key, and `partial_since` is cleared by writing `""`, not by omitting it.
- `upsert` is deliberately not used: on an existing id it may merge metadata, which would leave a stale
  one-hot key (a `soil_hydrophobic` a record no longer has) matching filters. Changed records are deleted by id
  and re-added.

**The HNSW pool must never evict a live index (`index.open_client` → `widen_hnsw_pool`).** Chroma 1.5.9 keeps
each collection's HNSW index in an in-memory pool (Rust source, tag `1.5.9`, fetched to verify; only the
compiled `.pyd` is installed). Four facts combine:

- The writer saves to disk only every `sync_threshold` (default 1000) records (`rust/segment/src/local_hnsw.rs`
  `apply_log_chunk`), so a small collection exists only in the pool.
- A pool miss rebuilds the reader from disk only (`local_segment_manager.rs` `get_hnsw_reader` →
  `local_hnsw.rs` `LocalHnswSegmentReader::from_segment`). With no `index_metadata.pickle` there it raises
  `UninitializedSegment`, "Nothing found on disk".
- The log replay that could repair it runs once per collection per process (`rust/frontend/src/executor/local.rs`
  `try_backfill_collection`). `Indexer._sync`'s first `get`, before any vector, already marks the collection done.
- The pool holds file-handle limit // 5 indexes (`chromadb/api/rust.py` `RustBindingsAPI.__init__`), split over
  64 shards (`rust/cache/src/foyer.rs` `default_shards`; foyer-memory 0.17.4 `raw.rs` gives each shard
  `capacity / shards`). On Windows that is 512 // 5 = 102 slots, or one per shard, so two of a process's three
  indexes that hash to one shard evict each other. The failure was intermittent (about 1 fixture in 20) because
  shard placement follows random segment UUIDs.

`widen_hnsw_pool` raises that limit before the first client (`_setmaxstdio` on Windows, whose `msvcrt.dll`
refuses more than 2048, or `RLIMIT_NOFILE` up to the hard limit elsewhere). That gives 6 slots per shard, twice
the three collections, so the pool cannot evict while a process holds at most six indexes per cache path.

On Linux (the container) Chroma reads the **soft** `RLIMIT_NOFILE`. Docker commonly starts a process with soft
1024 (raised here to 1920, allowed without privilege up to the hard limit) or 1048576 (left alone). An unlimited
soft limit is replaced by 1920 rather than kept: CPython reports `RLIM_INFINITY` as -1 on Linux, and Chroma would
size its pool as -1 // 5. A hard limit below 1920 is logged, because the pool then has fewer than 6 slots per shard.

Rejected alternatives:
- A lower `sync_threshold` cannot close the gap: Rust validates `range(min = 2)`, which still leaves a one-record
  tail that exists only in memory.
- A custom `chroma_api_impl` is refused by `SharedSystemClient._get_identifier_from_settings`.
- Separate or shared clients change nothing, because every client on one path shares one `System`.

Regression tests: `tests/test_index_lifecycle.py` `test_the_client_hnsw_pool_gives_every_shard_its_slots`
and `test_a_full_shard_of_hnsw_indexes_stays_queryable`.

## Indexing

- **Preflight before any change.** `index` decides what it will touch first: sources that are unregistered,
  unplanned or stale are *excluded*, and every usable source's plan must cover its whole raw file ("Validation").
  An incomplete one refuses the whole run (`IncompleteCoverageError`, exit 2) unless `--allow-partial`; with it,
  the report lists `partial_coverage_sources`. `--all` runs the preflight before dropping collections, so a
  refusal never leaves an empty index.
- **Excluded sources are removed, not kept.** A full run drops every source group it did not write; `--source X`
  for an excluded X drops X's records and exits 1 with the reasons, because keeping them would serve line numbers
  that no longer match the raw text.
- **Embed before delete.** Per group, only records whose `record_hash` changed are re-embedded; the vectors are
  computed first, then obsolete and changed ids are deleted by id, then the changed records are added. A model
  download that fails mid-run leaves the group as it was.
- **Full vs partial stamp.** Collections carry `corpus_version` only from a full run (no `--source`, no source
  excluded). Any other run keeps the old `corpus_version` and sets `partial_since` (the time of the first partial
  run since the last full one), so `index_is_stale` stays true until the next full `index`. A full run clears it.
- **Exclusion is loud.** `index` exits 1 whenever it left a planned source out (stale, unregistered, or the
  `--source` asked for), logging each source and reason: such a run is never stamped full, and exit 0 used to
  hide that. `index.read_index_stamp` reads the stamp back (`IndexStamp.stale_against` says why an index does
  not reflect a corpus); the sync uses it before publishing an index ("Storage").
- **Do not run `index` while `serve` has the same cache open.** The server loads documents, metadata and BM25
  into memory at start and reads the Chroma directory per query; a concurrent writer can hand it records its
  in-memory view does not know. Stop the server, index, restart it (or index a copy of the cache).

## Lexical

Okapi BM25 (`rank_bm25.BM25Okapi`, k1 = 1.5, b = 0.75) is rebuilt in-process on load from `collection.get()`;
the corpus is small enough that rebuilding beats persisting a second index that could drift. Tokens are
lower-case alphanumeric runs minus a short stopword list, so `tons/acre` hits `tons` and `acre`. Only positive
scores rank. rank_bm25 floors negative IDF at `epsilon * average_idf`, which is only safe on corpora larger than
a handful of documents.

- **Light stemming, BM25 only** (`lexical.stem`, not Porter - a full stemmer is on the do-not-do list):
  plurals (`-s`, `-es` after a sibilant, `-ies` -> `-y`), `-ing`, `-ed` (not `-eed`), an undoubled final
  consonant (`cropping` -> `crop`, but `till` keeps its `ll`) and a final `-e`, never leaving a stem under 3
  letters. `burned` and `burnt` are never stemmed: "burned" names a post-fire site state and "burn" a
  practice (prescribed burning), and folding them sent burned-ground queries to burn-scheduling strategies.
  `tokenize` itself stays unstemmed (the test embedder hashes its tokens).
- **Expansion tokens** (`query_intent.py`) are scored as a second BM25 query and added at
  `DEFAULT_EXPANSION_WEIGHT` (0.3; BM25 is additive over terms, so this is exact); a token already in the query
  is not counted twice. They never reach the dense embedding.

## Retrieval

- **Weighted reciprocal-rank fusion** (Cormack, Clarke & Buettcher 2009, with per-list weights), k = 60:
  dense `DENSE_WEIGHT` = 1.0, BM25 `LEXICAL_WEIGHT` = 0.8, and, when a `context_query` is given, its own dense
  and BM25 rankings at `CONTEXT_QUERY_WEIGHT` = 0.5 times those. `search_findings` and `search_passages` fuse
  documents with the same weights.
- **Collapse before fusing** (`search_strategies`): each facet ranking becomes a strategy ranking (a strategy
  at its best facet's position) and the strategy rankings are fused. Dense and BM25 then agree at the strategy
  level even when they matched different facets (dense on the overview, BM25 on how-to), and one strategy's
  four facets no longer take four rank positions that push every other strategy down. Fusing documents first
  scored a strategy by its single best facet document, so that cross-facet agreement counted for nothing. The
  reported `matched_facet` is the facet with the best document-level fused score.
- **Query intent** adds capped soil-condition boosts and a pH demotion ("Query intent"). A hit boosted by the
  query's **pH condition** (`acidic`/`alkaline`) is exempt from the family-diversity cap and takes no family
  slot (lime and its carbon-removal variant share `soil-chemistry-correction` with sulfur; the cap used to hold
  one back). `Boosts.diversity_exempt_tags` deliberately narrows to the pH condition only (`query_intent.
  PH_CONDITIONS`), not every query-intent soil condition: a 2026-09-27 review found the unrestricted version
  exempted all ~40 `erodible`-tagged strategies at once on a bare "washed out" or "gully" mention, defeating
  family diversity on the erosion path exactly the way a site-profile exemption was already rejected below for.
  A site-profile soil boost does **not** exempt either: server-filled site facts (`low_organic_matter` from
  SOC < 1%) would lift the cap on 30-odd cover-crop and compost variants at once.
- **Measured weights.** Tuned only on the tuning-eligible sets (paraphrases, lay_language, ph_contrast,
  family_coverage); golden and the held-out agent set are reported, never tuned. MRR per set
  (paraphrase / lay / contrast / family) at dense 1.0 and expansion 0.3: BM25 0.6 -> .796 / .754 / 1.0 /
  .926; 0.7 -> .797 / .754 / 1.0 / .926; **0.8 -> .801 / .817 / 1.0 / .921**; 0.9 and 1.0 drop a lay hit
  (hit@5 .875). Expansion weight at BM25 0.8: 0.2 drops a lay hit, 0.3 = 0.4 on lay, 0.3 best on paraphrases.
  Fusion alone (no query intent) leaves ph_contrast forbidden@3 at 1.00 and lay MRR at .54: the gains are the
  intent layer plus the vocabulary, not the weights. `context_query` weight was first swept on a 16-item scratch
  set (a model-style rewrite, "improve soil chemistry", paired with the user's lay words, "my pasture ground is
  sour"): hit@5 / MRR / forbidden@3 = .750 / .554 / .00 at 0, .813 / .612 / .00 at 0.25, .938 / .645 / .00 at
  **0.5**, .938 / .640 / .00 at 1.0, 1.0 / .707 / .25 at 1.5 (with the 0.5 pH demotion); query text alone scores
  .563 / .406 / .50. Above 0.5 the context's own pH-trap ranking starts to win, and older turns in the context
  are a reason to keep it below the query. That scratch set was never committed; `eval/context_query.json`
  (committed 2026-09-27, a 2026-09-27 review's fix for the weight having no committed regression coverage) is
  the small durable stand-in - the same two directionless queries the golden/held-out sets already use ("improve
  soil chemistry", "fix soil chemistry") each paired with a lay `context_query` both directions, so a later
  fusion or intent change cannot silently regress the S3 production path (`context_query=user_question`) while
  every other eval file stays green.
- `context_query` is embedded by its **last** `CONTEXT_EMBEDDED_WORDS` (150) words: MiniLM keeps only its first
  ~256 word pieces, and the latest user turn comes last. BM25 reads all of it (<= 2000 characters).
- **Fixed candidate pool.** Each retriever contributes at most `STRATEGY_KB_CANDIDATE_POOL` documents (default
  300), whatever page is asked for, so page N and page N + 1 come from one ranking and are disjoint and
  contiguous. When a page reaches past what the pool yields after collapse and diversity *and* the pool did not
  hold every matching document (dense search returned a full pool), the response sets `truncated: true` and
  returns what exists. `ranked_candidates` counts the fused pool, not every document that could match.
- **Boosts are capped.** Each matched soil condition or fire phase adds `0.25 / (k + 1)`, each requested goal the
  record states (not infers) `0.125 / (k + 1)`, and the total is capped at `0.5 / (k + 1)` - half of one first
  place. A boost therefore reorders near-ties and never removes anything, but a record missing from one ranking
  (at best `1 / (k + 1)` + cap) can never overtake one both rankings put first (`1.8 / (k + 1)` with the
  weights above; a BM25-only record reaches at most `0.8 / (k + 1)` + cap). `boosted_by` names every key that
  matched, even past the cap; a pH demotion appears as its key prefixed with `-` (`-soil_alkaline`).
- The matched facet is reported with a 60-word snippet of its raw text.
- Family diversity (default 2 per family, 0 disables) runs after collapse and before paging; held-back ids are
  returned per family so `get_family` can open them. A strategy without a family is its own family.

## Query intent

`query_intent.py` reads the query plus `context_query` (contract seam S3: the user's verbatim question, which
agri forwards out of band) and returns `QueryIntent`, echoed as `query_intent` with `context_query_used` on
`search_strategies` and `search_findings`. It is a data table, not a model: `LAY_VOCABULARY` is keyed by the
`SoilCondition` enum, each entry holding lay phrases ("sour", "hardpan", "salty", "burnt", "washed out",
"won't grow") and the corpus words BM25 should also look for. The dense query text is never rewritten
(server-side query rewriting/HyDE is on the do-not-do list).

- **pH direction** is a weighted vote: an explicit phrase ("raise the pH", "pH is too low", "acidify",
  "apply lime", "adding sulfur") counts 2; a bare condition word ("acidic", "alkaline") or a lay pH word ("sour",
  "acid-loving") counts 1; a tie is no direction. "lime"/"sulfur" only count next to an application verb
  (`apply`, `add`, `use`, `amend`, `spread`, `treat`, `need` ± up to two filler words, or "lime/sulfur
  application/treatment/amendment") — a 2026-09-27 review found bare mentions false-positive on "lime-induced
  chlorosis", "free lime" (excess lime is the opposite problem lime treats) and "sulfur deficiency" (a nutrient
  issue, not a pH amendment); none of those phrasings carry an application verb, so they now score 0. The same
  review found "getting more acidic" is a symptom lime corrects, not a request to acidify further, so it counts
  toward raise, not lower; a bare "more acidic" no longer counts either way (too ambiguous). Bare crop names
  ("blueberries") were dropped as a soil-condition cue entirely: they fire on any mention of the crop regardless
  of intent (a query about blueberry irrigation is not a pH query). "sour" excludes the produce sense ("sour
  cherry", "sour orange") via `_SOUR_FALSE_FRIENDS`, so it still means soil taste everywhere else. Raising pH
  boosts `acidic` (lime targets acidic soil),
  lowering boosts `alkaline` (sulfur), and the *opposite* condition is demoted by `PH_DEMOTION_WEIGHT` = 0.5
  unit unless the record also carries the boosted one. The demotion exists because only two strategies carry
  `alkaline`: a lower-pH query otherwise still ranked lime third on its shared "pH" vocabulary
  (ph_contrast forbidden@3 stayed .50 with boosts alone). "fix soil chemistry" has no direction and gets none:
  only the user's own words (context_query) can say which way.
- **Boosts are capped**: at most `MAXIMUM_INTENT_BOOSTS` = 2 conditions, the pH one first, then table order;
  each is an ordinary `BOOST_WEIGHT` tag under the same `MAXIMUM_TOTAL_BOOST` ceiling as site-profile boosts.
  A weak cue ("won't grow") acts only when nothing else was named. Terrain words ("hillside", "steep")
  expand BM25 but boost nothing: the corpus's `steep_slope` tags sit on erosion structures, and boosting them
  sent "replant trees on a hillside" and "native grass on a burned hillside" to contour logs (family_coverage
  MRR .926 -> .884 with the boost). "burned" and "after the fire" are not lay terms: they are the corpus's
  own words, and boosting `burned_high_severity` on them pulled erosion barriers above snag removal and
  cheatgrass control on golden queries.
- **Expansion tokens** (at most 12) go to the query's BM25 ranking only, at `DEFAULT_EXPANSION_WEIGHT`.
- `search_findings` echoes an empty `soil_condition_boosts` (findings carry no soil tags, as in the
  site-profile echo) but still uses the expansion tokens and the context ranking.

## Site profile

DESIGN section 10 thresholds live as named constants in `site_profile.py`. Inputs accepted beyond the table:
`burn_severity` as words (`high`, `moderate`, `low`; `unburned` means none) or MTBS classes 2-4; `land_cover` as
an NLCD class name or code. Wetland words are matched before herbaceous words because "Emergent Herbaceous
Wetlands" contains both. Unmapped classes (open water, barren, ice) produce a note and no filter. Every search
response echoes filters, boosts and one note per fired rule. `search_findings` applies only the stated-goal
boost (findings carry no soil or phase tags), so its site-profile echo shows empty soil/phase boosts plus a note
saying they were not applied, rather than listing boosts that did nothing.

## MCP server

`server.py` builds an `mcp.server.mcpserver.MCPServer` - **mcp 2.x renamed FastMCP to MCPServer** and
`mcp.server.fastmcp` now raises on import. Tools are sync functions (the SDK runs them on a worker thread),
built once as SDK `Tool`s (`Tool.from_function`, the call `MCPServer.add_tool` makes) into a `ToolTable` that
`MCPServer(tools=...)` registers and the HTTP route dispatches to ("HTTP transport"), with
`structured_output=False` (the JSON dict goes back as text content) and read-only `ToolAnnotations` (from the
SDK's own `mcp_types` package). Parameter enums and descriptions come from
`Annotated[..., Field(...)]`; the tool description is the cleaned docstring, which is what the calling model
reads, so it says what comes back and which tool to call next. A `knowledge_base.RequestError` (for example
`compare_strategies` given one strategy under two ids) becomes a `ToolError`, which the SDK returns as an
`is_error` result carrying the message.

| tool | parameters |
|---|---|
| `list_facets` | - |
| `search_strategies` | query, goals[], land_use[], region[], soil_conditions[], scale[], category[], fire_phase[], site_profile{}, min_evidence, context_query (<= 2000 chars), family_diversity=2, limit=10, offset=0 |
| `get_strategy` | ids[] |
| `get_family` | family_id |
| `compare_strategies` | ids[] (>= 2, schema `minItems: 2`; an id and its alias count once, so fewer than 2 distinct is an error) |
| `search_findings` | query, goals[], land_use[], region[], site_profile{}, min_evidence, study_type[], direction, strategy_id, context_query (<= 2000 chars), limit, offset |
| `search_passages` | query, source_id, content_type[], relevance[], strategy_id, limit, offset |
| `list_sources` | - |

Every response carries `claim_tier: "literature_grounded"`, the index's `corpus_version`, `index_is_stale`, and
a `review_state` per record (sources included). DESIGN names `review_state` without defining its values: a
record's own `review_state` is passed through; otherwise strategies, findings and sources report
`machine_extracted` (excerpts machine-verified, not human-reviewed) and passages `verbatim_source_text`.
`search_findings` also accepts `strategy_id` (additive to DESIGN section 11) so an agent can ask for the
evidence behind one strategy. `get_strategy`, `get_family` and `compare_strategies` return each canonical
strategy once, however many of its aliases were asked for. `search_findings` / `search_passages` answer a
`strategy_id` no registry id or alias resolves, or a `source_id` not in `sources.json`, with `results: []` and
the id in `not_found` (as `get_strategy` does); every other response carries `not_found: []`. A silent empty
page used to be indistinguishable from "the corpus has nothing on this".

## stdout is the transport

On stdio, NOTHING but protocol frames may reach stdout; one stray print desynchronises the client for the rest
of the session (measured twice in agri-data-service, see its `agent/AGENTS.md`). Two layers guard it here:

1. mcp 2.x `stdio_server()` claims fd 1 itself: it serves the wire from a private duplicate and points fd 1 at
   stderr, so even native-library writes miss the wire. It only claims fd 1 **if `sys.stdout` is still backed by
   fd 1** - rebinding `sys.stdout` to stderr *before* `run()` would make the SDK write the protocol to stderr.
2. So nothing that could print runs before `run()`. The server's **lifespan**, which the SDK enters inside
   `stdio_server()` (after the claim), rebinds `sys.stdout` (`streams.reserved_stdout`) for the session and only
   then opens the knowledge base and warms the embedder on a worker thread - the index load and a first-run ONNX
   model download both happen after the transport owns the wire. Tools read the opened knowledge base from the
   lifespan's slot.

CLI leaves that print machine-readable JSON (`index`, `eval`, `status`, `sync`, `import-chunked`, `bootstrap`,
`materialize`) write it through `reserved_stdout` too. Logging goes to stderr.

## HTTP transport

`strategy-kb serve --transport http --host H --port P [--pull-from-bucket]` (`http_app.py`) serves one Starlette
app under uvicorn. `--transport stdio` is the default and is what `.mcp.json` runs; without `--pull-from-bucket` its
loader is exactly `open_knowledge_base(settings)`, as before. The contract is `.omc/ultrapilot-strategy-integration-20260926/CONTRACT.md`
section C1 (git-ignored, so restated here).

| route | answer |
|---|---|
| `GET /health` | 200 `{"status":"ok"}`, always; liveness, never touches the knowledge base |
| `GET /ready` | 200 `{"status":"ready","corpus_version":<hex>,"index_is_stale":false}` once loaded with a full, fresh index; otherwise 503 `{"status":"not_ready","reason":<str>}` |
| `POST /v1/tools/{tool_name}` | body = the MCP tool's arguments as a JSON object (an empty body = no arguments). 200 = the tool's MCP JSON text, byte for byte. 400 `{"error":"invalid_arguments","detail"}`, 404 `{"error":"unknown_tool","tools":[...]}`, 413 `{"error":"request_too_large","limit_bytes":32768}`, 503 `{"error":"not_ready"}`, 500 `{"error":"internal_error"}` (traceback logged, never returned) |
| `/mcp` | MCP streamable HTTP with the same eight tools, stateless, JSON responses |

- **One tool table, no second copy of any tool.** `server.build_tool_table` builds each tool once; the HTTP route
  calls `ToolTable.call`, which validates with the same SDK argument model the MCP path uses
  (`fn_metadata.validate_arguments`, including the SDK's pre-parse of JSON-encoded strings), runs the same tool body
  on a worker thread, and `ToolTable.render` serialises with the SDK's own converter (`fn_metadata.convert_result`,
  which is `pydantic_core.to_json(..., indent=2)`). `tests/test_http_app.py` pins byte equality for all eight tools.
- **One rule stricter than MCP:** an argument name the tool does not have is a 400 (`unknown argument(s) [...]`),
  where the SDK's argument model silently drops it. A misspelt filter (`goal` for `goals`) would otherwise run
  unfiltered and come back looking like a real answer. Every accepted call returns the MCP payload unchanged.
- A `knowledge_base.RequestError` (for example `compare_strategies` given one strategy under two ids) is a 400
  carrying the message the MCP tool error carries. Validation details name the argument and the rule, never the
  rejected value.
- **Lifespan.** The app's lifespan enters the MCP session manager's `run()` (a mounted app's own lifespan never
  runs, and `run()` works once per manager). That enters the MCP server lifespan, the same one stdio uses, which
  runs the loader on a worker thread: the optional bucket pull, then `open_knowledge_base` (index load, ONNX
  warm-up). uvicorn binds the port only after startup completes, so while loading, a caller sees connection refused;
  the 503s exist for the gap before the lifespan runs (tests) and for a loaded-but-stale index.
- **`--pull-from-bucket`** runs `sync pull --with-index` (`server.pull_published_corpus`) inside the loader and
  raises `ServingRefusedError` on a failed pull (conflict, refused key, index refusal), on no index after the pull
  (nothing published under `index/LATEST`), or on an index that is partial or stamped for another corpus
  (`KnowledgeBase.index_staleness`, the `IndexStamp.stale_against` rule). uvicorn runs with `lifespan="on"`, so a
  startup failure exits 3 and the deploy fails instead of serving half a corpus. Pulling in-process is safe despite
  "Storage"'s own-process rule: nothing opens a Chroma client on the cache before the pull swaps `chroma/` in. Over
  stdio the flag works too, inside the lifespan (after the fd 1 claim).
- **Host validation, `/mcp` only.** The SDK's DNS-rebinding guard answers 421 for a Host not on the list: loopback
  (`127.0.0.1`, `localhost`, `[::1]`), `plantgeo-strategy-knowledge.railway.internal` (contract C2), the
  `RAILWAY_PRIVATE_DOMAIN` Railway injects, and `STRATEGY_KB_ALLOWED_HOSTS` (comma-separated), each bare and with any
  port. Origins: loopback only; server-to-server callers send none. `/health`, `/ready` and the tool route check no
  Host: Railway's healthcheck arrives with its own Host, and the tool API is read-only on a private network.
- **Stateless JSON MCP** (`stateless_http=True`, `json_response=True`): no session state lost on a redeploy, no
  session affinity, one JSON body per POST. Stateless mode only gives up server-to-client requests, which no tool
  makes.
- Body cap 32 KiB: the tool route checks the declared Content-Length and the streamed size; `/mcp` uses the SDK's
  `max_request_body_size`. Real arguments are a few hundred bytes.
- `log_config=None`: uvicorn's access and error logs go through the CLI's stderr `basicConfig`.
- Quirk: `GET /v1/tools/x` is a 404, not a 405, because the MCP app is mounted at the root and matches every path.

## Observability

HTTP mode logs one compact JSON line per request to real stdout (`log_access_event`, `http_app.py`) - never a
query, a passage or any request/response body, only the outcome, timing and identifiers:

- `mcp_request` for every `/mcp` call: `status`, `ms`, `request_id`.
- `tool_call` for every `POST /v1/tools/{tool_name}` call, on **every** exit path including a 4xx/5xx and an
  unhandled exception (`AccessLogMiddleware`/`call_tool`'s `finally`, not just its normal returns - a client
  disconnect mid-read used to leave no line at all): `tool`, `status` (`ok`, `invalid_arguments`, `not_ready`,
  `unknown_tool`, `request_too_large`, `internal_error`, or `aborted` for anything that reached the `finally`
  without a status), `ms`, `corpus_version` (the currently loaded one, or `null` before load), `request_id`.
  There is no per-tool line on the `/mcp` path - `mcp_request` covers the whole MCP call, not each dispatched
  tool inside it, because `/mcp` runs through the SDK's own session manager, not `call_tool`.
- **Request id.** `X-Request-ID` is echoed if the caller sent one; a caller value must match
  `[A-Za-z0-9._-]{1,128}` or it is replaced with a generated `uuid4().hex` - an unbounded or odd-charset header
  would otherwise land in every log line for the request's lifetime.
- **`limit_concurrency`** (`DEFAULT_LIMIT_CONCURRENCY` = 16, env `STRATEGY_KB_LIMIT_CONCURRENCY`, validated to
  (0, 512]; an invalid or out-of-range value falls back to the default) is uvicorn's own concurrent-request cap:
  requests past it get a 503 immediately rather than queuing behind a slow tool call. It counts accepted HTTP
  connections, not tool calls in flight on the worker-thread pool.

## Deploy (Railway)

Service `plantgeo-strategy-knowledge` in project Aevani, environment production (contract C2): root directory
`/services/strategy-knowledge`, Dockerfile builder, healthcheck `/ready`, watch paths `/services/strategy-knowledge/**`.
**Private network only, no public domain** (owner decision 2026-09-26). The one caller is `plantgeo-parquet-api`
(`STRATEGY_KNOWLEDGE_URL=http://${{plantgeo-strategy-knowledge.RAILWAY_PRIVATE_DOMAIN}}:8000`); `/mcp` and the tool
API have no auth; and `search_passages` returns verbatim copyrighted source text, which a public domain would hand
to anyone.

Variables: `PORT=8000`, pinned, because Railway otherwise injects its own `PORT` (8080) while the private URL and
target port say 8000 - the ml-service hit exactly that as a 502. `STRATEGY_KB_CACHE_DIR=/var/lib/strategy-knowledge`.
The five `OBJECT_STORE_*` as reference variables to `plantgeo-parquet-api`.

The image (`Dockerfile`) has one stage:

- **`runtime` stage:** `--no-dev`, user `strategykb` (uid 10001) with a real, writable HOME `/home/strategykb`.
  Quality checks are ad hoc, never a build gate (owner decision 2026-08-07: "a lint failure must never fail a
  Railway deploy"): run `uv run pytest -q`, `uv run ruff check` and `uv run ruff format --check` before pushing;
  none of them block a deploy.
  Chroma's `ONNXMiniLM_L6_V2.DOWNLOAD_PATH` is `Path.home()/.cache/chroma/onnx_models/all-MiniLM-L6-v2`, fixed at
  import. The build embeds one string as that user through `embedder_for`, so the model is baked into the image and
  a start never downloads it; the 80 MB archive is deleted in the same layer (Chroma checks only the extracted
  files). The sibling services' `--home-dir /nonexistent` would make every start download the model, or fail.
- `STRATEGY_KB_CACHE_DIR` is empty in the image: no raw text is ever baked in (`.dockerignore` drops `.cache/` and
  `.env`). There is no volume; every start pulls the corpus and the published index, and the cache is disposable.
- Build-time probes: the model file exists, and the package and `http_app` import.
- CMD: `exec strategy-kb serve --transport http --host "${STRATEGY_KB_BIND_HOST:-0.0.0.0}" --port "${PORT:-8000}"
  --pull-from-bucket`. The bind host defaults to the contract's `0.0.0.0` (IPv4). Railway's private network is
  IPv6-only in older environments: if `plantgeo-parquet-api` calls time out while `/ready` is green, set
  `STRATEGY_KB_BIND_HOST=::`. Under asyncio that socket is IPv6-only (`create_server` sets `IPV6_V6ONLY`), so
  confirm the healthcheck still passes after the switch.
- **Rollout order:** the bucket must hold the corpus AND a published index before the first deploy
  (`strategy-kb sync push --with-index` from a cache whose index is full and fresh); otherwise the first start fails
  its pull, by design.

## Storage

`BucketSync` mirrors `raw/` and `corpus/` with the bucket prefix (`STRATEGY_KB_PREFIX`, default
`strategy-knowledge/`) using the agri-data-service variable names (`OBJECT_STORE_*`; the bucket name is the S3
name, not the display name). Additive both ways: nothing is ever deleted remotely or locally, because a sync
that deletes is one typo from wiping copyrighted raw text nobody can re-fetch.

**Three-way.** `sync_manifest.json` (local only) records, per key, the remote ETag and local MD5 at the last pull
or push. Each key is classified against it: identical, one-sided, changed on one side, or changed on both
(a conflict; also any difference with no manifest entry, since then nobody knows which side is newer).

- pull downloads remote-only and remote-changed keys, and keeps (reports `kept_local_changes`) every local file
  changed since the last sync - it never overwrites an unpushed local change;
- push uploads local-only and local-changed keys, and keeps (`kept_remote_changes`) every object changed
  remotely since the last sync - it never overwrites another machine's push;
- a conflict is reported and the command exits 1 unless `--force-local` or `--force-remote` picks a side. A
  side picked against the direction of travel (keep local on a pull, keep remote on a push) is recorded so the
  next sync the other way applies it without asking again.

**All or nothing on conflict, registries last.** Every key is classified before anything moves. With no side
chosen, one conflict anywhere stops the whole transfer in that direction: nothing is uploaded or downloaded
(a half-applied sync would pair new plans with old findings). Otherwise keys move in `storage.publish_rank`
order - `raw/`, then chunk plans and findings, then `corpus/strategies/`, and `corpus/sources.json` last - so a
transfer that dies part-way never publishes a registry naming a raw file or plan that did not arrive; a raw
file or plan the registry does not list yet is inert (`index` excludes unregistered sources).

**`--corpus-only` and the bare-push guard.** Once a corpus+index has ever been published, `sync push` (no
`--with-index`) refuses outright when the bucket's published `corpus_version` (read from `index/LATEST`) would
diverge from the local corpus - normal pushes cannot silently leave the served index stamped for a corpus that
no longer matches what the bucket holds. `--corpus-only` is the explicit override: it uploads the corpus change
anyway and accepts that the server refuses to serve (`index_staleness`) until a matching `--with-index` push
follows. `--with-index` (the atomic publish `command_sync` and the skill's step 8 use) needs no such guard: it
checks the local index's own freshness and every key's three-way state *before* the first byte moves, then
uploads in one fixed order - the index archive, the corpus (`sources.json` last), `index/LATEST` last of all -
so a run that dies part-way never leaves LATEST naming a version whose files did not fully arrive. If the local
index is unpublishable, or the corpus would diverge (a remote-only/remote-changed key, an unsafe rejected key,
or a conflict resolved for the remote), or `index/LATEST` itself changed remotely since the last sync, the
**whole push refuses and uploads nothing** - not just the index: local-changed corpus keys are held back too
(`BucketSync._push_key`'s `upload_local` flag), so a partial corpus can never sit behind a refused index. Pull,
re-index and push again; `--force-local`/`--force-remote` settle an ordinary conflict but never override a
divergence refusal.

Uploads are single-part PUTs so a key's ETag stays its MD5. **Key safety:** a remote key ending in `/`
(a directory marker) or resolving outside the cache is refused (`rejected_keys`, exit 1) and never written, and
`index/LATEST` must name a 64-hex corpus version. Secrets are `SecretStr` and `Settings.__repr__` names only
which variables are set.

**Index sync (`--with-index`).** A published index must be exactly the index of the corpus the bucket holds, so
a push publishes it only when all of these hold, and otherwise records the reason in `index_refusals` (exit 1)
and uploads nothing under `index/`:

- the local index is complete and fresh: all three collections exist, no `partial_since`, and its own stamped
  `corpus_version` equals the local corpus (`IndexStamp.stale_against`);
- the corpus push left nothing unsynced: no conflict, refused key, kept remote change, or conflict settled for
  the remote - otherwise the bucket's corpus is not the one the index was built from;
- `index/LATEST` passes the same three-way check as corpus keys, against the same manifest: changed remotely
  since the last sync (another machine published) is refused, never overwritten; changed on both sides is a
  conflict that blocks the whole push unless `--force-local`. A first push over an existing LATEST with no
  manifest entry is such a conflict, because nobody knows which is newer.

The archive is labelled with the **index's own** stamped `corpus_version` (`index/<version>/chroma.tar.gz`),
uploaded before LATEST so LATEST never names a missing archive. A pull with no LATEST in the bucket (nothing
published yet) is a no-op that keeps the local index; a LATEST naming an archive the listing lacks is a
`rejected_keys` entry, never a crash. Otherwise a pull reads LATEST, downloads the archive and
accepts it only if **every** member is a regular file or directory under `chroma/` with a relative path and no
`..`, backslash or drive; anything else (absolute paths, links, `corpus/`, `raw/`, `sync_manifest.json`) refuses
the whole archive (`rejected_keys`) and touches nothing. An accepted archive is extracted into a scratch
directory inside the cache, then swapped in by two renames (the old `chroma/` is restored if the second fails).
The pull opens no Chroma client. Chroma caches one `System` per path, so a client opened on `chroma/` before
the swap would keep reading the retired directory. That is why `serve` runs as its own process after a pull.
The pulled LATEST is recorded in the manifest so the next push can tell a local rebuild from a remote one.

## Fetching

`add-source` streams the response: the body is refused (`FetchError`) the moment it passes 80 MiB (checked on
decoded bytes, so a compression bomb stops too, and up front from `Content-Length`), and the whole download has a
180 s wall-clock deadline on top of httpx's 30 s per-operation timeout (the deadline is checked between pieces,
so one stalled read can overrun it by at most that timeout). HTML is decoded from the capped bytes with the
header charset (UTF-8 otherwise). Every httpx error, a pypdf failure on a malformed PDF, and an unknown charset
become one `FetchError`, which the CLI prints as one line.

## Append pipeline (runbook)

DESIGN section 13, with the commands that implement it:

```
strategy-kb sync pull                           # start from the bucket's corpus
strategy-kb add-source <url>                    # fetch, extract, source_id, hash, write raw, register
#   prints the brief (briefs/chunk_source.md), how its slice vocabulary maps onto this source, the raw path and
#   line count, and the work dir <cache>/work/<source_id>/ holding strategy_ids.txt and slice_assignments.json
#   (slices S1..Sn of <= ~1,500 lines each, cut at blank lines; one slice S1 for a short source)
#   AGENT STEP, one agent per slice: chunk its lines per the brief -> <cache>/work/<source_id>/S<i>.json
strategy-kb import-chunked <work>/S<i>.json --assignments <work>/slice_assignments.json   # once per slice
strategy-kb validate --source <source_id>       # whole-file coverage, enums, verbatim excerpts, links
#   REGISTRY STEP (briefs/registry.md): reconcile candidate_strategies into
#   corpus/strategies/strategy_registry.json; new strategies get facets per briefs/facets.md
strategy-kb index --source <source_id>          # a partial update: index_is_stale stays true
strategy-kb index                               # full update: stamps corpus_version, clears partial_since
strategy-kb sync push [--with-index]
```

- The briefs speak the research wave's language. For an appended source: "assigned line ranges" = the agent's
  slice in `<work>/slice_assignments.json`, `briefs/strategy_ids.txt` = `<work>/strategy_ids.txt`,
  `chunked/<slice_id>.json` = `<work>/<slice_id>.json` (with `"slice_id"` set to the slice's name), and
  DESIGN.md / strategy_schema.md are restated here and in `vocabulary.py`. `add-source` prints this mapping and
  one import command per slice with the paths filled in.
- **Agent slices.** One agent reads about `LINES_PER_CHUNKING_AGENT` (1,500) raw lines at most, so
  `add-source` splits a longer source into n = ceil(N / 1,500) near-equal slices, each ending on the last blank
  line within half a slice of its boundary (a slice boundary is a forced chunk boundary, so it should not cut a
  paragraph). The slice ids are deterministic (S1..Sn from the line count), so a re-chunk of an unchanged source
  reuses them.
- `source_id` = `<yyyymmdd>-<host>-<slug>` (first host label after `www.`, capped at 40 characters; last
  non-numeric path segment, falling back to the title). A second URL that would collide gets a 6-hex URL digest,
  still no sequence number (DESIGN section 2).
- Re-running `add-source` on an unchanged URL is a no-op (the body is compared without the header, whose
  `fetched_at` always changes). A changed body rewrites the raw file under the same `source_id`, and freshness
  then marks its plans stale. A URL that is registered but whose raw file is not in the local cache is
  **refused before fetching** ("run `strategy-kb sync pull` first"): re-fetching would replace the stored text
  and hash that the plans' line numbers belong to.
- `import-chunked` refuses a block with any validation problem unless `--accept-problems`, and a rejected block
  writes nothing - its `source_record` registration is staged on a copy of `sources.json` and committed only with
  the block. Without `--assignments`, a source's assignment is its whole file. Importing a block replaces only
  what lay inside its assigned ranges, so a source split across slices can be imported slice by slice. With
  `--assignments`, the JSON also lists `slices_without_output`: slices the assignments file names that have no
  `<slice_id>.json` beside the imported file.
- **The slice id is resolved once.** `ingest.resolve_slice_id` = the document's own `slice_id`, else its file
  stem; the CLI and `bootstrap` both call it and pass the result to `import_chunked` explicitly, which refuses an
  empty one. So a slice-less document is keyed by its file name, never by None, and two slice-less documents for
  one source keep separate candidates. Plans written before this rule may hold `origin_slice: null`; they still
  load, and such candidates are kept by a partial re-import and replaced by a whole-file one.
- **Candidate key.** Stored candidates are keyed by `(origin_slice, strategy_id)`: several slices of one source
  may each propose the same strategy, and the registry step sees each proposal. Re-importing a slice replaces
  that slice's candidates only - **unless its assignment is the source's whole file** (every add-source import of
  a short source, a whole-file bootstrap slice), when it replaces every stored candidate of the source: that
  slice owns every line, so a re-chunk under a new slice id must not leave the old slice's candidates behind.
- **Candidate folding.** The chunk brief asks for one candidate per strategy per slice, but agents repeat them.
  `merge_candidates` folds repeats without weakening either copy: citations and list fields (`actions`,
  materials, benefits, ...) are ordered unions; per goal `stated` beats `inferred`; `evidence_strength` keeps the
  higher `EVIDENCE_RANK`; single-value fields (name, summary, category, timing, slope_guidance, cost_level,
  labor_intensity, time_to_effect, nrcs_practice_code, proposed_family) keep the first non-empty value;
  `application_rate` and `notes` keep every distinct text joined with ` | `. Two different non-null
  `matches_existing` targets are never folded: validation reports them, and the merge raises
  (`CandidateConflictError`), so the block is rejected even with `--accept-problems`. The old fold let the first
  copy's `inferred` goal and weaker evidence override a later copy's `stated` goal and peer-reviewed evidence.
- `index` with no flag updates every usable source and removes excluded ones ("Indexing"). `--all` drops and
  rebuilds. `--allow-partial` indexes sources whose coverage is incomplete.
- One-time seeding from the research directory: `strategy-kb bootstrap .omc/research/wildfire-ag-strategies-20260926`
  copies raw files, registers SourceRecords from `extracted/*.json`, imports every `chunked/*.json` whatever its
  slice id (P/C/S/W/U, the deterministic `D1` that covers every line no agent slice owns, or any other) with
  `briefs/slice_assignments.json` and `briefs/strategy_ids.txt`, and copies `registry/*.json`. A chunked file
  whose slice the assignments file does not list is rejected (a missing assignment must not become the whole
  file). It exits 1 on any rejected block and when `slices_without_output` (assigned slices with no chunked file
  yet) is non-empty.

## Evaluation

`strategy-kb eval --golden FILE` runs a `GoldenQuery` list (`evaluation.py`) through `search_strategies` by
default, or `search_findings`/`search_passages` when `kind` is `"findings"`/`"passages"` (`filters` takes any of
that endpoint's own parameters and is validated), and reports hit@k, MRR@k (the reciprocal rank of the first
expected id within the top k), a `forbidden_at_3` violation rate (an id in `forbidden_at_3` must not rank in the
top 3 - the raise/lower-pH contrast pairs use this), a per-`kind` breakdown, and per-family coverage. Expected
and forbidden ids are first resolved through the knowledge base's alias map (registry `merged_from`; "Strategy
aliases"), because search returns canonical ids only: a golden file written before a registry merge would
otherwise score a miss for the very strategy that absorbed its id. `expected_any_of` and `forbidden_at_3` may
never share an id (`GoldenQuery` rejects it at load) - one item cannot be both a right answer and a violation.
`context_query` forwards onto the request's own field (contract seam S3) once that field exists on the target
model (`StrategySearch`/`FindingSearch`; never `PassageSearch`), so a golden file written ahead of the retrieval
change still validates.

`eval/` holds every eval file, one `GoldenQuery` list each, scored the same way; see `eval/README.md` for the
full catalogue (paraphrases, lay language, pH contrast, family/findings/passages coverage, the committed
`context_query.json`, and the HELD-OUT `agent_queries_heldout.json`, drawn from real agent transcripts and never
tuned against). `tests/fixtures/golden_example.json` is only a two-query format example, not a real eval file.

Beyond `golden_queries.json`, `eval/` holds seven more files sharing the same `GoldenQuery` schema
(`evaluation.py`): `paraphrases.json` (3 reworded variants of each golden query), `lay_language.json` (farmer
vocabulary: sour, hardpan, hillside, burnt, washed out, salty, "won't grow anything"), `ph_contrast.json`
(raise- vs lower-pH pairs, lime vs elemental sulfur, both directions), `family_coverage.json` (one query per
strategy family the original golden set never tested), `findings_coverage.json` / `passages_coverage.json`
(coverage for `search_findings` / `search_passages`, selected by the item's `kind`), and
`agent_queries_heldout.json`.

Each `GoldenQuery` also carries: `kind` (golden | paraphrase | lay | contrast | family | agent | findings |
passages; "findings"/"passages" select that endpoint, everything else scores `search_strategies`);
`forbidden_at_3` (ids that must NOT rank in the top 3 - the pH contrast pairs use it to catch the
wrong-direction strategy); and `context_query`, forwarded into the request only once the target request model
declares that field (seam S3), so a golden file written ahead of the retrieval change still validates and
records `context_query_sent: false` per row until it lands. `evaluate()` reports hit@k / MRR / forbidden@3
overall, a `by_kind` breakdown and `family_coverage` (tested/untested families + per-family hit rate), the last
read off the live searcher's `.strategies` registry (None for a test double with none).

**HELD-OUT - do not tune against it.** `agent_queries_heldout.json` holds the verbatim `query` arguments real
agent runs sent to `search_environmental_strategies`, labelled against each scenario's hand-authored
`expected_family_ids` (never the transcript's retrieved ids, which would be circular). It only confirms that a
retrieval change generalizes past the tuning-eligible sets; see `eval/README.md` and `eval/BASELINE.md`.

Retrieval wave 2 (query intent, strategy-level weighted RRF, family text + registry search terms in BM25, light
stemming, the sulfur facet rewrite), local corpus `fb95535b...` against the `eval/BASELINE.md` numbers
(corpus `5e1f53dc...`), hit@5 / MRR / forbidden@3:

| file | baseline | wave 2 |
|---|---|---|
| `golden_queries.json` (reported, not tuned) | 1.000 / .833 / .00 | 1.000 / .861 / .00 |
| `paraphrases.json` | .931 / .739 / .00 | .986 / .801 / .00 |
| `lay_language.json` | .750 / .750 / .00 | 1.000 / .817 / .00 |
| `ph_contrast.json` | 1.000 / .875 / **.75** | 1.000 / 1.000 / .00 |
| `family_coverage.json` | 1.000 / .931 / .00 | 1.000 / .921 / .00 |
| `findings_coverage.json`, `passages_coverage.json` | 1.000 / 1.000 / .00 | 1.000 / 1.000 / .00 |
| `agent_queries_heldout.json` (HELD OUT) | 1.000 / .708 / **1.00** | 1.000 / .667 / .50 |

The held-out forbidden@3 left is "fix soil chemistry": no direction exists in that text, and the user's "sour
pasture" reaches retrieval only as `context_query`. Its MRR moved -.04 on 10 queries (one rank-2 -> rank-3
change is -.017), mostly broad erosion/cheatgrass scenarios. The new code over the *published* corpus and
index (`5e1f53dc...`, no search terms, old sulfur facet) serves without error: golden 1.000 / .856, paraphrases
.931 / .778, lay .875 / .713, ph_contrast 1.000 / 1.000 / .00, held-out 1.000 / .633 / .50.

## Testing

`tests/conftest.py` injects `HashingEmbedder` (feature hashing into 64 dimensions), so no ONNX model is
downloaded and results are deterministic, and provides a `knowledge_base` built over a real persistent Chroma
index in `tmp_path`. Fixtures are a synthetic raw source written for the tests (real source text is copyrighted
and lives only in the bucket) plus five real StrategyRecord shapes from the research extraction, re-cited to
that synthetic text; tests derive variants (a registry with a `north_america_general` record, a raw file ending
in a newline, a half-chunked source) in `tmp_path`.

| file | covers |
|---|---|
| `test_end_to_end.py` | every knowledge-base operation; corpus_version is an independently recomputed content hash |
| `test_knowledge_base.py` | page consistency, truncation, region hierarchy, aliases, echoes, malformed rows, facet documents |
| `test_index_lifecycle.py` | stale source removal and exit code, re-chunking, full/partial stamp, coverage gate, embed-before-delete, trailing newline |
| `test_server.py` | the MCP surface over the SDK's in-process `Client(server)`: list_tools and one call of each tool, claim_tier on each |
| `test_http_app.py` | the HTTP transport via Starlette's `TestClient`: every tool's body byte-identical to its MCP text, `/health`, `/ready` before and after the lifespan and on a stale index, 400/404/413/500 bodies, `/mcp` mounted behind host validation, the pull-first loader against a fake bucket, `serve` flags and dispatch |
| `test_storage.py` | three-way sync against a fake S3 client, conflicts and force flags, all-or-nothing on conflict, registries last, unsafe keys, index publishing refusals, unsafe index archives |
| `test_fetch_and_ingest.py` | source ids, bounded fetch (httpx `MockTransport`), add-source refusal and agent slices, import staging, assignments, slice-id resolution, candidate keying and folding, bootstrap |
| `test_validate.py`, `test_filters.py`, `test_search.py`, `test_materialize.py`, ... | the pure units |

Per the repo rule, authors do not run the suite; one sweep runs at the join (`uv run pytest`,
`uv run ruff check .`, and `uv run ruff format --check .`). These checks are ad hoc, not a build gate
("Deploy (Railway)"), so run them before every push.

## Open items

- The registry (reconcile) step is an agent/registry-lane step, not a CLI verb; `import-chunked` stores
  `candidate_strategies` in the chunk plan for it.
- The repo's `.mcp.json` registers the server as `strategy-knowledge`: `command: uv`,
  `args: [run, --no-sync, strategy-kb, serve]`, `cwd: services/strategy-knowledge`, `UV_NO_SYNC=1`. The cwd
  matters because `uv run` resolves the project from it; `--no-sync` keeps a session start from re-resolving the
  environment. The default cache and `.env` resolve from the package's own location (`config.SERVICE_ROOT`), not
  from the cwd. (`anyio`, which `server.py` imports directly, is declared in `pyproject.toml`.)
- A partial re-chunk that changes how many agent slices a source has (S1..S3 before, S1..S2 now, same raw file)
  keeps the dropped slice's candidates until a whole-file import; `validate` still passes. Re-chunk a source
  whole, or with the same slices.
- **Retrieval next steps** (after wave 2, "Retrieval" / "Query intent"): commit the context_query scratch set
  as an eval file; family_coverage MRR sits .01 under baseline (forest-vulnerability and crop-rotation queries
  moved rank 2 -> 3); lay vocabulary beyond pH/salinity/compaction has no registry `search_terms` yet.
