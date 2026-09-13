---
type: source-admission-correction
status: fixed
captured_on: 2026-09-13
---

# UBC's archive has no `scientificName` field; the recipe now joins the atomized name

## What was wrong

After publishing UBC v16.43 (generation `0c0f3cb8...`), every one of its ~192,948 records read back
with `scientific_name: null` and `resolution_state: "unmatched"`. Checking the archive's own
`meta.xml` directly showed why: **UBC's `occurrence.txt` declares no `dwc:scientificName` field at
all.** It exports the name atomized instead — `genus`, `specificEpithet`, `infraspecificEpithet`,
`taxonRank` as separate columns — and `normalize.py`'s taxonomy recipe (`source-names-v0`) only ever
read the combined field.

This is a real defect for the stated purpose of this data (intervention modeling needs species
identity), not a source-side limitation to work around by outreach or a governance decision.

## Fix

- [terms.py](../../../../services/agri-data-service/src/agri_data_service/foundation/botanical_occurrences/terms.py)
  now maps `dwc:specificEpithet` and `dwc:infraspecificEpithet` (previously only `genus`/`taxonRank`
  were mapped).
- [normalize.py](../../../../services/agri-data-service/src/agri_data_service/pipeline/direct/botanical_occurrences/normalize.py)'s
  `_joined_scientific_name()` builds the name from genus/specificEpithet/infraspecificEpithet/
  taxonRank verbatim when no combined field is present. A genus-only or family-only determination
  stays exactly that; nothing is padded or invented.
- `TAXONOMY_RECIPE_VERSION` bumped `source-names-v0` -> `source-names-v1` in
  [limits.py](../../../../services/agri-data-service/src/agri_data_service/foundation/botanical_occurrences/limits.py),
  since this changes every concept id derived from a name in a served generation.
- 5 new tests in `tests/direct/botanical_occurrences/test_normalize.py` cover: atomized join, combined
  field taking priority when both exist, genus-only (no invented species), infraspecific+rank join,
  and no-genus-no-combined-field staying correctly nameless.

## Republished

UBC v16.43 republished under the corrected recipe as generation `956c0be7...` (`current.json`
repointed). Verified live against production: sampled records now read real names --
`Magnolia x soulangeana`, `Salix lasiandra`, `Bidens sp.`, `Rhododendron sp.`, `Helianthus cusickii`
-- instead of `null`. The prior generation `0c0f3cb8...` is superseded but not deleted (indefinite
retention per [owner-risk-decision-20260913.md](owner-risk-decision-20260913.md)).

## Still open

`resolution_state` for these records is still `unmatched` -- that is correct and expected under
`source-names-v1`, which binds no external taxonomic authority. A name is now READ correctly; it is
not yet MATCHED against any authority table. That is unchanged scope, not a new gap.
