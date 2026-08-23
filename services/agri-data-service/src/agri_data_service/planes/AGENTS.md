# Layer L3: Planes

## Responsibility
Domain execution planes that bind method algorithms and pipeline acquisition outputs into warehouse persistence.

## Dependency Rules
- **May import**: `foundation` (L0), `method` (L1), `warehouse` (L1), `pipeline` (L2).
- **May NOT import**: `interface` (L4).

## The zoom axis: one rule, no exceptions

Every PUBLIC function in this directory takes a `requested_zoom: int` -- the map zoom a viewport is
actually at -- and resolves it exactly once through `foundation.parquet.zoom.serving_zoom_tier`.
Every PRIVATE helper takes the already-resolved `zoom: ZoomTier`. The two types are the signal: an
`int` has not been resolved yet, a `ZoomTier` has, and nothing in between exists.

A plane resolves rather than demanding a tier because `serving_zoom_tier` walks DOWN -- z11 reads the
z9 rung -- and that direction is a correctness rule, not a rounding preference (`zoom.py`'s module
docstring: rounding up claims a resolution the writer never generalised to). Every caller
re-deriving it is one caller away from rounding the other way. Resolution is idempotent, so a caller
that already holds a tier may pass it straight in: every tier is a legal request that resolves to
itself.

Three consequences worth stating, because each has a silently-wrong alternative:

1. **One call, one rung, listing and scan alike.** When a function lists to discover a day and then
   scans to read it, both name the SAME resolved tier. Splitting them yields a release day that is
   real and rows that are not the ones it names.
2. **No "all tiers" mode, and no default.** A default would decide the axis quietly, and the mistake
   surfaces as geometry at the wrong resolution rather than as an error. A blended read is not
   expressible here: no signature accepts more than one zoom.
3. **The answer says which rung answered.** Where a module already names what answered
   (`answered_by_snapshot_day`, `valid_date`, `release_day`), it names the tier beside it. A z11
   request served from z9 got a different resolution than it asked for, and that is the same class
   of fact as being served a three-day-old snapshot.

The tier is NOT stamped as a data column anywhere. `kind` is, because Polars' Hive injection is
inconsistent between empty and non-empty scans and because callers concatenate two kinds' frames; no
caller ever builds a frame spanning two tiers, so a per-row tier stamp would disambiguate a case that
cannot arise while adding a column no registered schema has.

`pipeline/validation/*` deliberately does NOT follow this rule -- see that directory's own note.
