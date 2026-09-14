---
type: track-plan
status: pre-acquisition
updated_on: 2026-09-14
---

# GBIF Download API source admission -- plan

## Scope

Metadata and source-governance evidence only. No code, no live network fetch
against GBIF, no acquisition, no institutional or GBIF outreach. This track
records the admission gates a future acquisition pass must clear; it does not
clear them.

## Relationship to `pnw_herbaria_source_admission_20260911`

See [evidence/gbif-vs-institutional-admission-20260914.md](evidence/gbif-vs-institutional-admission-20260914.md)
for the full reasoning. Summary: the existing track is a UBC/WTU-specific
institutional-IPT admission process, mid-flight, with its own
one-archive-at-a-time budget and `admitted_releases` list. GBIF's Download API
is an aggregator with a different terms shape (per-record license, not
per-archive) and a different fetch shape (request-then-poll, not a stable
exact URL up front). This track is a sibling, not a slice of that track, so
neither process blocks or gets diluted by the other.

## What this track records

1. [evidence/admission-decisions.json](evidence/admission-decisions.json) --
   the open pre-acquisition gates, mirroring the shape of the sibling track's
   `admission-decisions.json` (gate list + status), not its content.
2. [evidence/gbif-vs-institutional-admission-20260914.md](evidence/gbif-vs-institutional-admission-20260914.md)
   -- the scoping/identity note above.

## What this track deliberately does NOT do

- Does not decide the `fetch.py` ALLOWED_HOSTS entry or exact-URL manifest
  mechanics -- that is Lane 1's plan (source-and-schema), reviewed against
  this track's recorded gates once published.
- Does not decide the per-record license display/UI treatment -- that is
  Lane 3's (map layer) call if Lane 1's plan calls for extending the
  provisional-governance-status pattern.
- Does not accept GBIF's Data User Agreement or contact any institution.
- Does not acquire, quarantine, or inspect any GBIF archive.

## Gate to close before any GBIF release reaches `admitted_releases`

Same shape as the sibling track: archive/member safety inspection, field-map
reconciliation (now including `license`/`rightsHolder`/`publisher`), and an
owner risk decision analogous to
[owner-risk-decision-20260913.md](../pnw_herbaria_source_admission_20260911/evidence/owner-risk-decision-20260913.md)
that explicitly addresses the per-record license mix and the request-then-poll
fetch shape -- neither of which the UBC/WTU decision was written to cover.
