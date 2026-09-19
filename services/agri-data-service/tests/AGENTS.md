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

**String matches are length-capped** (40 characters, aligned with the TS guard's cap -- NIT 3, W3
review) so a long citation sentence that happens to
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

## Copied ML schemas are pinned twice (`tests/parquet/test_ml_schema_parity.py`)

Three Arrow schemas exist as byte-equal copies in two service trees — `fire-risk` and
`weather-forecast` (written by `services/plantgeo-ml-service`, read here) and `expert-labels`
(written here, read there). The copies are deliberate: the two services deploy independently and
neither imports the other. `scripts/regenerate_ml_schema_fixtures.py:106-134` is the single place
that says which three they are and where each half lives.

### The hole the fixture closes, and the measured instance that proved it

`QUALITY_RECEIPT.json` digests **this** tree only — `scripts/quality_receipt.py:39` lists the
digested directories, and `services/plantgeo-ml-service` is not among them. On 2026-09-19 the ML
service amended `fire-risk`'s `quantile` field, this service's copy did not follow, the suite went
red on `origin/main`, **and the committed receipt still verified over its own 863 files**, because
the breaking commit touched no digested byte. A receipt is a staleness check over reviewed bytes
plus a recorded green run; with one service, the difference between that and a test result was
invisible.

The golden fixtures — `tests/parquet/fixtures/ml_schema_parity/{fire-risk,weather-forecast,expert-labels}.json`
— close it by construction:

1. They live under `tests/`, which is a digest input by `scripts/quality_receipt.py:39`, and the
   exclusion list is build artifacts by name only (`scripts/quality_receipt.py:44-47`), so JSON
   under `tests/` is digested.
2. They are rendered from the **sibling's** modules, never from this service's copies
   (`scripts/regenerate_ml_schema_fixtures.py:255-278`).
3. So when the ML service moves a field, `test_the_fixture_is_the_live_ml_module_so_a_stale_fixture_cannot_pass_as_current`
   (`tests/parquet/test_ml_schema_parity.py:238`) goes red in a monorepo checkout, and the only way
   to make it green is to regenerate a digested byte. That stales the tree digest, so
   `scripts/verify_quality_receipt.py` refuses in the image build (`Dockerfile:48`) until a green
   sweep rewrites the receipt — and `scripts/check.py:685` will not write one over a red sweep, so
   this service's copy has to follow before anything ships. A cross-service schema change is a
   two-tree change after that, by construction rather than by policy.

### Why the regeneration script refuses without the sibling tree

`sibling_tree_absence` (`scripts/regenerate_ml_schema_fixtures.py:240`) is checked before anything
is rendered, and `main` exits 2 rather than writing
(`scripts/regenerate_ml_schema_fixtures.py:294-297`). A fixture rendered from this service's own
copy would agree with that copy by construction and would bless exactly the drift it exists to
catch. `test_the_regeneration_refuses_without_the_sibling_tree_so_no_fixture_is_blessed_from_nothing`
(`tests/parquet/test_ml_schema_parity.py:196`) holds that, including the exit code.

**The fixture is never hand-edited.** The always-on assertion names the regeneration command in its
own failure message (`tests/parquet/test_ml_schema_parity.py:121-137`), and every refusal in
`read_fixture` does too (`scripts/regenerate_ml_schema_fixtures.py:186-212`), so the next person is
told how to update it deliberately instead of guessing.

### What these two guards do NOT catch

- **Neither knows whether anyone read the diff.** They establish only that a cross-service schema
  change cannot be *silent*: it must move a digested byte and appear in a commit. A regeneration run
  without reading the sibling's change still passes. Review is a separate obligation.
- **A monorepo checkout is still required to notice the sibling moving at all.** Check 2 skips when
  the tree is absent (`tests/parquet/test_ml_schema_parity.py:225-229`), which it is inside this
  service's image. The image only ever re-checks this tree against the fixture.
- **Only three contracts are covered**, and the set is a hand-maintained list
  (`scripts/regenerate_ml_schema_fixtures.py:106`). A fourth copied schema added without an entry
  is unguarded; `test_every_copied_contract_has_a_fixture_and_an_agri_object_with_nothing_orphaned`
  (`tests/parquet/test_ml_schema_parity.py:145`) only catches an entry with no fixture or a fixture
  with no entry, not a copy nobody declared.
- **Schema-level metadata is out of scope.** `render_contract`
  (`scripts/regenerate_ml_schema_fixtures.py:151`) keeps name, Arrow type, nullability and order,
  plus stream name, sort columns and codec where the object carries them. pyarrow's pandas metadata
  block carries a library version, so including it would make two identical exports differ.
- **Nothing here checks the partition BYTES**, only the declared shape. A writer that declares this
  schema and emits something else is a different failure, caught at read time and not here.
