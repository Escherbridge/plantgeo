# `tests/` — service-tree test conventions

## Stray-literal guard (`test_region_literal_contract.py`)

`conductor/code_styleguides/federation.md` §1 wants exactly one place a deployment's footprint is
declared — the `Region` manifest in `foundation/region/`. `test_region_literal_contract.py` is the
regression guard: it walks every `src/agri_data_service/**/*.py` module with `ast` and fails on a
new `Assign`/`AnnAssign` whose value contains a WGS84 footprint tuple, a PNW admin code
(`US-WA`/`US-OR`/`US-ID`), or the words `Pacific Northwest`/`PNW`.

**The walk is scoped to assignment statements, not every string literal.** An earlier draft that
scanned all `ast.Constant` strings flooded on prose: module docstrings, `AGENTS.md`-style
rationale comments, and dict literals passed straight into `session.execute(...)` as a data-source
`purpose`/`description` field (`execution/vegetation_ndvi_plane.py`'s `"...for the Pacific
Northwest 0.25-degree vegetation lattice..."`) all mention the region by name without declaring
its footprint. Restricting the walk to `Assign`/`AnnAssign` targets excludes bare-string docstrings
(they are `Expr` statements) and call-keyword-argument dicts (they are not assignments) for free,
while still catching every named constant federation.md §1 lists as an offender
(the former `PACIFIC_NORTHWEST_BBOX` and `SEED_ENVELOPE`, now manifest-reading functions, plus
`PNW_STATE_CODES` and `PNW_COARSE_NODES`).

**Numeric 4-tuples are hemisphere-neutral, filtered by shape and by name/span instead** (2026-09-18,
S1 fix): a `west < 0 and east < 0` discriminator can only ever police the pilot's own hemisphere --
a fabricated eastern-hemisphere region (a Kenya box, say) would be structurally undetectable, which
defeats the guard's own portability purpose. `(0, 5, 9, 13)` (a zoom-tier ladder,
`foundation/parquet/zoom.py`, `parquet_ops/mtbs_snapshot_catalog.py` and elsewhere) and any 4-item
all-integer 0-255 tuple (a colour) are excluded by explicit shape predicates
(`_is_plausible_zoom_ladder`, `_is_plausible_color_tuple`) instead. What remains -- in range,
ordered west<east/south<north, not a ladder or colour -- is a footprint only when it is EITHER
assigned to a footprint-hinting name (`_NAME_HINT_PATTERN`, underscore-normalised so
`SCREAMING_SNAKE_CASE` names still hit a boundary) OR its span is plausible for a region (0.5-60
degrees each axis, narrower than a state, wider than a neighbourhood).

**String matches are length-capped** (30 characters) so a long citation sentence that happens to
mention "PNW" (`pipeline/parquet/lane_registry.py`'s `_climate_floor_basis` rationale, "...MEASURED
against POWER's live solar edge on 2026-09-15 at five PNW points...") doesn't count as a declared
region-name literal; a real offender (`GBIF_COLLECTION_KEY = "gbif:pnw:vascular"`,
`_PILOT_REGION_SLUG = "pnw"`) is short. This also relies on `PNW`/`Pacific Northwest` being an
exact-case match — `"gbif:pnw:vascular"`'s lowercase `pnw` is a GBIF collection-key convention
(a source-system identifier, federation.md §1's named exception), not this region's name.

`KNOWN_OFFENDERS` is the debt list: empty as of the 2026-09-18 push (wave 2 already pointed every
literal this walk can see at `load_region()`). A new offender fails the test; removing an entry
once its value reads from the manifest is the only edit the set is allowed. Entries are keyed by
`(path, description)`, never line number (S2 fix) -- the description already carries the offending
value, and a line-keyed entry fails this test on any unrelated edit above it, in both directions at
once.
