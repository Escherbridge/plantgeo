# Implementation Plan: RAG Recommendation Engine

## Overview

This plan builds the AI-driven recommendation engine in 7 phases. Phases 1-2 establish the knowledge base and embedding pipeline. Phases 3-4 build the deterministic scoring engines. Phases 5-6 build the RAG retrieval and LLM synthesis. Phase 7 wires the PlantGeo frontend integration.

**Estimated total effort:** 32-40 hours across 7 phases.
**Dependencies:**
- Track 1 (agri_data_service_scaffold_20260324) must be complete (models, migrations, API skeleton)
- Track 2 (data_ingestion_pipeline_20260324) Phase 1 must be complete (Celery infrastructure)
- Track 2 Phases 2-6 should be complete for realistic end-to-end testing (can use mock data otherwise)

---

## Phase 1: Knowledge Base Document Ingestion

**Goal:** Build a CLI-driven document ingestion pipeline that loads PDF, Markdown, and text documents into a tracked knowledge base with metadata.

### Tasks:

- [ ] Task 1.1: Create `documents` SQLAlchemy model in `src/agri_data_service/models/document.py`: UUID PK, filename (unique), source_org, title, doc_type (enum: nrcs_cps, sare_bulletin, extension_pub, attra_guide, other), url (nullable), file_hash (SHA-256 for deduplication), ingested_at, chunk_count (updated after embedding), status (enum: pending, chunked, embedded, failed). Add Alembic migration. (TDD: Write test that creates a Document, saves to DB, and verifies unique constraint on filename and hash)

- [ ] Task 1.2: Create `src/agri_data_service/knowledge/loaders.py` with document loader functions. `load_pdf(path) -> str` using PyPDF2 or pdfplumber, `load_markdown(path) -> str`, `load_text(path) -> str`. Factory function `load_document(path) -> tuple[str, dict]` that detects format by extension and returns (text_content, metadata). (TDD: Write tests with sample PDF, MD, and TXT fixtures; verify text extraction and metadata parsing)

- [ ] Task 1.3: Create `src/agri_data_service/knowledge/ingest.py` with `ingest_documents(source_dir, source_org)` function. Scans directory for supported files, computes SHA-256 hash, skips already-ingested documents (by hash), loads text content, creates Document record with status=pending. Returns summary: ingested, skipped, failed counts. (TDD: Write test that ingests a directory with 3 test documents, verifies 3 Document rows created, re-runs and verifies all 3 skipped)

- [ ] Task 1.4: Add CLI command `agri-data-service ingest-docs --source <path> --org <source_org>` to `src/agri_data_service/cli.py`. Command calls `ingest_documents()` and prints summary. (TDD: Write test using Click CliRunner that invokes the command with a test directory and verifies output contains correct counts)

- [ ] Task 1.5: Create `data/knowledge/` directory with 5 initial test documents (public domain): 2 NRCS practice standard summaries (Markdown), 1 SARE cover cropping guide excerpt (text), 1 agroforestry overview (text), 1 silvopasture guide excerpt (text). These serve as fixtures for development and testing. (TDD: Write test that verifies all 5 documents load without errors and have content length > 100 characters)

- [ ] Verification: Run `agri-data-service ingest-docs --source data/knowledge/ --org test`. Verify 5 documents in database with status=pending. Re-run and verify all skipped. [checkpoint marker]

---

## Phase 2: Chunking and Embedding Pipeline

**Goal:** Split ingested documents into semantic chunks, generate vector embeddings, and store in pgvector for similarity search.

### Tasks:

- [ ] Task 2.1: Create `src/agri_data_service/knowledge/chunker.py` with `chunk_document(text, metadata, chunk_size=1000, chunk_overlap=200) -> list[ChunkData]`. Uses LangChain `RecursiveCharacterTextSplitter`. Each chunk gets: content, chunk_index, source_document, title, content_hash (SHA-256 of content for dedup). Detect strategy association from document title or first-paragraph keywords. (TDD: Write test with a 3000-char document, verify produces 4 chunks with correct overlap, indexes 0-3, and metadata preserved)

- [ ] Task 2.2: Create `src/agri_data_service/knowledge/embedder.py` with `EmbeddingService` class. Uses LiteLLM `embedding()` function with configurable model (default: `text-embedding-3-small`). Implements `embed_texts(texts: list[str], batch_size=50) -> list[list[float]]` with batching and rate limiting. Implements `embed_query(query: str) -> list[float]` for single query embedding. (TDD: Write test with mocked LiteLLM that returns dummy 1536-dim vectors, verify batching splits 120 texts into 3 batches, verify single query returns 1536-dim vector)

- [ ] Task 2.3: Create `src/agri_data_service/knowledge/pipeline.py` with `embed_document(document_id) -> int` function. Loads document text from loader, chunks it, generates embeddings, upserts `KnowledgeChunk` rows (skip existing by content_hash), updates Document chunk_count and status=embedded. Returns number of chunks created. (TDD: Write test that embeds a test document with mocked embedder, verifies correct number of KnowledgeChunk rows, re-run verifies chunks skipped)

- [ ] Task 2.4: Create batch embedding function `embed_all_pending()` that queries Documents with status=pending or status=chunked, processes each through `embed_document()`, logs progress. Add CLI command `agri-data-service embed --batch-size 50`. (TDD: Write test that creates 3 pending documents, runs embed_all_pending with mocked embedder, verifies all 3 documents reach status=embedded)

- [ ] Task 2.5: Verify pgvector HNSW index is created on `knowledge_chunks.embedding` with parameters `m=16, ef_construction=64`. Write a query test that inserts 100 chunks with random embeddings, performs a nearest-neighbor query using `<=>` operator, and verifies results are ordered by distance. (TDD: Integration test that inserts chunks, queries nearest neighbors, and verifies ordering and result count)

- [ ] Verification: Run full pipeline: ingest 5 test documents, then embed. Verify KnowledgeChunk rows created with non-null embeddings. Perform a similarity search query and verify results return. [checkpoint marker]

---

## Phase 3: Suitability Scoring Engine

**Goal:** Build the SQL-based strategy suitability scoring engine that evaluates all 10 strategies against a location's environmental context.

### Tasks:

- [ ] Task 3.1: Create `src/agri_data_service/scoring/membership.py` with `trapezoidal_score(value, min_bound, optimal_low, optimal_high, max_bound) -> float` function. Returns 1.0 when value is in [optimal_low, optimal_high], linearly decays to 0.0 at min_bound and max_bound. Returns 0.0 outside [min_bound, max_bound]. Handle edge case where optimal range equals full range (step function). (TDD: Write tests for: value in optimal range=1.0, value at min_bound=0.0, value at midpoint between min and optimal=0.5, value outside range=0.0, edge case of equal bounds)

- [ ] Task 3.2: Create PostgreSQL function `score_trapezoidal(value, min_bound, optimal_low, optimal_high, max_bound)` via Alembic migration. Mirrors the Python implementation for use in SQL queries. (TDD: Write integration test that calls the SQL function with the same test cases as Task 3.1 and verifies identical results)

- [ ] Task 3.3: Create `src/agri_data_service/scoring/engine.py` with `score_strategies(location_id) -> list[StrategyScore]` function. Loads location context (soil, climate, topography, water profiles). For each strategy, computes 6 dimension scores using trapezoidal scoring:
  - precip_fit (weight 0.25): annual_precip vs strategy min/max precip (optimal range = middle 60%)
  - temp_fit (weight 0.20): avg_temp vs strategy min/max temp
  - soil_fit (weight 0.20): binary - 1.0 if texture_class in suitable_soil_types AND drainage in suitable_drainage, else 0.0
  - slope_fit (weight 0.10): trapezoidal with max at 0, optimal to max_slope*0.7, decay to max_slope
  - organic_matter_fit (weight 0.10): trapezoidal with optimal above min_organic_matter
  - water_fit (weight 0.15): based on water_requirement vs available_water_capacity + precip
  Overall score = weighted sum. (TDD: Write tests with a known Seattle-like location context and verify: Cover Cropping scores >0.8, Contour Farming on flat land scores low on slope utility, Hydroponics scores high regardless of soil)

- [ ] Task 3.4: Add confidence level and limiting factor logic to `score_strategies`. Confidence = high if all profile types present, medium if 1 missing, low if 2+ missing. Limiting factors = dimensions scoring below 0.3. Missing profiles use 0.5 neutral score for affected dimensions and flag `low_confidence`. (TDD: Write tests for: all profiles present=high confidence, missing soil=medium confidence with soil_fit=0.5, two missing=low confidence, limiting factor detection)

- [ ] Task 3.5: Add Redis caching to `score_strategies`. Cache key: `scores:{location_id}`, TTL: 3600s. Cache invalidated when any profile for the location is updated (hook into profile upsert). (TDD: Write test that scores a location, verifies cache hit on second call, updates a profile, verifies cache miss on third call)

- [ ] Task 3.6: Create `GET /api/v1/locations/{id}/scores` endpoint in `src/agri_data_service/routers/scoring.py`. Returns list of StrategyScore objects sorted by overall_score descending. 404 if location not found. 422 if location has no profiles. (TDD: Write API tests for: successful scoring, location not found, no profiles, cached response)

- [ ] Verification: Seed a test location with realistic Seattle environmental data. Run scoring. Verify scores are plausible (Cover Cropping, Managed Grazing, Agroforestry should score highest for PNW). Manually review dimension breakdowns. [checkpoint marker]

---

## Phase 4: Species Recommendation Engine

**Goal:** Build the species recommendation engine that filters and scores species for a location and strategy, including companion planting groups.

### Tasks:

- [ ] Task 4.1: Create `src/agri_data_service/recommendations/species_filter.py` with hard filter functions: `filter_by_zone(species_query, zone: int)` (WHERE usda_zones @> zone), `filter_by_precipitation(species_query, precip_mm: float)` (WHERE min_precip <= precip <= max_precip), `filter_by_ph(species_query, ph: float)` (WHERE min_ph <= ph <= max_ph). Each returns a filtered SQLAlchemy query. (TDD: Write tests that create 10 species with varying zones/precip/pH, apply each filter, and verify correct inclusion/exclusion)

- [ ] Task 4.2: Create `src/agri_data_service/recommendations/species_scorer.py` with scoring functions: `score_zone_centrality(species_zones, location_zone) -> float` (1.0 at center of range, 0.5 at edges), `score_precip_match(species_min, species_max, location_precip) -> float`, `score_soil_compatibility(species_ph_range, species_drainage_pref, soil_ph, soil_drainage) -> float`, `score_functional_diversity(species, existing_recommendations) -> float` (bonus for underrepresented functional groups). (TDD: Write tests for each scoring function with known inputs and expected outputs)

- [ ] Task 4.3: Create `src/agri_data_service/recommendations/engine.py` with `recommend_species(location_id, strategy_id=None, limit=20) -> list[SpeciesRecommendation]`. Loads location context, applies hard filters, computes soft scores, ranks by combined score. If strategy_id provided, additionally filters species where guild_roles overlaps with strategy's required guilds (e.g., silvopasture needs ["canopy_tree", "forage_grass", "nitrogen_fixer"]). (TDD: Write test with 30 seeded species, a PNW location, and silvopasture strategy; verify top 20 returned include expected guild distribution)

- [ ] Task 4.4: Add companion planting logic to `recommend_species`. For each recommended species, query `companion_relationships` to find companions that are also in the recommendation set. Return top 3 companions per species. Also detect and warn about antagonist relationships in the recommendation set. (TDD: Write test that seeds companion/antagonist relationships, runs recommendation, and verifies companions attached and antagonist warnings present)

- [ ] Task 4.5: Create `GET /api/v1/locations/{id}/species` endpoint in `src/agri_data_service/routers/recommendations.py`. Query params: `strategy_id` (optional), `limit` (default 20), `guild_role` (optional filter). Returns list of SpeciesRecommendation with companions. (TDD: Write API tests for: basic recommendation, filtered by strategy, filtered by guild, empty results)

- [ ] Verification: Seed 50 species from pilot list. Create a test location with PNW environmental data. Request species recommendations for silvopasture. Verify results include trees, grasses, and nitrogen fixers with companion groupings. [checkpoint marker]

---

## Phase 5: RAG Retrieval and Context Assembly

**Goal:** Build the vector similarity search retrieval pipeline that finds relevant knowledge base passages for a location + strategy query.

### Tasks:

- [ ] Task 5.1: Create `src/agri_data_service/rag/query_builder.py` with `build_retrieval_query(location_context, strategy_names) -> str` function. Constructs a natural language query string from location environmental data: "regenerative agriculture {strategy} in {koppen_zone} climate zone with {soil_type} soil, {annual_precip}mm annual precipitation, USDA zone {zone}, {slope}% slope". (TDD: Write test with sample location context and verify query string includes all key attributes)

- [ ] Task 5.2: Create `src/agri_data_service/rag/retriever.py` with `retrieve_chunks(query_embedding, strategy_ids=None, top_k=10) -> list[RetrievalResult]`. Executes pgvector cosine similarity search on `knowledge_chunks`. If strategy_ids provided, 70% of results filtered to those strategies (WHERE strategy_id IN ...), 30% unrestricted. Returns chunks with relevance_score (1 - cosine_distance). (TDD: Write integration test that inserts 50 chunks with known embeddings, retrieves top 10, verifies ordering by similarity and strategy filtering ratio)

- [ ] Task 5.3: Implement MMR (Maximal Marginal Relevance) re-ranking in `src/agri_data_service/rag/reranker.py`. Function `mmr_rerank(query_embedding, chunks, lambda_param=0.7, top_k=10) -> list[RetrievalResult]`. Iteratively selects chunks that are similar to query but diverse from already-selected chunks. (TDD: Write test with 20 chunks where some are near-duplicates, verify MMR selects diverse set compared to pure similarity ranking)

- [ ] Task 5.4: Create `src/agri_data_service/rag/context_assembler.py` with `assemble_context(retrieval_results, max_tokens=4000) -> AssembledContext`. Concatenates chunk contents with source attribution markers (`[Source: {title}, {source_org}]`). Truncates to max_tokens using tiktoken token counting. Returns assembled text and list of source citations. (TDD: Write test that assembles context from 10 chunks, verifies token count under budget, verifies all sources cited)

- [ ] Task 5.5: Create `src/agri_data_service/rag/pipeline.py` with `retrieve_context(location_id, strategy_ids, top_k=10) -> RetrievalContext`. Orchestrates: load location context -> build query -> embed query -> retrieve chunks -> MMR rerank -> assemble context. Returns assembled context with sources. (TDD: Write end-to-end test with mocked embedder, seeded knowledge chunks, and verify complete pipeline returns assembled context with citations)

- [ ] Verification: Embed test documents from Phase 1. Run retrieval pipeline for a test location + "cover cropping" strategy. Verify returned passages are relevant to cover cropping. Inspect source citations. [checkpoint marker]

---

## Phase 6: LLM Synthesis Endpoint

**Goal:** Build the `/locations/{id}/recommend` endpoint that orchestrates scoring, species recommendation, RAG retrieval, and LLM synthesis into a comprehensive recommendation.

### Tasks:

- [ ] Task 6.1: Create `prompts/recommendation_v1.md` with the system prompt and user prompt template for LLM synthesis. System prompt establishes the AI as a regenerative agriculture advisor. User prompt template includes slots for: location summary, strategy scores with dimension breakdowns, species recommendations, knowledge base context. Instruct JSON structured output with fields: narrative, citations, key_recommendations, risks, next_steps. (TDD: Write test that renders the template with sample data and verifies all slots filled, output instruction present, and token count < 2000)

- [ ] Task 6.2: Create `src/agri_data_service/rag/synthesizer.py` with `LLMSynthesizer` class. Uses LiteLLM `completion()` with configurable model via `LITELLM_MODEL` env var (default: `claude-3-5-haiku-20241022`). Implements `synthesize(prompt, max_tokens=2000) -> SynthesisResult`. Parses JSON from LLM response. Handles: malformed JSON (retry with stricter prompt), API errors (raise with context), token budget tracking. (TDD: Write test with mocked LiteLLM that returns a valid JSON response, verify parsing; test with malformed response, verify retry; test with API error, verify exception)

- [ ] Task 6.3: Create `src/agri_data_service/recommendations/orchestrator.py` with `generate_recommendation(location_id, strategy_ids=None, include_species=True, include_narrative=True) -> Recommendation`. Orchestrates:
  1. `score_strategies(location_id)` -> strategy scores
  2. If include_species: `recommend_species(location_id, top_strategy_id)` -> species list
  3. If include_narrative: `retrieve_context(location_id, top_strategy_ids)` -> RAG context
  4. If include_narrative: `synthesizer.synthesize(assembled_prompt)` -> narrative
  5. Assemble full Recommendation response
  (TDD: Write test with mocked scoring, species, RAG, and LLM; verify orchestration order, verify partial results when include_narrative=false)

- [ ] Task 6.4: Create `POST /api/v1/locations/{id}/recommend` endpoint in `src/agri_data_service/routers/recommendations.py`. Request body: `{"strategy_ids": [...], "include_species": true, "include_narrative": true}`. Response: full Recommendation with strategies, species, narrative, citations. Add Redis caching (6h TTL, key includes all request params). Rate limit: 10/min per IP. (TDD: Write API tests for: full recommendation, without narrative, with specific strategies, cached response, rate limit hit)

- [ ] Task 6.5: Add fallback behavior: if LLM call fails (timeout, API error, budget exceeded), return the recommendation without narrative but with strategy scores and species. Log the failure. Include `"narrative_available": false, "narrative_error": "LLM service unavailable"` in response. (TDD: Write test where LLM mock raises timeout, verify response still contains scores and species with narrative_available=false)

- [ ] Task 6.6: Add token usage tracking. Log per-request: model, prompt_tokens, completion_tokens, total_tokens, estimated_cost. Create utility `estimate_cost(model, prompt_tokens, completion_tokens) -> float` with per-model pricing table. (TDD: Write tests for cost estimation with known token counts for Claude Haiku, Sonnet, and GPT-4o)

- [ ] Verification: Run full recommendation endpoint against seeded database with test location, 10 strategies, 50 species, and embedded knowledge base. Verify response includes scores, species, and narrative with citations. Test with narrative disabled. Test LLM fallback. [checkpoint marker]

---

## Phase 7: PlantGeo Integration Bridge

**Goal:** Create tRPC procedures in PlantGeo that proxy to the agri-data-service API, making recommendation data available to the Next.js frontend.

### Tasks:

- [ ] Task 7.1: Create `src/lib/server/services/agri-data.ts` in PlantGeo with `AgriDataClient` class. Uses `fetch` with base URL from `AGRI_DATA_SERVICE_URL` env var. Implements: `getLocationContext(locationId)`, `getStrategies()`, `getRecommendation(locationId, options)`. Each method has proper error handling (timeout 15s, retry once on 5xx). (TDD: Write test with msw or nock mocking the agri-data-service API; verify each method returns typed responses, handles 404, handles timeout with retry)

- [ ] Task 7.2: Create Zod schemas in `src/lib/server/schemas/agri-data.ts` matching agri-data-service response types: `StrategyScoreSchema`, `SpeciesRecommendationSchema`, `RecommendationResponseSchema`, `LocationContextSchema`. Validate all responses from AgriDataClient through these schemas. (TDD: Write test that validates sample response JSON against each schema, test that malformed responses throw ZodError)

- [ ] Task 7.3: Create tRPC router `src/lib/server/routers/agri-data.ts` with procedures: `getLocationContext` (input: locationId string), `getStrategies` (no input), `getRecommendation` (input: locationId, optional strategy_ids array, include_species boolean, include_narrative boolean). Each procedure calls AgriDataClient and returns validated response. Add Redis caching (30min TTL) on `getStrategies` and `getLocationContext`. (TDD: Write tRPC procedure tests with mocked AgriDataClient; verify input validation, caching behavior, error propagation)

- [ ] Task 7.4: Register `agriData` router in PlantGeo's root tRPC router. Verify TypeScript types are inferred correctly on the client side. Create a simple test page or component that calls each procedure and displays results (dev-only, not production UI). (TDD: Write integration test that calls tRPC procedures through the full stack (httpx -> tRPC -> AgriDataClient -> mocked agri-data-service) and verifies end-to-end type safety)

- [ ] Verification: Start both PlantGeo dev server and agri-data-service (or mocked). Call tRPC procedures from browser console or test page. Verify data flows end-to-end. Verify caching works. Verify error handling on service unavailability. [checkpoint marker]

---

## Dependencies Between Phases

```
Phase 1 (Doc Ingestion)
  └─> Phase 2 (Chunking + Embedding)
        └─> Phase 5 (RAG Retrieval)
              └─> Phase 6 (LLM Synthesis)
                    └─> Phase 7 (PlantGeo Integration)

Phase 3 (Suitability Scoring) ── independent, can parallel with Phase 1-2
  └─> Phase 6 (LLM Synthesis)

Phase 4 (Species Recommendation) ── requires Phase 3 for strategy context
  └─> Phase 6 (LLM Synthesis)
```

Phases 1-2 (knowledge base) and Phase 3 (scoring) can be worked in parallel. Phase 4 can start after Phase 3. Phase 5 requires Phase 2. Phase 6 requires Phases 3, 4, and 5. Phase 7 requires Phase 6.
