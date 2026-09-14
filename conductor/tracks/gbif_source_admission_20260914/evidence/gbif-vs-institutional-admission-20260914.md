---
type: source-admission-scoping-note
status: informational
updated_on: 2026-09-14
---

# Why GBIF gets its own track, not a slice inside `pnw_herbaria_source_admission_20260911`

## The check performed

Before creating this track, this evidence trail was checked against the sibling
[`pnw_herbaria_source_admission_20260911`](../pnw_herbaria_source_admission_20260911/metadata.json)
to see whether it already covers GBIF or is "scoped tightly to UBC specifically"
(per this lane's brief). Finding: **tightly scoped, and mid-flight** —
`admission_status: "one-archive-quarantined-post-capture-gates-open"`,
`admitted_releases: []`, and a stated "two-archive, one-transfer-at-a-time
budget" that has UBC acquired into quarantine and WTU deliberately deferred
behind it (see
[owner-risk-decision-20260913.md](../pnw_herbaria_source_admission_20260911/evidence/owner-risk-decision-20260913.md)
lines 70-72). Adding a third source into that budget mid-decision would blur
an already-auditable, already-decided scope.

## The identity trap this track must not repeat

The existing track's own evidence already flags a naming collision worth
carrying forward: WTU's specimen data *is itself distributed through GBIF*
(GBIF UUID `8310f570-f762-11e1-a439-00145eb45e9a`, served from
`ipt.pnwherbaria.org`, see
[wtu-gbif-identity-correction-20260913.md](../pnw_herbaria_source_admission_20260911/evidence/wtu-gbif-identity-correction-20260913.md)).
That is **not** what this mission means by "the GBIF source" — the mission's
GBIF source (per the seeded goal finding) is GBIF's **Download API acting as
an aggregator**: one predicate-bounded request that returns a Darwin Core
Archive spanning hundreds of herbaria (UBC's own feed among them) plus
iNaturalist research-grade observations, not a single institution's IPT
resource. The two are related (both eventually flow through GBIF's
infrastructure) but are administratively distinct:

| | `pnw_herbaria_source_admission_20260911` | this track (`gbif_source_admission_20260914`) |
| --- | --- | --- |
| What is admitted | One institution's own IPT-published archive (UBC, later WTU) | GBIF's own Download API aggregate export (a DOI-stamped, multi-provider snapshot) |
| License shape | One license per whole archive (UBC = CC0, WTU = CC-BY), stated in that institution's EML | **Per-record** license (`license`, `rightsHolder`, `publisher` DwC fields) — a GBIF download can mix CC0/CC-BY/CC-BY-NC rows from different constituent providers in one file |
| Terms authority | Institutional release-bound EML | GBIF's Data User Agreement / Download API terms, plus each constituent record's own license field |
| Access shape | Direct exact-URL archive fetch (`data.canadensys.net`, `ipt.pnwherbaria.org`) | Request-then-poll-then-download (a GBIF download key is requested, then a final file URL is issued once the export is built) — see the fetch-shape gate this track leaves open below |

## Why this matters for `fetch.py`'s exact-URL model

`pipeline/direct/botanical_occurrences/fetch.py`'s `permission_granted()` is
exact-URL match only, and `_RefusingRedirectHandler` refuses every redirect
outright (per the seeded architecture finding). The existing UBC/WTU track's
manifest model (one exact archive URL, one EML-stated license, one
`permission_granted` entry) fits an institutional IPT resource because that
URL is stable at acquisition time. GBIF's Download API does not hand out a
stable exact URL until an operator-initiated request finishes building the
export — this track's [`admission-decisions.json`](admission-decisions.json)
records that as an **open pre-acquisition gate**, not something this lane
(governance/evidence only, no code, no live fetch) can resolve. Lane 1's plan
is the authority on whether the manifest model needs to change shape to
accommodate this, or whether the same "operator runs the acquisition by hand,
hands the pipeline an exact final URL" pattern UBC already uses is sufficient
unchanged. This note exists so that decision is not made twice, once here and
once in Lane 1's plan, without either side citing the other.

## Scope this track owns

Metadata and source-governance evidence only, exactly like its sibling.
No code, no live network fetch, no acquisition, no institutional outreach.
`conductor/tracks/gbif_source_admission_20260914/**` only.
