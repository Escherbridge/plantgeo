---
type: reference
---

# Reviewed historical plan artifacts

Checksum-governed input plans for the local historical replays. Every file here is either a
plan that some `agri-cli historical-*` command consumes, or the generator that authors one.

## Why the generators exist

`HistoricalEra5LandBackfillPlan` carries a field named `nasa_lattice_plan_checksum`. Nothing in
the codebase recomputes or cross-checks it — it is declared once at
`execution/historical_era5.py:143` as a bare `^[0-9a-f]{64}$` pattern, and the repository's own
unit test fills it with `"a" * 64`. It is therefore load-bearing provenance that the type system
cannot defend: a hand-typed value would look valid forever while pointing at nothing, and it is
folded into `historical_era5_plan_checksum()`, so a wrong value silently poisons the release
chain downstream.

`author_pnw_soil_moisture_plans.py` exists so that value is always *derived* from a real NASA
plan object rather than typed. It writes the NASA lattice plan and the ERA5 plan in one pass and
sets the ERA5 field from `historical_nasa_plan_checksum()` of the plan it just wrote.
`tests/test_pnw_soil_moisture_plans.py` re-derives the binding from the artifacts on disk, so a
hand-edit of either file fails the suite.

## The real prerequisite is spatial cells, not the checksum

The checksum is unenforced metadata. The *enforced* prerequisite lives in the writer:
`_require_era5_spatial_cells` (`execution/historical_writer.py`) raises "ERA5 persistence
requires the complete matching NASA sampling lattice in the warehouse" unless an
`agri.spatial_cell` row already exists for every `cell_key` in the ERA5 plan. Those rows are
created only by the NASA POWER persist path (`_ensure_spatial_cell`). So a NASA lattice plan
must genuinely be authored *and run* before ERA5 can persist — running ERA5 first fails at the
warehouse, not at validation.

Consequence for authoring: ERA5 cell keys must be reused verbatim from the NASA plan, and the
NASA plan must place its cells on whole-degree centroids, because
`require_governed_monthly_coverage` rejects any ERA5 cell that is not aligned to the reviewed
one-degree output grid.

## Cell geometry is deliberately borrowed

The PNW lattice is a strict subset of the already-reviewed North America lattice at
`infra/local-warehouse/plans/nasa-power-na-sampling-20220430-20260430-asof-20260721.json`,
copied cell-for-cell with its `grid_name`, `grid_resolution_m` and `cell_half_span_degrees`
intact. `_ensure_spatial_cell` compares existing rows with `ST_Equals` and refuses a key that is
"already governed by different geometry", so borrowing keeps a later full-coverage run
idempotent instead of making it collide.

## As-of times are frozen once a plan has run

`NASA_ACQUISITION_RELEASE_SET_AS_OF` must not be edited: that plan has been run and its checksum
is quoted by the ERA5 plan, so changing it orphans the binding and would re-persist the source
content under a new release. When receipts land after the acquisition as-of time,
`finalize_nasa_release_set` correctly declines to publish and the fix is the finalization
artifact plus `historical-nasa-finalize`, which advances only the release identity. The
generator hard-fails rather than silently rewriting a plan that is already on disk.

## Running the commands

Two environment facts bite every time:

- `_require_cds_credentials()` reads `os.environ` directly, while `Settings` loads `.env`
  through pydantic-settings, which does *not* populate `os.environ`. Listing `CDSAPI_URL` and
  `CDSAPI_KEY` in `.env` is not enough; they must be exported into the process.
- `require_local_source_loader_database_url()` rejects a loader DSN equal to `DATABASE_URL`.
  Because `.env` already points `DATABASE_URL` at the production proxy, `DATABASE_URL` has to be
  blanked in the process environment when passing the same target as the loader DSN.
