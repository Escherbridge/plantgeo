---
type: module-notes
module: foundation/botanical_occurrences
---

# Why these five modules exist, and what each refuses to do

Five primitives the occurrence lane cannot be honest without. They live in `foundation` (L0) because
the quarantine reader, the normaliser, the publisher, the plane reader and the agent tools all need
the same answers, and an answer computed twice drifts.

## `limits.py` — the ceiling is a copy of a decision, not a policy of its own

The eleven numbers restate `admission-decisions.json` → `limits` verbatim. They are NOT this module's
to raise. Two archives, one transfer at a time, 64 MiB compressed per archive, 128 MiB total, 2 GiB
decompressed, 32 members, 600,000 core rows, 2,000,000 extension rows, 8 HTTP attempts, 30 s per
request, 600 s wall. The audit is explicit that "no third archive or widened budget is implied", so
the correct response to an overrun is to defer or fail, never to widen.

`max_compression_ratio` is the one field with no counterpart in the decisions file, and it is here
because the decompressed-byte total alone is not a zip-bomb control: one 1 KiB member can expand to
the entire 2 GiB budget before any total is exceeded, and the archive would look compliant the whole
way down. A per-member ratio is what makes the bomb visible at the member that carries it.

The four version constants (`PARSER_VERSION`, `QC_POLICY_VERSION`, `TAXONOMY_RECIPE_VERSION`,
`SUPPORT_VERSION`) are bound into the release-set identity. That is what makes a recipe change a NEW
generation rather than an edit of one already serving — the property the track's rights/taxonomy/QC
acceptance criterion is asking for.

## `release_identity.py` — content is in every key on purpose

`release_key` hashes (collection, publisher version, archive sha256). The archive hash is the only
one of the three that proves anything: a filename, a version URL, an HTTP validator and a retrieval
time all change without the bytes changing, which is the audit's own "neither mutable filename,
version URL, HTTP validator nor retrieval time proves immutable archive bytes".

`occurrence_id` hashes (collection, native record key, row content hash) rather than the native key
alone. A publisher that reuses a native key for different content has published a DIFFERENT record,
and the identity-stability report exists to see exactly that. Keying on the native key alone would
overwrite the evidence the report is built to find.

`release_set_id` sorts and de-duplicates its inputs, so a generation's identity depends on WHICH
releases it reads, not on the order a caller listed them.

## `event_interval.py` — a year is a year, not the first of January

The spec's rule is "partial and interval dates remain partial or interval-valued; no exact day is
invented", and this module is where that is enforced rather than promised. `1987` becomes
1987-01-01..1987-12-31 at precision `year`; `1987-06` becomes the whole month; an ISO range keeps
precision `interval` even when both ends are exact days, because the record does not say which day
inside it the event happened.

`UNKNOWN_EVENT` overlaps NOTHING, deliberately. A record whose date nobody knows must not answer an
event-window filter — silently including it would turn "we do not know when" into "it is in range".

The year bounds are typo guards, not history claims. A collection holding genuinely pre-1700
material moves `MINIMUM_EVENT_YEAR` with its own evidence; until then a three-digit-looking year in
a modern export is far more often a transcription artefact than a seventeenth-century specimen, and
this lane would rather refuse to date a record than date it wrongly.

## `coordinates.py` — the publisher's suppression wins over the publisher's numbers

The classification order is fail-closed and the first step is the important one: a record whose
`informationWithheld` says the location is suppressed is `withheld` EVEN IF the coordinate columns
still hold numbers, because what is left in those columns is usually a county centroid, and the
track spec's "county centroids never become specimen points" is precisely that case. Withheld
records keep no longitude or latitude at all in the normalized stream, so nothing downstream can
reconstruct them.

Then: missing, out-of-range and null-island coordinates are `nonspatial` (null island is the shape a
failed georeference takes, not a place plants grow). Then a publisher generalization statement, an
uncertainty ≥ 10 km, or a datum this lane cannot verify demote an otherwise exact point to
`generalized`. An unverified datum is NOT silently reprojected: that would move the point by an
unknown distance and still call it exact.

`DECLARED_ENVELOPE` is a PLACEHOLDER and is marked as one in the code. It is the track's scoping box,
not a measured coverage claim, and the first admitted release's own EML coverage statement replaces
it. Its only job is to bound where `evaluated_zero` may be asserted: "we looked here and found
nothing" is honest only inside admitted coverage. A specimen outside the envelope is still published
with `within_envelope=False`, because a specimen collected outside the box is a real specimen.

`WITHHELD_MARKERS` and its siblings are a best-effort READ of a publisher policy, not the policy. A
phrase they have not learned leaves a record `exact`, which is why both source terms also survive
verbatim in the raw stream — a later pass can reclassify from the preserved text without refetching.

## What is deliberately NOT here

No HTTP, no zipfile, no Arrow, no object store. These are pure functions over strings and floats, so
the quarantine tests can exercise the classification tables without building an archive, and so a
reviewer can read the honesty rules without reading the pipeline.
