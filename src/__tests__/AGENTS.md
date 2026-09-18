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

**The scan is hemisphere-neutral, filtered by shape and by name/span instead** (2026-09-18, B2/S1
fix). The prior `west < 0 && east < 0` discriminator could only ever police the pilot's own
hemisphere and, worse, required the closing brace/bracket immediately after the fourth number, so a
Prettier-formatted multi-line literal (every multi-line bbox this repo actually declares) matched
neither pattern at all -- both bugs are fixed together since the multi-line self-test exercises the
same code path the hemisphere removal does. `[0, 5, 9, 13]` (zoom tiers, `lib/map/zoom-tiers.ts`)
and RGBA colour arrays (`lib/map/deck-config.ts`) are now excluded by explicit shape predicates
(`isPlausibleZoomLadder`, `isPlausibleColorTuple`) rather than by hemisphere. What remains -- in
range, ordered, not a ladder or colour -- is a footprint when it carries literal
`west`/`south`/`east`/`north` keys (the object pattern always does), is declared near a
footprint-hinting name (`bbox`/`envelope`/`bounds`/`extent`/`lat`/`lon`, underscore-normalised so
`SCREAMING_SNAKE_CASE` names still hit a word boundary), or its span is plausible for a region
(0.5-60 degrees each axis). `footprint-literals.test.ts` also now pins the guard's own detection —
a planted single-line object, a planted multi-line object, a planted multi-line array, and a
fabricated eastern-hemisphere (Kenya) box — not just its verdict on the live tree, which is how the
multi-line gap shipped invisible in the first place.

**`KNOWN_OFFENDERS` is keyed by `(path, description)`, never line number** (S2 fix): the
description already carries the offending value, and a line-keyed entry fails this test on any
unrelated edit above it, in both directions at once. It holds two real entries as of the
2026-09-18 push: `coverage-region.ts`'s `NAMED_COVERAGE_REGIONS` California/Western-US/North-America
rows (they describe footprints this deployment does not serve — `src/lib/region/AGENTS.md`
"Remaining footprint literals `coverage-region.ts` still carries" names the future multi-region
registry that moves them), and `OfflinePanel.tsx`'s offline-download default bbox (a genuinely
different footprint from the manifest's `defaultCameraEnvelope` — reading the manifest here would
silently widen the pre-existing default download area, which is a behaviour change this guard is
not authorised to make). A third candidate, `useLandContextQuery.ts`'s `resolveBoundaryInArea`
fallback, matched `getRegion().defaultCameraEnvelope` exactly and was fixed in the same push rather
than added to the debt list.
