---
type: track-evidence
track: plantgeo_ml_service_20260918
slice: p2d-fire-risk-registration
branch: ml/p2d-agri-registration
base: origin/main bc08eca9
---

# Phase 2D predictions — agri-side registration of `fire-risk` and `weather-forecast`

Written before any sweep. The author ran NOTHING but smoke imports (owner rule 2026-08-25: authors
predict what will fail, a separate monitor judges). The one command run was
`uv run --no-sync python -c "..."` inside `services/agri-data-service`, which imported the registry,
resolved both new stream schemas at both kinds, ran `validate_derivation_against_schema` for both
lanes, imported the export module and the `ops` CLI group, and compared both region manifests
against `PLATFORM_LAYER_SLUGS`.

**Smoke result:** 34 registered lanes (was 32); `get_stream_schema("fire-risk", "forecast")` returns
the 14-column forecast-shaped contract; `get_stream_schema("weather-forecast")` returns the 19 frozen
columns; `validate_derivation_against_schema` returned `()` for both; `ops` now exposes
`export-expert-labels`; both manifests equal `PLATFORM_LAYER_SLUGS`.

---

## 1. Every file

### New — agri-data-service

| file | what it does |
| --- | --- |
| `src/agri_data_service/warehouse/schemas/fire_risk.py` | the `fire-risk` stream schema, field-for-field equal to `plantgeo_ml_service/warehouse/streams.py::FIRE_RISK_SCHEMA`, plus its `GridAggregation` tier derivation. Header states agri READS, the ML service WRITES (owner decision D2, 2026-09-18). |
| `src/agri_data_service/warehouse/schemas/weather_forecast.py` | the 19 frozen columns of `e66dbc36`, equal to `plantgeo_ml_service/warehouse/weather_forecast.py`, plus its tier derivation. First paragraph states this is NOT a reversal of the 2026-09-19 deletion (that decision was about admission), cites FR-12. |
| `src/agri_data_service/warehouse/schemas/expert_labels.py` | the 24-field Arrow shape of one exported label release, so both services can parity-test it. Not registered: it is no lane. |
| `src/agri_data_service/execution/expert_label_export.py` | row assembly, Parquet encoding, the refusal to overwrite, and the `export-expert-labels` Click command. |
| `src/agri_data_service/sql/execution/select_expert_labels_for_release.sql` | the three-table read, with the beginner walkthrough `sql/AGENTS.md` requires. |
| `tests/execution/test_expert_label_export.py` | 13 tests, no DB and no bucket. |
| `docs/lanes/fire-risk.md`, `docs/lanes/weather-forecast.md` | lane contracts in the existing seven-section shape, naming `services/plantgeo-ml-service` as the writer and its modules. |

### Changed — agri-data-service

- `src/agri_data_service/pipeline/parquet/lane_registry.py` — two `LaneRegistration` entries; a new
  `_foreign_service_refusal` adapter factory distinct from `_source_direct_refusal` (the operator's
  next move differs: "run this module" vs "there is nothing to run here"); the `forecast_module`
  comment now documents the two directories a stem may name and points at the new binding test; a
  comment on `__post_init__` recording that who-writes-a-lane is deliberately unvalidated. Counts in
  the module docstring and the calendar floor basis moved 32→34 and 31→33.
- `src/agri_data_service/warehouse/parquet/schema.py` — `FORECAST_ORIGINATED_STREAMS` and the
  `get_stream_schema(..., "forecast")` branch that returns such a stream verbatim.
- `src/agri_data_service/warehouse/schemas/AGENTS.md` — the two-read-only-lanes section, carrying the
  not-a-reversal paragraph verbatim.
- `src/agri_data_service/pipeline/parquet/objectstore.py` — `ML_PREFIX`, `MAX_ML_OBJECT_BYTES`,
  `validate_ml_relative_path`, `write_ml_object`, `read_ml_object`.
- `src/agri_data_service/foundation/region/layer_availability.py` — both slugs in
  `PLATFORM_LAYER_SLUGS`, with the land-context precedent quoted.
- `src/agri_data_service/foundation/region/{pnw,kenya_highlands}.json` — both slugs in
  `platform_layers`, neither in `enabled_layers`.
- `src/agri_data_service/interface/cli/ops.py` — the new verb.
- `tests/parquet/test_lane_contract.py` — three new tests (below).
- `tests/parquet/test_stream_schema_registry.py` — the provenance-column rule now excuses
  `FORECAST_ORIGINATED_STREAMS`, read from the constant rather than a literal, plus a test that a
  forecast-originated stream is returned verbatim.
- `tests/foundation/test_region_layer_availability.py` — `PILOT_UNBOUND_LAYER_SLUGS` is now three;
  the agent-surface completeness assertion names the three exceptions; one test renamed.

### Changed — web tree

- `src/lib/region/{pnw,kenya_highlands}.ts` — both slugs in `platformLayers`.
- `src/lib/map/layer-region-binding.ts` — both slugs in `REGION_LAYER_SLUG_BY_WAREHOUSE_NAME`, and
  the header's "mirrors `SURFACE_REGION_LAYER_SLUGS` entry for entry" corrected, since neither new
  slug has an agent surface.

---

## 2. What each region/slug change does

Adding a slug to `platform_layers` and NOT to `enabled_layers` is a STATEMENT, not an omission.
`layerBindingInRegion` then answers `unbound` from the manifest, so a caller asking about `fire-risk`
gets "no data source is bound for it here" with a reason, instead of `not_federated` — which is
treated as available and would draw an empty map as a working layer. This is exactly the
`land-context` precedent, and it is the honest state until the ML service publishes a partition.

The four-way identity therefore had to move together: `pnw.json`, `pnw.ts`, `kenya_highlands.json`,
`kenya_highlands.ts` (the twin exists), plus `PLATFORM_LAYER_SLUGS` as the fifth end of the chain.

Adding both to `REGION_LAYER_SLUG_BY_WAREHOUSE_NAME` keeps `TOGGLE_REACHABLE_REGION_LAYER_SLUGS`
equal to `platformLayers` minus `land-context`, which is what
`layer-region-binding.test.tsx`'s "covers every platform layer a toggle can reach" asserts — so that
test needs no edit, and would have FAILED had the table not been updated in the same change.

---

## 3. Predicted failures

### Agri — `scripts/check.py` four gates (`format`, `lint`, `mypy`, `pytest`)

| # | likelihood | gate | what and why |
| --- | --- | --- | --- |
| 1 | **high** | format | `ruff format --check` on seven new/edited Python files written by hand. Expect wrapping differences in the long `floor_basis` string concatenations and in the new test's parametrised helpers. Mechanical. |
| 2 | **medium** | lint | `ANN401` on `_exported_row(row: Mapping[str, Any])` and `_canonical_json(value: object)`; `PLR0913`-adjacent complaints on `write_expert_label_export` (6 parameters, one keyword-only group); possible `TRY003`/`EM101` on the long inline refusal messages, though the surrounding code uses that style throughout. The new test file's `InMemoryBackend.list_objects` raising `NotImplementedError` may draw `ARG002`. |
| 3 | **medium** | mypy | `pa.Table.from_pylist` and `pq.write_table` are untyped (pyarrow ships no stubs); the existing files carry `# type: ignore[import-untyped]` on the import, which I copied, but `table.replace_schema_metadata(None)` and `buffer.getvalue()` may still surface `Any`-return complaints under this repo's strictness. `fetch_rows` returning `Sequence[Mapping[str, Any]]` into `tuple(...)` should be fine. |
| 4 | **medium** | pytest | `tests/parquet/test_tier_derivation.py::test_every_lane_derives_a_real_row_at_every_rung` now parametrises over 34 lanes, including the two new ones. It synthesises a row per column type and derives at every rung through DuckDB. `validate_derivation_against_schema` already returned `()` for both, which covers the coupling failure class, but NOT the runtime one: a `first` aggregate over a timestamp column, or the `mean` over `value` where every base row is null, could surface a Polars dtype complaint the static check cannot see. If one fails, the fix is in the lane's own `aggregations` tuple, not in the test. |
| 5 | **medium** | pytest | Slider/census surface. `parquet_ops/coverage.py::registered_census_lanes` derives from `LANE_REGISTRATIONS` minus `NON_SLIDER_REGISTERED_LAYERS`, so both new lanes now appear as census lanes with zero coverage. FR-5a explicitly wants the slider catalogue to know `fire-risk`, so they are deliberately NOT exempted — but any test under `tests/agent/` or `tests/parquet_ops/` that pins a census lane COUNT or an exact capability list will fail. I found no such hand-spelled pin by grep; this is the residual risk of not having run the suite. |
| 6 | **low** | pytest | `tests/test_sql_tree_conventions.py` over the new `.sql` file: rule (a) header present, rule (b) both bind names (`:release_key`, `:limit`) appear in the body as well as the comments, rule (c) no bare one-word `--` comment below line 1, rule (d) exactly one `load_query_sql` call site. All four were written for deliberately; rule (b) is the one most easily broken by a later comment edit. |
| 7 | **low** | pytest | The new `test_a_forecast_originated_stream_is_returned_verbatim_rather_than_re_provenanced` asserts `get_stream_schema(name, "forecast") is observed` — identity, not equality. It holds today because the branch returns the registered object; if someone later makes that branch copy, the test fails loudly on purpose. |
| 8 | **low** | pytest | Parquet round-trip in `test_the_export_writes_the_part_file_and_a_receipt_beside_it`: `pq.read_table(...).schema.remove_metadata().equals(EXPERT_LABEL_EXPORT_SCHEMA)`. Field nullability survives Parquet, and `timestamp("us", tz="UTC")` round-trips, but this is the one assertion in the file that depends on a library's serialisation rather than on our own code. |
| 9 | **low** | pytest | `tests/direct/test_direct_package_registration.py` collects refusal messages and matches `pipeline.direct.<package>`. The two new refusals name `plantgeo_ml_service.*` instead, so they match no package and cannot falsely satisfy a pending registration. Verified by reading, not by running. |

### Web

| # | likelihood | command | what and why |
| --- | --- | --- | --- |
| 10 | **low** | `vitest src/__tests__/region/` | `manifest-parity.test.ts` diffs `pnw.json` against `pnw.ts` in ORDER; `second-region-manifest.test.ts` asserts four-way identity. Both slugs were inserted at the same alphabetical positions in all four files (`fire-perimeters` < `fire-risk` < `land-context`; `watersheds` < `weather-forecast` < `weather-observations`), so these should pass — but insertion order is exactly the class of mistake a parity test exists to catch, so this is the first thing to run. |
| 11 | **low** | `vitest src/__tests__/region/layer-region-binding.test.tsx` | Three assertions touch the edited table. All three should pass as argued in section 2; the one that would fail on a missed edit is "covers every platform layer a toggle can reach". |
| 12 | **low** | `npx tsc --noEmit -p .` | Two object literals and two string arrays. No new types, no new imports. |
| 13 | **low** | `npm run check:data-boundary` | The edited TS comments name modules and slugs; **no bare URL was added to any `src/**` comment**, which is the trap that failed image build `64a586e1`. The two new `docs/lanes/*.md` files contain relative links only, and `docs/` is outside `src/**` in any case. |

### Not predicted, because not touched

No change to any `pipeline/direct/**` writer, any `execution/vegetation_*` module, any migration, any
Drizzle contract, or `conductor/RUNBOOK.md`. No `git add`, commit, push, or `tsc`/`vitest`/`pytest`
run was performed by this slice.

---

## 4. Two decisions a reviewer should look at first

1. **`FORECAST_ORIGINATED_STREAMS`.** `fire-risk` has no observed side, so its registered schema
   already carries the six provenance columns — which the existing rule "no registered lane declares
   a provenance column on its observed side" would have failed, and which
   `forecast_stream_schema()` would have refused as a collision. Rather than skip the lane with a
   literal in a test, the exemption is a constant in `warehouse/parquet/schema.py` that the LOOKUP
   itself branches on, so a stream cannot be quietly excused from the rule without also changing
   what `get_stream_schema` does with it.

2. **`stratum` is a KEY in the fire-risk tier derivation, not an aggregate.** A coarse cell reports
   one row per stratum present in it. Averaging a closed-forest probability with a rangeland one
   would produce a number no model ever fit, and `stratum` is `nullable=False` so it cannot be
   nulled away. The consequence a reviewer should weigh: coarse fire-risk rungs are larger than a
   naive one-row-per-cell fold would be.
