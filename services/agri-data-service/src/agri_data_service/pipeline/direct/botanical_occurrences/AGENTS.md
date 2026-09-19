---
type: module-notes
module: pipeline/direct/botanical_occurrences
---

# The botanical occurrence lane: why it is shaped unlike its ten siblings

## The unit is a release set, not a day

Every other writer under `pipeline/direct/` publishes a DAY into the frozen
`layer=/kind=/zoom=/day=` partition layout. This one publishes a GENERATION addressed by
`release_set_id` under its own `botanical-occurrences/<release_set_id>/` prefix, and that is not a
stylistic departure. A preserved specimen has two times — the source snapshot that says which records
exist, and the collecting event the record describes — and neither is a daily ecological availability.
Writing a day partition would manufacture the daily cadence the track spec explicitly forbids
("do not create daily ecological availability"), and every zero-filled day in it would be an absence
claim about a collection that simply was not re-exported that day.

That is also why `ObjectStore.write_partition` is not used here while
`conform_to_stream_schema`, `polars_storage_options` and `BotoObjectStoreBackend` ARE: the parts of
the shared machinery that are about SCHEMA and CREDENTIALS apply unchanged; the part that is about
the day layout does not apply at all. `publish.py` reuses the backend protocol and the schema
conformer and adds only the generation layout.

## Why the zoom-tier / `TierDerivation` ladder is not reused for the supports

`warehouse/parquet/tiers.py` derives coarse rungs by simplifying or dissolving PUBLISHED GEOMETRY
from a base rung. The supports here are not derived geometry: they are an ANALYTIC LATTICE, and
membership at each rung is recomputed from the exact record set rather than rolled up from the rung
below. That difference is load-bearing rather than cosmetic — richness is a count of DISTINCT taxon
concepts, and summing a finer rung's counts double-counts every concept present in two cells, which
is precisely the "recomputed per support from exact sets, never summed from children" rule in the
track spec. A `GridAggregation` over the finer rung would produce a number that looks like richness
and is not one. The lattice also needs no simplification pass: a cell polygon is four corners
computed from its own id.

## Cell indices are snapped, never divided

`GridSupport.cell_indices` binned with a bare `math.floor(value / degrees)` until 2026-09-18, and
IEEE division makes that wrong on exactly the points that matter most: a coordinate that IS a
multiple of the pitch. `46.05 / 0.05` evaluates to `920.9999999999999`, so a record sitting exactly
on the origin of cell 921 was filed in cell 920 — one cell south of where its own id says it is, and
a cell whose polygon does not contain it. The same class of defect was measured on the Polars side
in `warehouse/parquet/tiers.py::floor_to_resolution` and fixed there with a snap tolerance; this is
the scalar twin of that rule, with the same `1e-9` and for the same reason.

The rule to keep: **bin by rounding the quotient to an integer and accepting it when it is within
tolerance, and only then fall back to flooring.** Never compare or floor a raw `value / pitch`, and
never assume two runs of the same division agree — they do not across frame lengths, and they do not
across hosts.

## Quarantine is measured, never declared

`quarantine.py` streams every member through `ZipFile.open` and extracts NOTHING to disk. The size,
CRC and hash it reports are recomputed, not read out of the central directory, because a hostile
archive writes whatever it likes into the directory. The controls the governance audit enumerates
map one-to-one onto the reason words in `REJECTION_REASONS`: traversal, absolute, drive and UNC
paths; symlinks; duplicates after NFC normalisation and casefolding; encrypted and nested members;
CRC mismatch; size and ratio anomalies; unsupported member content; and the two required descriptors.

The walk is EXHAUSTIVE rather than first-failure, because an operator who fixes one refusal and
refetches a 60 MiB archive should not discover the second refusal on the next attempt. The two
exceptions are the size caps, which abort mid-member: continuing past a cap is the exact resource
exhaustion the cap exists to prevent.

`max_compression_ratio` catches what the byte totals cannot. One 1 KiB member can expand to the whole
2 GiB decompressed budget and every total still looks compliant on the way down.

## XML is refused on the bytes, not on the parser

`archive_descriptor.py` scans for `<!DOCTYPE`, `<!ENTITY` and external identifiers BEFORE any parser
sees the document, and uses `defusedxml` only when it is installed. A control that works only when an
optional dependency is present is not a control; the byte scan runs either way, and with no DTD able
to reach the parser the only entity references left are the five XML predefines.

An archive whose core `rowType` is not `dwc:Occurrence` is refused rather than read. A Taxon-core
archive is a checklist, and reading its rows as occurrences would manufacture specimen records out
of a name list.

## The row cap produces `partial`, and `partial` is not comparable

`rows.py` reads one row PAST the ceiling deliberately: that is the only way to tell "the file ended
at the cap" from "the file was cut off there". A truncated release is `partial`, and
`identity.py::compare_releases` returns `inconclusive` for any comparison involving one — because you
cannot tell a withdrawn record from an untransferred one, and the audit is explicit that partial
exports must not generate deletion tombstones.

## Dedup annotates, it never deletes

`normalize.py::mark_native_duplicates` keeps every colliding row and writes the collision into
`qc_reasons`, including whether the CONTENT also matched. Native identity removes duplicate
DOWNLOADS, not duplicate SPECIMENS: two sheets from one collecting event legitimately share
collector, date and locality, and the spec's rule is that name/location/date coincidence is not a
deletion rule.

Taxon resolution under `source-names-v1` binds NO external authority. The name read is the
archive's own verbatim `scientificName` when it exports one, else `_joined_scientific_name()`'s
verbatim join of the archive's own `genus`/`specificEpithet`/`infraspecificEpithet`/`taxonRank`
columns (UBC's `occurrence.txt` exports the atomized form only, with no combined column at all —
`v0` read only the combined field and so left every one of its ~193k records `unmatched`). An exact
string match against an in-memory table supplied by the caller resolves; a name matching two
concepts stays `ambiguous`; everything else is `unmatched` under a `source:<collection>:<sha>` key.
A pinned external authority is a LATER recipe with a later version — never a silent upgrade of this
one, because that would change every concept id in a served generation without changing its
identity.

## Fan-out has a cap, and past it the claim weakens rather than the evidence disappearing

`support.py` gives a record `confirmed` membership only when its uncertainty footprint lies inside
one cell AND the coordinate is `exact`. Past `MAX_FANOUT_CELLS` (the 3×3 neighbourhood) the record
keeps ONE `possible` association to the cell holding its nominal point, which makes that cell read
`withheld_or_generalized_only` unless a real record confirms it. Evidence preserved, claim not made.

`evaluated_zero` is materialised only INSIDE the admitted envelope, because that is the only area
where "we looked and found nothing admitted" is a statement this lane is entitled to make. Cells
outside it are not written at all and the reader answers `outside_coverage` for them.
`not_evaluated` is therefore never written here — a cell this code computed was, by definition,
evaluated — and the word exists in the schema for a future partial-coverage generation.

That envelope is MEASURED, not configured. `forward.py`'s `generation_envelope` derives it once per
turn from the admitted `exact` coordinates (`foundation/.../coordinates.py` §`derive_envelope`), and
the one box is threaded into `build_generation_contents` for both the `within_envelope` re-stamp and
`evaluate_support`. Deriving it in one place is the point: when normalize and evaluate_support each
defaulted to their own module constant, the published flag and the published grey cells were two
independent claims about coverage that nothing forced to agree. `config.envelope` is now only the
seed the archives are first read against, and the fallback when a generation admits no exact point.

## Publication order IS the recovery story

Parts → `manifest.json` → `_COMPLETE` → `current.json`. A process killed anywhere before the marker
leaves a directory no reader will open and a pointer still naming the previous generation. Replay is
idempotent BY IDENTITY, not by timestamp: the same releases under the same three recipes address the
same directory, and a directory that already carries `_COMPLETE` is a no-op. That is what makes a
retry safe after a crash of unknown depth.

## `fetch.py` is the only module that opens a socket, and nothing calls it

No scheduled caller, no import from `forward.py`, no default in the CLI. It refuses before opening a
socket unless an admission manifest grants THAT EXACT URL, and it refuses redirects rather than
following them — the audit requires each redirect validated before it is followed, and this lane
validates by not following. The allowlist is three hosts; extending it is an admission decision.

## Schema registration

All seven streams register from ONE module, `warehouse/schemas/botanical_occurrences.py`, which
deviates from that package's one-slug-per-module autoload convention. They are seven grains of one
release set rather than seven lanes. Nothing here resolves a schema by autoload — publisher and
reader import the constants by name — but a future caller that calls
`get_stream_schema("botanical-raw-occurrence")` must import that module first, or split the seven.

## Shared registration is NOT in this tree

`pipeline/parquet/lane_registry.py`, `execution/job_executor_service.py`, `app.py` and
`agent/tools.py` are owned by other in-flight slices. The exact hunks this lane needs are held as a
unified diff in the track's `evidence/shared-registration.patch`, including the entry
`tests/direct/test_direct_writer_contract.py::WRITER_MODULES` needs. Until they land, the two shared
direct-package tests will fail on this package, and that failure is the registration reminder
working as designed rather than a defect in this lane.

## The current pointer: `current.json` writes, `availability/_LATEST.json` serves

`pointer.py` holds the §4a pointer document (`conductor/code_styleguides/layer-lanes.md` §4a) and
`publish.py` writes it LAST, after `_COMPLETE` and after the legacy `current.json`. Two pointers
exist on purpose and they are not redundant: `current.json` is the writer's own bookkeeping
(`read_pointer`, one field, no binding), while `_LATEST.json` is what a serving read resolves and
carries the digest of the exact `manifest.json` bytes it names.

The digest is the whole point. Without it a reader that fetched pointer-then-manifest could observe
a pointer advanced by one writer and a manifest replaced by the next, and would have no way to know
it had assembled an answer from two generations. With it, the serving read
(`planes/botanical_occurrences.py::read_current_botanical_release`) does ONE pointer GET and ONE
data GET, never a listing, and fails closed with a stable reason — `pointer_missing`,
`pointer_malformed`, `pointer_stale`, `pointer_checksum_invalid`, `transport_unavailable` — rather
than falling back to a listing or to `current.json`. A fallback would restore exactly the
unverifiable answer the binding exists to refuse.

STALE means the binding broke, never that the pointer is old: a release set may legitimately be
current for months, so a wall-clock ceiling would refuse correct data on a calendar.

## The legacy bridge is retired (cut: 2026-09-18)

`read_current_botanical_release` resolves ONLY `availability/_LATEST.json`; a missing pointer is
`pointer_missing` regardless of whether `current.json` exists, and `current.json` is never read by
the serving path — it is still WRITTEN, as the publisher's own bookkeeping (`publish.py::read_pointer`).
