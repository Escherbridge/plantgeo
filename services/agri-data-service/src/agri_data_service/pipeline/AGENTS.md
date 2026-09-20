# Layer L2: Pipeline

## Responsibility
Upstream acquisition, external API fetching, raw tile/data backfill routines (`ingest/` and `historical_*`).

## Dependency Rules
- **May import**: `foundation` (L0), `warehouse` (L1).
- **May NOT import**: `method` (L1), `planes` (L3), `interface` (L4).

## Shared governed source census

`constants.DIRECT_HOURLY_REFRESH_INTERVAL_SECONDS` is the shared configured cadence for the
climate and ERA5-Land soil writers and their slider timing metadata. It states schedule policy,
not a successful run or guaranteed publication. Both executor declarations consume it explicitly.

`vegetation_source.py` owns the bounded PostgreSQL cell-day census shared by vegetation writers,
operators, and validators. Validation modules may re-export that contract for compatibility, but
sibling validation modules import the lower pipeline module so pytest's layer contract remains
acyclic.

## Availability follows publication, never leads it

`parquet/availability_extension.py` is the only path from a terminal lane-day into that lane's
availability generation, and it runs strictly AFTER the day's completion or governed-absence marker.
See `parquet/AGENTS.md`, "`availability_extension.py` — the terminal day joins the index", for the
write ledger it reads its receipts from, the five typed outcomes, the retry claim, and why a lane
without a bootstrap still completes its days.

`db/vegetation_publication.py` owns the vegetation-wide advisory barrier and durable per-day queue
operations. The 45-day ingestion lookback spans up to 46 inclusive UTC dates; publication rechecks
that boundary every tick and drains durable pending work independently of whether ingestion emitted
a callback in that tick.

## Retained MTBS reconciliation reader

`lanes/burn_severity.py` only reads release-scoped PostgreSQL rows and conforms them to the
registered schema for `validation/burn_severity.py`. Empty results describe that database query;
they do not prove upstream absence. Its unregistered PostgreSQL-to-Parquet exporter was removed
on 2026-09-10. Publication belongs to `direct/burn_severity/adapter.py`, whose source evidence,
all-rung finalization and bounded parts remain unchanged. The SQL and reconciliation reader stay
until their own retirement proof is discharged.

## Source bindings

`source_bindings.py` is the one table mapping a region manifest's `source_slug` to the coverage
claim the source implementation declares, and it is the only thing that knows all three of
`burn_severity/mtbs.py`, `drought/usdm.py` and `soil_survey/ssurgo.py` at once.

It sits at `pipeline/` root rather than in `pipeline/direct/` because every module directly inside
`pipeline/direct/` IS a lane under `layer-lanes.md` §1, and a registry that imports three lanes
would be a cross-lane import three times over
(`tests/test_layer_import_contract.py::test_lanes_do_not_import_each_other`). One level up, it is
ordinary pipeline-layer wiring.

**The registry is keyed by LAYER, not by source slug alone** (`SourceRegistry`: one
`Mapping[str, <LayerProtocol>]` per layer). A flat `{slug: object}` cannot say which layer an
implementation serves, so the two resolvers could only assert their return type — three coded
`type: ignore`s, none of them a missing-stub case — and the boot check could not test conformance
at all. With the per-layer maps, `resolve_drought_source` returns `DroughtSource` because that is
the map it reads, and `declared_layer_source_contracts()` can hand `bindings.py` the protocol each
layer expects so `drought -> ssurgo` fails `create_app()` instead of a scheduled turn
(STYLE-REVIEW-W5 B2).

**The layer key survives all the way to the boot check.** `sources_by_layer()` hands
`bindings.py` `{layer_slug: {source_slug: instance}}` un-flattened, because registration under the
bound layer is the servability question itself — the earlier flattened `source_instances()` left
the check nothing to test but an `isinstance`, which compares member names only and therefore
accepted `drought -> mtbs` (STYLE-REVIEW-W6 B1). `coverage_claims()` stays flat because coverage
is a property of the source rather than of the layer it serves, and `SourceRegistry.__post_init__`
refuses a registry whose slugs collide across layers (`DuplicateSourceSlugError`), so that flatten
can no longer drop an entry last-wins (STYLE-REVIEW-W6 S6).

`SOIL_SURVEY_LAYER_SLUG` is boot-checked with no `resolve_soil_survey_source` to match: the
soil-survey lane still reaches `ssurgo.py` by name, so its binding is validated at boot and
consumed nowhere. That asymmetry with drought and burn severity is a stated state — the check
landed ahead of the wiring — not an oversight (BACKLOG N38).

Its imports are inside the function, not at module scope: each source module pulls its layer's
ingest transport and lane registry, and the sole caller is `app.py`'s boot check. Paying for all of
`pipeline/direct/` merely to name the registry would put a heavy third edge into the application's
import graph for no benefit.
