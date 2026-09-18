# `src/__tests__/` — web-tree test conventions

## Stray-literal guard (`region/footprint-literals.test.ts`)

Web-tree counterpart to `services/agri-data-service/tests/test_region_literal_contract.py` (read
that file's `AGENTS.md` entry first — the design tradeoffs are the same). Regex-scans every
`src/**/*.{ts,tsx}` file (excluding any directory named `__tests__`/`__benchmarks__`, `.test.ts(x)`
files, and `src/lib/region/{pnw,region}.ts`, the manifest's own declaration) for a 4-number bbox
string/array/object that reads as a western-hemisphere WGS84 footprint, and for the
`US-WA`/`US-OR`/`US-ID`/`Pacific Northwest`/`PNW` markers.

**Comments are stripped before scanning.** `coverage-region.ts`'s own JSDoc says `a PNW box reads
"Pacific Northwest" rather than the "North America" box` — a quoted string sitting inside a
comment, which a naive `/".../"/g` scan cannot tell apart from a real code string. The stripper
matches string/template literals *before* comment alternatives at every position (the standard
trick for comment-stripping without a full tokenizer: a `//` inside a quoted URL is consumed as
string content before the comment branch is ever tried), then blanks `//`/`/* */` comments
character-for-character so line numbers stay correct.

**The western-hemisphere discriminator matters here too**: `[0, 5, 9, 13]` (zoom tiers,
`lib/map/zoom-tiers.ts`), RGBA colour arrays (`lib/map/deck-config.ts`), and NLCD class codes
(`lib/environmental/nlcd.ts`) are all "four numbers in lon/lat range" without it. Requiring `west <
0 && east < 0` for the object/array forms is what keeps the scan from flooding on those.

**`KNOWN_OFFENDERS` holds two real entries as of the 2026-09-18 push**:
`coverage-region.ts`'s `NAMED_COVERAGE_REGIONS` California/Western-US/North-America rows (they
describe footprints this deployment does not serve — `src/lib/region/AGENTS.md` "Remaining
footprint literals `coverage-region.ts` still carries" names the future multi-region registry that
moves them), and `OfflinePanel.tsx`'s offline-download default bbox (a genuinely different
footprint from the manifest's `defaultCameraEnvelope` — reading the manifest here would silently
widen the pre-existing default download area, which is a behaviour change this guard is not
authorised to make). A third candidate, `useLandContextQuery.ts`'s `resolveBoundaryInArea`
fallback, matched `getRegion().defaultCameraEnvelope` exactly and was fixed in the same push rather
than added to the debt list.
