---
type: code-styleguide
---

# PlantGeo federation and portability standard

PlantGeo is a forward-deployed product. The Pacific Northwest is the **pilot
region**, not the platform. Every other georegion (another US state cluster, a
Mediterranean basin, an East African highland) is expected to run the same
tree with a different region manifest and, where a data source is regional, a
different source binding. This guide states what that requires of code today,
so the PNW pilot stays a reusable base rather than becoming a fork's ancestor.

Owner rulings 2026-09-18 (grilled once, recorded here so they are not
re-litigated): **one typed region manifest** is the single declaration of a
deployment's footprint; **regional data sources bind behind a per-layer source
interface**; module size is **soft guidance**, not tooling. It inherits
[`engineering-principles.md`](./engineering-principles.md) §5 and is made
concrete by the TypeScript, Python, SQL and layer-lane guides.

## 1. One region manifest, one place for every footprint literal

- **A deployment has exactly one region manifest.** It is typed (a frozen
  Pydantic model in `foundation/region/` for the service; a `satisfies`-checked
  object next to `src/lib/map/coverage-region.ts` for the web tree, generated or
  parity-tested against the service copy) and it declares, at minimum:
  `slug`, `display_name`, `envelope` (west/south/east/north, WGS84), `crs`
  for any projected work, lattice `pitch_degrees` and its origin rule,
  `timezone`, the ISO country and first-level admin codes it spans, and the
  list of **enabled layers with their source binding** (§2).
- **A literal latitude, longitude, envelope, state code or region name outside
  the manifest is a violation.** Today's offenders are the migration list, not
  precedent: `PACIFIC_NORTHWEST_BBOX` (`ingest/mtbs.py:76`), `SEED_ENVELOPE`
  (`foundation/botanical_occurrences/coordinates.py:22`), `PNW_STATE_CODES`
  and `PnwStateCode` (`src/lib/server/db/schema/land-context/shared.ts`),
  `PNW_COARSE_NODES`, the `NAMED_COVERAGE_REGIONS` table and
  `FALLBACK_COVERAGE_BBOX` (the opening camera when `NEXT_PUBLIC_INGEST_BBOX`
  is unset) in `coverage-region.ts`, and the `(-125, 42, -111, 49)` burn
  envelope restated in `pipeline/direct/burn_severity/{capture,current_snapshot,
  stage}.py` and defaulted as `SUPPORTED_BURN_SNAPSHOT_SCOPE` in the
  `parquet-trpc-readers/burn-severity.ts` reader (found by the 2026-09-18
  readability swarm). Each moves into (or reads from) the manifest in its own
  small commit; call sites take the value as a parameter or read the manifest,
  never re-declare it.
- **Permitted literal coordinates** are the world envelope (`-180,-90,180,90`
  as the explicit "no viewport" sentinel), test fixtures that state the region
  they model in their name, and source-system constants that are genuinely
  about the source (a provider's own tile origin). Anything else is footprint.
- **The manifest is read at ingress once and passed down.** Lanes, planes,
  agent tools and tRPC readers receive a `Region` value; they do not import a
  module-level constant that hides the dependency. A function whose behaviour
  depends on the region says so in its signature.
- **Region names never appear in symbol names, object-store prefixes, table or
  column names, or route paths.** `region.slug` is data. `layer=<slug>/` stays
  region-free; the region is a manifest field on the deployment, and a future
  multi-region bucket adds `region=<slug>/` as an outer partition rather than
  renaming lanes.

## 2. Regional sources bind behind a per-layer source interface

Layers are the platform's vocabulary (soil, drought, burn severity, fire
detections, vegetation, water gauges, weather, watersheds, evacuation zones,
sensors, botanical occurrences). Sources are how one region fills them
(SSURGO, USDM, MTBS, FIRMS, MODIS, USGS NWIS, Open-Meteo, HydroSHEDS...). The
pilot binds US sources; another region binds its own.

- **Every layer defines a source protocol** in its lattice layer of record: a
  `typing.Protocol` (service) or an `interface` (web) stating the pull, the
  normalized record shape, the provenance fields, the coverage claim and the
  availability query. The protocol is the layer's contract; a source is one
  implementation, named `<layer>/<source>.py` (`pipeline/lanes/soil/ssurgo.py`),
  never `<source>.py` at the layer root.
- **Every source declares `coverage`**: `global` (FIRMS, MODIS, ERA5, POWER,
  Open-Meteo, HydroSHEDS, GBIF) or `regional` with the ISO codes it serves.
  The manifest may only bind a source whose coverage contains the region's
  envelope; a binding that does not is a startup error, not a runtime surprise.
- **Layer logic is source-agnostic.** The Monte Carlo method, the Parquet
  schema, the availability index, the serving plane and the agent tool for a
  layer consume the protocol's normalized record. If a plane branches on the
  source name, the normalization is incomplete and the branch is the bug.
- **The platform must run with a layer unbound.** A region with no soil source
  yet gets a soil layer that reports `not available in this region` through
  the slider capability catalogue, legends and agent tools. A missing binding
  is a governed absence with a named reason, never a crash, never an empty map
  that looks like an outage, and never a silent fallback to the pilot's source.
- **Units, datums and calendars normalize at the source boundary.** The
  protocol's record is SI, WGS84 and UTC with a declared local timezone; a
  source that reports inches, NAD83 or local civil time converts inside its
  own implementation and records the conversion in provenance.

## 3. Readability is portability

Someone standing up the next region will not have the pilot's authors in the
room. The code has to teach itself.

- **Full-word names** (`bounding_box`, `horizontal_padding`,
  `create_flex_board`), never cryptic abbreviations. Domain terms the whole
  field uses (`NDVI`, `HUC12`, `SRID`, `bbox` inside a type name) are words.
- **Name the algorithm at its implementation** (`# Boyer-Moore search`,
  `// Douglas-Peucker simplification`) and link the paper or wiki page for a
  genuinely esoteric one. That is a "what", not a rationale essay.
- **Rationale lives in the directory's `AGENTS.md`**, with a one-line pointer
  from code (`see pipeline/lanes/AGENTS.md §soil`). A newcomer reads the
  directory before the file.
- **Size is soft guidance, applied in review:** aim for modules under ~600
  lines, functions under ~60, nesting no deeper than three. The existing
  3,000-line modules are not to grow; split them along the seams the lattice
  already draws (schema / method / pipeline / plane) when a change touches
  them. No linter enforces this by owner ruling; a reviewer citing this
  section is the enforcement.
- **A region-specific assumption is written down where it is made.** "Fire
  season is June to October" is a PNW fact; it belongs in the manifest or in a
  source implementation with a comment naming the region, not in shared layer
  logic.

## 4. What a federation-ready change proves

- [ ] No new footprint literal outside the manifest; any touched legacy literal
      moved in or parameterised.
- [ ] A new or changed source declares `coverage` and implements the layer's
      protocol; layer logic gained no source-name branch.
- [ ] The platform's boot-with-global-lanes-only path still works (the test that
      enables only `coverage: global` sources and checks the slider catalogue,
      legends and agent tools report the rest as unavailable).
- [ ] Symbols, prefixes, routes and object names contain no region name.
- [ ] Names are full words; rationale is in `AGENTS.md`; the touched module did
      not cross the soft size ceiling, or the change split it.

## 5. Migration order for the pilot

Do these as separate small pushes, each reviewed once:

1. Land the `Region` type and the PNW manifest; point `coverage-region.ts` and
   the service at it. No behaviour change.
2. Move `PACIFIC_NORTHWEST_BBOX`, `SEED_ENVELOPE`, `PNW_STATE_CODES`,
   `PNW_COARSE_NODES` into or behind the manifest, one per push.
3. Introduce the soil, drought and burn-severity source protocols and rename
   the SSURGO, USDM and MTBS lanes to `<layer>/<source>` files.
4. Add the boot-with-global-lanes-only test and the stray-literal test.
