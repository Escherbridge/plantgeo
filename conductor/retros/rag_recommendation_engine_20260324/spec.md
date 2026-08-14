# Specification: RAG Recommendation Engine

## Overview

Build the AI-driven recommendation engine that scores regenerative agriculture strategies for a given location based on its environmental context, recommends suitable species with companion planting guilds, and uses Retrieval-Augmented Generation (RAG) to synthesize actionable guidance from authoritative knowledge base documents.

## Background

With the data warehouse populated (Track 1) and ingestion pipeline operational (Track 2), the platform has rich environmental profiles for any location. This track builds the intelligence layer: a suitability scoring engine that matches strategies to environmental conditions, a species recommendation engine that filters by zone/soil/strategy guild, and a RAG pipeline that retrieves relevant passages from NRCS practice standards, SARE guides, and extension publications to generate contextualized recommendations via LLM synthesis.

The final output is a `/locations/{id}/recommend` endpoint that returns scored strategies, recommended species, and an AI-generated narrative explaining why each strategy fits (or doesn't) with specific citations from the knowledge base.

## Functional Requirements

### FR-1: Knowledge Base Document Ingestion
**Description:** Ingest authoritative regenerative agriculture documents into the knowledge base for RAG retrieval.
**Acceptance Criteria:**
- CLI command `agri-data-service ingest-docs --source <path>` processes documents from a directory
- Supported formats: PDF, Markdown, plain text
- Document sources: NRCS Conservation Practice Standards (CPS), SARE research bulletins, university extension publications, ATTRA/NCAT guides
- Each document is tracked in a `documents` metadata table: id, filename, source_org, title, doc_type, url, ingested_at, chunk_count
- Initial corpus: minimum 20 documents covering all 10 strategies
- Documents are processed through the chunking + embedding pipeline (FR-2)
**Priority:** P0

### FR-2: Chunking and Embedding Pipeline
**Description:** Split documents into semantic chunks and generate vector embeddings stored in pgvector.
**Acceptance Criteria:**
- LangChain `RecursiveCharacterTextSplitter` with chunk_size=1000, chunk_overlap=200
- Metadata preserved per chunk: source_document, title, chunk_index, strategy association (if detectable from document title/content)
- Embedding model: configurable via LiteLLM (default: OpenAI text-embedding-3-small, 1536 dimensions)
- Embeddings stored in `knowledge_chunks.embedding` column (pgvector)
- HNSW index on embedding column for fast approximate nearest neighbor search
- Batch embedding with rate limiting (max 100 chunks per API call)
- CLI command: `agri-data-service embed --batch-size 50`
- Pipeline is idempotent: re-running skips already-embedded chunks (based on content hash)
**Priority:** P0

### FR-3: Suitability Scoring Engine
**Description:** Score each strategy's suitability for a location based on environmental context data using SQL-based rule evaluation.
**Acceptance Criteria:**
- Function `score_strategies(location_id) -> list[StrategyScore]` returns all 10 strategies with scores 0.0-1.0
- Scoring dimensions (each 0.0-1.0, weighted):
  - Precipitation fit (0.25): how well annual_precip_mm falls within strategy's min/max range
  - Temperature fit (0.20): how well avg_temp_c falls within strategy's min/max range
  - Soil fit (0.20): whether soil texture_class is in strategy's suitable_soil_types AND drainage_class in suitable_drainage
  - Slope fit (0.10): whether slope_pct is below strategy's max_slope_pct
  - Organic matter fit (0.10): whether organic_matter_pct meets strategy's minimum
  - Water availability fit (0.15): based on strategy's water_requirement vs. available water capacity and precipitation
- Range scoring uses trapezoidal membership function: 1.0 within optimal range, linear decay to 0.0 at +-50% beyond range boundaries
- Missing data for any dimension: use 0.5 (neutral) and flag as `low_confidence`
- Results include: strategy_id, strategy_name, overall_score, dimension_scores dict, confidence_level (high/medium/low), limiting_factors (list of dimensions scoring below 0.3)
- Results cached in Redis for 1 hour per location
**Priority:** P0

### FR-4: Species Recommendation Engine
**Description:** Recommend species suitable for a location filtered by environmental constraints and strategy guild roles.
**Acceptance Criteria:**
- Function `recommend_species(location_id, strategy_id=None, limit=20) -> list[SpeciesRecommendation]`
- Hard filters (exclude species that fail):
  - USDA hardiness zone must contain the location's zone
  - Precipitation tolerance must overlap with location's annual precipitation
  - pH tolerance must overlap with location's soil pH
- Soft scoring (0.0-1.0):
  - Zone centrality: how central the location's zone is within the species' range
  - Precipitation match: how well precip aligns with species optimal range
  - Soil compatibility: pH match, drainage preference
  - Functional diversity bonus: nitrogen fixers, dynamic accumulators, pollinator plants scored higher when underrepresented in current recommendations
- If `strategy_id` provided: additionally filter by species with matching guild_roles for that strategy (e.g., silvopasture wants trees + forage grasses + nitrogen fixers)
- Results include: species_id, scientific_name, common_name, suitability_score, guild_role, companion_species (list of companion species from companion_relationships)
- Companion planting groups: for each recommended species, include top 3 companions that are also in the recommendation set
**Priority:** P1

### FR-5: RAG Retrieval and Context Assembly
**Description:** Retrieve relevant knowledge base passages for a location + strategy combination using vector similarity search.
**Acceptance Criteria:**
- Function `retrieve_context(location_id, strategy_ids, top_k=10) -> RetrievalContext`
- Generates a query embedding from a constructed query string: "regenerative agriculture {strategy_name} in {koppen_zone} climate with {soil_type} soil and {annual_precip}mm annual precipitation"
- Retrieves top_k chunks from `knowledge_chunks` using pgvector cosine similarity (`<=>` operator)
- Filters retrieval to chunks associated with the requested strategies (when strategy FK is set), with a portion (30%) unrestricted for cross-strategy insights
- Re-ranks results using a simple cross-encoder score or MMR (Maximal Marginal Relevance) to reduce redundancy
- Returns: list of chunks with source attribution, relevance scores, and assembled context string
- Context window budget: max 4000 tokens of retrieved context to leave room for prompt + response
**Priority:** P1

### FR-6: LLM Synthesis Endpoint
**Description:** Generate AI-powered narrative recommendations combining suitability scores, species recommendations, and RAG context.
**Acceptance Criteria:**
- Endpoint: `POST /api/v1/locations/{id}/recommend` with optional body `{"strategy_ids": [...], "include_species": true, "include_narrative": true}`
- Orchestrates: suitability scoring -> species recommendation -> RAG retrieval -> LLM synthesis
- LLM call via LiteLLM (provider-agnostic: supports Claude, GPT-4o, Llama)
- Prompt template includes: location environmental summary, top strategy scores with limiting factors, recommended species with guild roles, retrieved knowledge base passages with citations
- LLM response is structured (JSON mode or function calling): `{"narrative": "...", "citations": [...], "key_recommendations": [...], "risks": [...], "next_steps": [...]}`
- Citations reference source documents by title and chunk index
- Response time target: < 10 seconds (including LLM call)
- Caching: full recommendation cached in Redis for 6 hours per location
- Fallback: if LLM call fails, return structured data (scores + species) without narrative
**Priority:** P1

### FR-7: PlantGeo Integration Bridge
**Description:** Create a tRPC procedure in PlantGeo that proxies to the agri-data-service recommendation API.
**Acceptance Criteria:**
- tRPC router `agriData` in PlantGeo's existing tRPC setup
- Procedures: `getLocationContext`, `getStrategies`, `getRecommendation`
- `getRecommendation` calls `POST /api/v1/locations/{id}/recommend` on agri-data-service
- Response types defined as Zod schemas matching the agri-data-service OpenAPI spec
- Server-side caching in Redis (PlantGeo's Redis instance) for 30 minutes
- Error handling: timeout after 15 seconds, retry once, return partial data on failure
- Environment variable `AGRI_DATA_SERVICE_URL` configures the base URL
**Priority:** P2

## Non-Functional Requirements

### NFR-1: Performance
- Suitability scoring: < 200ms for all 10 strategies (SQL-based, no LLM)
- Species recommendation: < 500ms for 20 species
- RAG retrieval: < 1 second for top-10 chunks
- Full recommendation with LLM: < 10 seconds
- All non-LLM results cached in Redis

### NFR-2: Quality
- Suitability scores validated against expert assessments for 5 test locations with known strategies
- RAG retrieval relevance validated: top-5 chunks should include at least 3 relevant passages for a given strategy query
- LLM output validated for: no hallucinated citations, structured format compliance, actionable recommendations

### NFR-3: Cost Control
- LLM calls only when `include_narrative=true` (default: false)
- Embedding model uses text-embedding-3-small (cheaper than ada-002)
- LLM uses configurable model (default: Claude 3.5 Haiku for cost efficiency, Claude 3.5 Sonnet for quality)
- Token usage logged per request
- Monthly cost estimation endpoint or dashboard

### NFR-4: Security
- LLM API keys stored as environment variables, never logged
- User input sanitized before inclusion in LLM prompts (prevent prompt injection)
- Rate limiting on recommendation endpoint (10 requests/minute per IP)
- Knowledge base content is from vetted sources only (no user-uploaded content in V1)

## User Stories

### US-1: Location Strategy Assessment
**As** a land manager viewing a location on PlantGeo, **I want** to see which regenerative strategies are best suited for this site, **so that** I can make informed decisions about land management.
**Given** I am viewing a location with populated environmental profiles
**When** I request strategy recommendations
**Then** I see all 10 strategies scored 0-100% with clear explanations of why each scores as it does (e.g., "Cover Cropping scores 92% - excellent precipitation fit, suitable soil drainage, gentle slope")

### US-2: Species Discovery
**As** a permaculture designer, **I want** to see which species thrive at a location for a given strategy, **so that** I can design a planting plan with compatible species guilds.
**Given** I have selected "Permaculture Food Forest" as my strategy at a location
**When** I request species recommendations
**Then** I see 20 species organized by guild role (canopy trees, understory, ground cover, nitrogen fixers, accumulators) with companion planting suggestions

### US-3: AI-Guided Strategy Narrative
**As** a beginning farmer, **I want** AI-generated guidance about implementing a strategy, **so that** I can understand the practical steps backed by authoritative sources.
**Given** I request a full recommendation with narrative enabled
**When** the AI synthesizes the recommendation
**Then** I receive a narrative that explains the strategy's fit, cites specific NRCS practice standards and SARE guides, lists risks to watch for, and provides concrete next steps

### US-4: PlantGeo Map Integration
**As** a PlantGeo user, **I want** to click a location on the map and see strategy suitability, **so that** I can explore regenerative potential across a landscape.
**Given** I click a point on the PlantGeo map
**When** the location panel loads
**Then** I see strategy suitability scores fetched via tRPC from the agri-data-service, with an option to "Generate Full Recommendation" that triggers the LLM narrative

## Technical Considerations

- Suitability scoring should be implemented as a SQL function or query for performance, not in Python application code
- The trapezoidal membership function for range scoring can be expressed as a PostgreSQL function: `GREATEST(0, LEAST(1, (value - lower_bound) / (optimal_lower - lower_bound), (upper_bound - value) / (upper_bound - optimal_upper)))`
- LangChain is used for document loading, text splitting, and embedding generation, but NOT for the full chain/agent framework (too much abstraction)
- LiteLLM provides a unified interface for multiple LLM providers; configure via `LITELLM_MODEL` env var
- The recommendation prompt should be version-controlled in a `prompts/` directory, not hardcoded
- pgvector HNSW index parameters: `m=16, ef_construction=64` for balanced recall/speed
- For the PlantGeo integration, use `fetch` (not axios) on the server side to call agri-data-service

## Out of Scope

- User accounts and saved recommendations (future track)
- Fine-tuning or custom model training
- Real-time weather integration (current conditions vs. normals)
- Economic analysis or cost-benefit calculations
- Multi-year implementation planning / timeline generation
- User-uploaded knowledge base documents
- Conversational follow-up (single-turn recommendation only in V1)

## Open Questions

1. Should suitability scoring be a pure SQL function or a hybrid SQL + Python approach? (Leaning SQL for performance, Python for complex logic like trapezoidal scoring)
2. What LLM should be the default for synthesis? (Claude 3.5 Haiku for cost, with option to upgrade to Sonnet)
3. How should we handle locations with no environmental data yet? (Return error with message to wait for ingestion, or trigger ingestion and return partial results)
4. Should the 20-document knowledge base be committed to the repo or stored externally? (Committed as test fixtures, with a separate download script for the full corpus)
5. For PlantGeo integration, should the tRPC bridge be synchronous (blocking) or use SSE for streaming the LLM response? (Start synchronous, add streaming in a follow-up)
