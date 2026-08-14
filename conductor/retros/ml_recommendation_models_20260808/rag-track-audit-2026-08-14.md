---
type: audit-record
---

# What `rag_recommendation_engine_20260324` actually built

Verified 2026-08-14 by read-only queries against the production warehouse
(`DATABASE_URL_SYNC`, database `plantgeo`, PostgreSQL 18.4, Alembic head
`20260808_0019`). Nothing was written. Every claim below is a query result, not a
reading of that track's specification.

## Verified facts

| Question | Answer |
| --- | --- |
| Does a `documents` table exist in `agri`? | **No.** No relation whose name contains `document` exists in the schema. |
| Does `agri.knowledge_chunks` exist? | **Yes**, as a table, with **0 rows**. |
| Does `agri.species` exist? | **Yes**, 0 rows. |
| Does `agri.companion_relationships` exist? | **Yes**, 0 rows. |
| Is pgvector active? | **Yes** — extension `vector` **0.8.5** installed. |
| Is there a vector column? | **Yes** — `agri.knowledge_chunks.embedding` is `vector(1536)`. |
| Row counts of the neighbouring profile tables | `strategies`, `locations`, `climate_profiles`, `soil_profiles`, `water_profiles`, `topography_profiles`, `land_use_snapshots`: **all 0**. |

`agri.knowledge_chunks` columns as built: `id uuid`, `source_document
varchar(500)`, `title varchar(500)`, `content text`, `chunk_index integer`,
`embedding vector(1536)`, `strategy_id uuid`, `metadata_json jsonb`,
`created_at timestamptz`.

## What that means for this track

1. **The RAG plane is schema-only.** The retrieval tables exist and pgvector is
   installed, but no chunk, species or companion row has ever been written. There
   is nothing to retrieve, so nothing in this track could have been built on top
   of it even if the design had wanted to.
2. **`source_document` is a `varchar`, not a foreign key.** Without a `documents`
   table there is no work/edition/DOI identity to hang a citation on. That is the
   concrete gap the expert label plane closes for this track: it authors its own
   `agri.expert_label_source` registry (DOI, title, year, journal, licence
   posture, checksum) rather than reusing a free-text column.
3. **The embedding column is unused, and this track does not use it either.**
   Serving-side neighbour search over pgvector remains available for a later lane;
   the models built here are JSON-extractable logistic classifiers whose scoring
   needs no index.
4. **No overlap, no collision.** This track writes to `agri.expert_label*` and
   `agri.recommendation_training_receipt` only. It reads neither
   `knowledge_chunks` nor `species`, and it neither drops nor alters them.

## Method

The audit script connected with `psycopg2` in a `READ ONLY` session
(`set_session(readonly=True)`) and ran catalogue queries plus `count(*)` on the
named tables. It was run once, on 2026-08-14, against the production DSN. Prod
remains at `20260808_0019`: revisions `20260814_0020` and later had not been
applied there at audit time.
