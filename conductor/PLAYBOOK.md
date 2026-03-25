# PlantGeo — OMC Execution Playbook

## Track Dependency Graph

```
Track 1: Map Layer Data Viz ──────────────────── (independent, in progress)
Track 2: Agri Data Service Scaffold ──────┐
                                          ├──→ Track 4: RAG Recommendation Engine
Track 3: Data Ingestion Pipeline ─────────┘
```

## Execution Strategy

### Phase A: Parallel Foundation (Tracks 1 + 2)

These two tracks are independent — run them simultaneously.

**Track 1** finishes the GIS map layers (already ~70% done).
**Track 2** scaffolds the new Python data service repo.

```bash
# Terminal 1 — Finish map layer viz
/conductor:implement map_layer_data_viz_20260324

# Terminal 2 — Scaffold data service (separate repo)
# Create the new repo first, then:
/conductor:implement agri_data_service_scaffold_20260324
```

**OMC skills for Phase A:**
| Task | Skill | Agent Tier |
|------|-------|------------|
| Fix remaining map layers | `/autopilot` | executor (sonnet) |
| Scaffold FastAPI project | `/ultrapilot` | executor-high (opus) |
| Define SQLAlchemy models | `/autopilot` | executor (sonnet) |
| Create Alembic migrations | `/autopilot` | executor-low (haiku) |
| Seed strategy data | `/ecomode` | executor-low (haiku) |
| Build API endpoints | `/ultrapilot` | 3 parallel executors |
| Code review each phase | `/code-review` | code-reviewer (opus) |

### Phase B: Data Pipeline (Track 3)

Starts after Track 2 Phase 3 (schema + migrations done).

```bash
/conductor:implement data_ingestion_pipeline_20260324
```

**OMC skills for Phase B:**
| Task | Skill | Agent Tier |
|------|-------|------------|
| Research API shapes | `/research` | scientist (sonnet) |
| Build Celery workers | `/ultrapilot` | 5 parallel executors (one per data source) |
| Test with real APIs | `/ultraqa` | qa-tester (sonnet) |
| Data quality layer | `/autopilot` | executor (sonnet) |
| Verify data integrity | `/tdd` | tdd-guide (sonnet) |

### Phase C: RAG + AI (Track 4)

Starts after Track 2 complete + Track 3 Phase 1 done.

```bash
/conductor:implement rag_recommendation_engine_20260324
```

**OMC skills for Phase C:**
| Task | Skill | Agent Tier |
|------|-------|------------|
| Knowledge base design | `/plan` | planner (opus) |
| Document ingestion pipeline | `/autopilot` | executor (sonnet) |
| Embedding + chunking | `/autopilot` | executor (sonnet) |
| Suitability scoring engine | `/ralph` | architect verify loop |
| Species recommendation | `/autopilot` | executor (sonnet) |
| RAG retrieval pipeline | `/ralph` | architect verify loop |
| LLM synthesis endpoint | `/autopilot` | executor-high (opus) |
| PlantGeo integration | `/ultrapilot` | parallel executors |
| Security review | `/security-review` | security-reviewer (sonnet) |
| Final review | `/code-review` | code-reviewer (opus) |

## Quick Reference — OMC Commands

```bash
# Planning
/plan              # Strategic planning with interview
/ralplan           # Iterative plan with Planner + Architect + Critic

# Execution
/autopilot         # Full autonomous: idea → code (single track)
/ultrapilot        # Parallel autopilot with file partitioning (5x faster)
/ralph             # Self-referential loop until done with architect verify
/ecomode           # Token-efficient parallel (haiku + sonnet agents)
/ultrawork         # Maximum performance parallel orchestration

# Quality
/code-review       # Comprehensive code review (opus)
/security-review   # Security vulnerability scan
/tdd               # Test-driven development enforcement
/ultraqa           # QA cycling: test → verify → fix → repeat

# Research
/research          # Parallel scientist agents for comprehensive research
/deepsearch        # Thorough codebase search
/analyze           # Deep analysis and investigation

# Utilities
/note              # Save notes for compaction resilience
/cancel            # Cancel active OMC mode
```

## Repo Setup Commands

```bash
# PlantGeo (this repo)
./dev.sh           # Start local dev (PostGIS + Redis + Next.js)
.\dev.ps1          # PowerShell equivalent

# Agri Data Service (new repo — to be created)
# 1. Create repo
mkdir agri-data-service && cd agri-data-service
git init

# 2. Run conductor setup
/conductor:setup

# 3. Start scaffolding
/conductor:implement agri_data_service_scaffold_20260324
```

## Estimated Timeline

| Phase | Tracks | Est. Hours | Parallel? |
|-------|--------|-----------|-----------|
| A | Map Layers + Data Service Scaffold | 20-28h | Yes (2 terminals) |
| B | Data Ingestion Pipeline | 28-36h | After A.Track2.Phase3 |
| C | RAG + AI Engine | 32-40h | After A.Track2 + B.Phase1 |
| **Total** | | **~60-80h** | With parallelism |

With aggressive OMC parallelism (`/ultrapilot`, `/ecomode`), real elapsed time is ~40-50% of sequential estimate.
