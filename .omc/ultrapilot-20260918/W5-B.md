# W5-B — the "not available in this region" path (federation §5 step 4b)

Branch `wave5-unbound-layer-absence`, worktree
`C:\Users\atooz\Programming\plantgeo\.claude\worktrees\wave5-unbound-layer-absence`.
Commits `0b7a3fe3` (service), `c98ea9df` (web), `7e0250db` (tests). Nothing pushed.

## Files

### Service (Python)

| File | What |
|---|---|
| `services/agri-data-service/src/agri_data_service/foundation/region/layer_availability.py` | NEW. `PLATFORM_LAYER_SLUGS`, `LayerBindingState`, `LayerBindingStatus`, `region_layer_availability(region)`, `is_layer_bound(region, slug)`. |
| `.../foundation/region/__init__.py` | Exports the six new names. |
| `.../foundation/region/AGENTS.md` | New section "Layer availability, and why the catalogue is hand-spelled". |
| `.../parquet_ops/wire.py` | NEW `LayerBindingCoverage` dataclass; `WarehouseCoverage.layer_bindings` (defaulted `()`), rendered by `to_wire()`. |
| `.../interface/http/parquet_routes.py` | `region_layer_bindings()` builds the field from `load_region()` on every census build; wired into `_build_coverage_payload`. |
| `.../agent/surfaces.py` | NEW `SURFACE_REGION_LAYER_SLUGS`, `SIGNAL_PLANE_REGION_LAYER`, `FIRE_REGION_LAYERS`, `surface_region_layer()`. |
| `.../agent/tools.py` | NEW `_region_absence` / `_surface_region_absence`; gates in ten tools. |
| `.../agent/AGENTS.md` | New section "A layer this region binds no source for". |
| `services/agri-data-service/tests/contract/wire_contract.py` | NEW `WireLayerBinding`; `WireCoverage.layer_bindings` defaulted. |
| `services/agri-data-service/tests/contract/fixtures/coverage.json` | `"layer_bindings": []` (the states-nothing case). |
| `services/agri-data-service/tests/contract/fixtures/coverage_availability.json` | Four bindings covering all three states. |
| `services/agri-data-service/tests/foundation/test_region_layer_availability.py` | NEW. The boot-with-global-lanes-only proof + the PNW regression. |

Gated tools: `observation_coverage_on_day`, `observation_temporal_neighbors`,
`feature_value_near_point`, `surface_value_near_point` (via the surface map);
`drought_history_at_point`, `fire_history_near_point`, `signals_near_point`,
`signal_value_on_day`, `signal_neighbors_in_time`, `nearest_signal_cells` (fixed lanes).

### Web (TypeScript)

| File | What |
|---|---|
| `src/lib/map/layer-region-binding.ts` | NEW. Warehouse-name → manifest-layer table, `regionLayerSlugForToggle`, `isLayerUnboundInRegion`, `regionLayerBindingForToggle`, `unboundLayerCaption`. |
| `src/lib/map/AGENTS.md` | New section `layer-region-binding.ts`. |
| `src/lib/server/services/parquet-plane-client.ts` | `PARQUET_LAYER_BINDINGS`, `ParquetRegionLayerBinding`, `ParquetWarehouseCoverage.layerBindings`, optional `layer_bindings` in `wireCoverageSchema`, mapped in `decodeCoverage`. **The `WIRE` const block is untouched.** |
| `src/lib/server/services/parquet-slider-capabilities.ts` | `ParquetSliderCapabilities.layerBindings` + `toSliderLayerBindings`; `[]` on the coverage-unavailable branch. |
| `src/types/time-slider.ts` | `LAYER_BINDING_STATES`, `LayerBindingState`, `SliderLayerBinding`, optional `SliderCapabilities.layerBindings`. |
| `src/lib/map/layer-toggle-context.ts` | `useLayerVisibility` reads false for an unbound layer — the single seam that makes "no fetch issued" true. |
| `src/components/map/layer-panel/LayerRow.tsx` | Unbound → `isWithheld` (switch disabled), amber caption replacing every other caption, `data-testid="layer-region-absence-<id>"`. |
| `src/__tests__/region/layer-region-binding.test.tsx` | NEW. Catalogue render + fail-open rules. |
| `src/__tests__/services/parquet-slider-capabilities.test.ts` | `setCoverage` gains a defaulted `layerBindings`; three carry-through cases. |
| `src/__tests__/services/parquet-plane-client.test.ts` | Two decode cases (with and without the field). |

`LayerLegend.tsx` needed no edit: it renders DRAWN layers, and an unbound layer cannot be drawn
once `useLayerVisibility` reads false for it. The catalogue surface is `LayerRow`.

## Payload delta (additive field names only)

`/api/v1/parquet/coverage` gains ONE top-level key; every existing key and every lane row is
byte-identical to today.

```
layer_bindings: [ { layer, binding, source, reason } ]
                   binding ∈ {"bound_global","bound_regional","unbound"}
                   source  = null exactly when binding == "unbound"
                   reason  = "no_source_bound_in_region" exactly when unbound, else null
```

Client-side names: `ParquetWarehouseCoverage.layerBindings` →
`ParquetSliderCapabilities.layerBindings` → `SliderCapabilities.layerBindings?` with entries
`{ layerSlug, binding, sourceSlug, reason }`.

**`COVERAGE_SCHEMA_VERSION` stays at 3.** This is a deliberate deviation from
`parquet_ops/AGENTS.md`'s "putting fields on coverage is a contract change, not an addition", and
the reasoning is recorded in `WarehouseCoverage.to_wire()`'s docstring: the version gate exists so a
client cannot read a field's SILENCE as health, and here silence means "this deployment states no
bindings", which the only consumer renders exactly as today's payload — every layer available.
There is no reading of the absent field that is a false claim, so a version rejection would blank a
working slider during the deploy window for no gained safety. Python `layer_bindings` is defaulted
and TS `layer_bindings` is `.optional()`, so old-server/new-client and new-server/old-client both
work. **Flag for review** if the wave wants the bump anyway; it is a 3→4 edit across `wire.py`,
`wire_contract.py`, both fixtures and `parquet-plane-client.ts`.

## Verified vs assumed

Verified by reading:
- `useLayerVisibility` is the single gate every layer component mounts off, and already withholds on
  `permanentlyUnavailableReason` — the exact precedent this follows.
- `LayerRow`'s `isWithheld` already disables the switch and renders a caption; the amber tone copies
  `ParquetLayerFaultBanner`'s `notice` classes (`text-amber-700 dark:text-amber-400`).
- `soil-survey` (not `soil`) is the right test subject: `soil` has `warehouseLayerName: null` AND a
  `permanentlyUnavailableReason`, so it would be vacuous.
- PNW binds all thirteen `PLATFORM_LAYER_SLUGS`, so the pilot is behaviour-neutral.
- `interventions` is absent from the manifest, from `SURFACE_PARQUET_LANES` and now from the
  platform vocabulary — its tool answers `surface_not_served_from_parquet` exactly as before.
- `agent` is not a layer in `test_layer_import_contract.py`, and `interface` has no forbidden
  imports, so the new `foundation.region` imports are legal.
- `run_context(session_provider=None, ...)` is the documented default; `asyncio_mode = "auto"`.
- The `WIRE` const block in `parquet-plane-client.ts` was not edited.

Assumed (NOT run — no test/lint/typecheck/build/uv by instruction):
- `pytest` passes. `test_coverage_fixture_round_trips` is the one I changed the shape under; both
  fixtures got `layer_bindings` so the dump/compare should still match exactly.
- `vitest`/`tsc` pass. The riskiest surface is the new `layerBindings` required field on
  `ParquetWarehouseCoverage`: I found and patched five object literals in two test files
  (`mockResolvedValue`/`mockReturnValue`/`resolve(...)`), but `mocks` is `vi.hoisted(vi.fn())` and
  therefore loosely typed, so a missed literal would surface as a runtime `undefined` → `[]`
  rather than a compile error.
- `eslint --fix` ran automatically via the commit hook on all three commits and reported clean.

## Predicted breakage

1. **Most likely:** `services/.../tests/interface/test_parquet_routes.py` validates the live coverage
   payload against `WireCoverage`. It now carries `layer_bindings`; the contract model accepts it,
   but if that test asserts an exact key SET rather than validating the model, it fails. One-line
   fix either way.
2. **Likely:** a Python test asserting `WarehouseCoverage(...).to_wire()` equals a literal dict
   (`tests/parquet_ops/test_coverage_census.py`, `test_availability_coverage.py`,
   `execution/test_gap_repair.py` all build `WarehouseCoverage`). Any that compares the rendered
   dict rather than reading fields will now see the extra `layer_bindings: []`.
3. **Possible:** a vitest snapshot or `toEqual` over the whole `getParquetSliderCapabilities()`
   result now sees `layerBindings: []`.
4. **Possible:** `test_agent_tools_http.py` / `test_agent_parquet_tools.py` — every gated tool now
   calls `load_region()`, which reads `PLANTGEO_REGION` and parses `pnw.json` on first use. Under
   PNW nothing is unbound so no assertion changes, but a test that monkeypatches `PLANTGEO_REGION`
   to a junk value would now raise `ValueError` from a tool that previously ignored the region.
5. **Watch:** `useLayerVisibility` gained a `useTimeSliderStore` subscription. It now re-renders on
   every capabilities write (a 5-minute poll), where before it only followed `activeLayers`. The
   selector returns the whole `capabilities` object, so the `useMemo` re-runs once per poll — cheap,
   but it is a new render edge in the hottest layer hook. A narrower selector would need the toggle
   id, which this hook does not have.
6. **Contract-review point:** `coverage_availability.json` now states `soil-survey` as `unbound`.
   That is a fixture exercising the decode, not a claim about the pilot; if any test cross-checks a
   fixture against the real manifest it will disagree.
