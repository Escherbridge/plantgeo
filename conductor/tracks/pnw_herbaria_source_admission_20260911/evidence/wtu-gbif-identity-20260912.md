---
type: source-metadata-receipt
status: partial-gate-closure
captured_on: 2026-09-12
---

# WTU vascular: GBIF registry identity and two-release EML comparison

## Outcome

This capture closes part of WTU's blocking gate from
[the September 12 metadata refresh](metadata-refresh-20260912.md) and
[the source-governance audit](source-governance-audit-20260912.md): **standalone,
release-bound EML now exists for two complete WTU vascular releases**, bound to
a stable GBIF dataset UUID. WTU is **still blocked** — this pass measured EML
and identity only. It did not touch `meta.xml`, the archive, or any occurrence
row, and it requested no institution, agreement, or credential.

Twelve bounded, allowlisted, metadata-only GETs completed under the inherited
eight-attempt/30-second/600-second/1 MiB ceilings, using a hardened capture
recipe that **refuses redirects** (`RefuseRedirects` raises rather than
following) so every recorded endpoint is provably zero-hop, closing the
transport-inference gap the audit flagged in the September 12 refresh recipe.
No specimen archive, image, or non-metadata content type was requested; no
provider was contacted; no data-use agreement was accepted.

## New retained evidence

Bounded capture scripts (reusable, redirect-refusing, allowlisted to
`api.gbif.org`, `ipt.pnwherbaria.org`, `www.pnwherbaria.org`,
`data.canadensys.net`):
[`pnw-admission-gbif-registry-20260912.py`](../../../../.omc/research/pnw-admission-gbif-registry-20260912.py),
[`pnw-admission-gbif-wtu-20260912.py`](../../../../.omc/research/pnw-admission-gbif-wtu-20260912.py),
[`pnw-admission-bounded-capture.py`](../../../../.omc/research/pnw-admission-bounded-capture.py)
(generic allowlisted single-shot fetcher used for the IPT resource/EML pulls
below). Receipts:
[`pnw-admission-gbif-registry-http-receipts-20260912.json`](../../../../.omc/research/pnw-admission-gbif-registry-http-receipts-20260912.json),
[`pnw-admission-gbif-wtu-http-receipts-20260912.json`](../../../../.omc/research/pnw-admission-gbif-wtu-http-receipts-20260912.json),
[`pnw-admission-ipt-wtu-20260912-http-receipts.json`](../../../../.omc/research/pnw-admission-ipt-wtu-20260912-http-receipts.json),
[`pnw-admission-ipt-wtu-versions-20260912-http-receipts.json`](../../../../.omc/research/pnw-admission-ipt-wtu-versions-20260912-http-receipts.json).

| Name | Retrieved endpoint | Bytes | SHA-256 |
| --- | --- | ---: | --- |
| GBIF dataset search "WTU" | `api.gbif.org/v1/dataset/search?...` | 23,388 | `2e2b7342…6b4aa91` |
| GBIF WTU dataset record | `api.gbif.org/v1/dataset/8310f570-f762-11e1-a439-00145eb45e9a` | 5,948 | `e094a1cf…4ba3d7` |
| GBIF WTU endpoints | `.../dataset/8310f570…/endpoint` | 595 | `e12accc4…877ccea` |
| GBIF WTU installation | `api.gbif.org/v1/installation/240e89bc-4985-4468-aa2f-aaba905a8be4` | 1,236 | `b1bb2189…9a023f5` |
| WTU IPT resource, current | `ipt.pnwherbaria.org/resource?r=wtuvascular` | 38,343 | `4a07c777…9395eaaef` |
| WTU EML, current (v1.1) | `ipt.pnwherbaria.org/eml.do?r=wtuvascular` | 5,407 | `d38a2b1b…3484604923d6` |
| WTU EML v1.0 | `ipt.pnwherbaria.org/eml.do?r=wtuvascular&v=1.0` | 5,219 | `443cda91…029de0f55af` |
| WTU EML v1.1 | `ipt.pnwherbaria.org/eml.do?r=wtuvascular&v=1.1` | 5,407 | `d38a2b1b…3484604923d6` |
| WTU IPT resource, v1.0 | `ipt.pnwherbaria.org/resource?r=wtuvascular&v=1.0` | 37,219 | `a230d98d…3de4150fdf8` |

The current EML pull and the explicit `&v=1.1` pull are byte-identical
(`cmp` confirmed), so "current" and "v1.1" are the same immutable body.

## Measured facts

**Distributor moved.** WTU vascular specimens are now served from
`ipt.pnwherbaria.org` (a dedicated IPT install for the Consortium of Pacific
Northwest Herbaria), not only the `pnwherbaria.org/data/getdataset.php` portal
export this track originally scoped. GBIF's registry independently confirms
this as the authoritative distribution: dataset key
`8310f570-f762-11e1-a439-00145eb45e9a`, DOI `10.15468/plngb6`, endpoints
`DWC_ARCHIVE https://ipt.pnwherbaria.org/archive.do?r=wtuvascular` and
`EML https://ipt.pnwherbaria.org/eml.do?r=wtuvascular`. GBIF's registry
metadata is a third-party corroboration of identity and licence, not a
substitute for inspecting the actual EML/archive itself, which this pass did.

**Two complete, equivalent, release-bound EML documents now exist**, satisfying
the audit's "equivalent prior release" requirement in principle for EML (not
yet for the archive):

| Version | `packageId` | `pubDate` | Records (IPT versions table) | Licence |
| --- | --- | --- | --- | --- |
| v1.0 | `https://ipt.pnwherbaria.org/resource?id=wtuvascular/v1.0` | 2025-11-06 | 262,626 | CC-BY 4.0 (`intellectualRights` ulink) |
| v1.1 (current) | `8310f570-f762-11e1-a439-00145eb45e9a/v1.1` | 2026-03-12 | 265,729 | CC-BY 4.0 (`intellectualRights` ulink); `<dc:replaces>8310f570…/v1.1.xml` |

v1.0's `packageId` is a mutable-looking resource URL rather than the
UUID/version form v1.1 uses; this is the source's own inconsistency and is
recorded, not corrected. The IPT versions table (rows extracted from the
`aDataSet` JS array in the resource page) additionally records a
human-readable change summary for v1.1: "Additional specimen records and
images," and for v1.0: "Migrated from University of Washington Herbarium Burke
Museum endpoint." Both entries name David Giblin as the version author. GBIF's
registry independently reports 265,729 records and `MONTHLY` update frequency,
consistent with the current IPT page.

**Licence changed from the original portal record.** The `pnwherbaria.org`
portal provider page (recorded in the September 11/12 refresh) labels WTU
vascular specimen facts CC0. Both captured **release-bound** EML documents
(v1.0 and v1.1) instead declare **CC-BY 4.0** under `intellectualRights`, and
GBIF's dataset registry record agrees: `license` =
`http://creativecommons.org/licenses/by/4.0/legalcode`. This is a materially
different obligation (attribution required) than the portal's CC0 label. The
draft outreach message in
[requests-and-handoff.md](requests-and-handoff.md) still says "Your provider
page labels specimen facts CC0" — that sentence is now contradicted by the
release-bound EML and must be corrected before any message is sent, not
reused as drafted.

**Geographic coverage in the EML is an unbounded placeholder**, not a usable
envelope: both v1.0 and v1.1 give `westBoundingCoordinate=-180`,
`east=180`, `north=90`, `south=-90` (the entire globe). This cannot serve as
the "documented coverage envelope" the occurrence plane's support-evaluation
artifact needs; a real envelope, if one exists, is not in this metadata and
must come from measured record extents after a permitted capture, not from
this EML field.

**No coordinate-withholding or sensitive-locality statement appears in either
WTU EML.** Neither v1.0 nor v1.1 contains any of `sensitive`, `withh`,
`generali[sz]`, `obscur`, or `coordinateUncertainty` in a case-insensitive
search of the raw XML. This differs from the portal's separate
`datausagepolicy.php` text (captured 2026-09-12), which says public exports
"omit sensitive localities" — that statement governs the older portal
distribution, and there is still no evidence it governs the `ipt.pnwherbaria.org`
distribution GBIF now points to.

## Gate status update

WTU's pre-acquisition gates from
[admission-decisions.json](admission-decisions.json) were: (1) exact collection
EML and release identity evidence, (2) public-coordinate/terms binding to the
exact export, (3) reviewed custody/archive-control preflight.

- Gate 1 (EML + release identity): **now measured for two releases.** Stable
  GBIF UUID `8310f570-f762-11e1-a439-00145eb45e9a`, two dated `packageId`
  values, DOI, and a human-readable version-to-version change summary. This
  is real progress but does not by itself admit the collection: `meta.xml`,
  archive hashes, and field mapping remain unmeasured (post-capture gates,
  unchanged).
- Gate 2 (coordinate policy): **still open**, and now sharper: the CC0-vs-CC-BY
  discrepancy means any outreach must ask about redistribution/attribution
  terms under CC-BY, not the CC0 assumption in the existing draft. No
  coordinate-withholding statement is bound to either measured EML.
  Distribution has also moved to `ipt.pnwherbaria.org`; any custody/allowlist
  decision must name that host, not only the `pnwherbaria.org` portal domain.
- Gate 3 (custody/archive-control preflight): unchanged, still open.

WTU remains **blocked**. `admitted_releases` remains empty. This receipt
does not authorize a WTU or UBC archive fetch; the two-archive,
one-transfer-at-a-time budget from
[admission-decisions.json](admission-decisions.json) remains entirely unused
and unwidened. The next gate is unchanged in kind (release-bound coordinate
policy for the now-correctly-identified distribution, plus custody/archive
controls) but the EML sub-gate that blocked it is measurably closer.

## Correction owed to existing evidence

[requests-and-handoff.md](requests-and-handoff.md)'s WTU draft message asserts
a CC0 label and asks the provider to "confirm the applicable terms" — that
premise is now known to be wrong for the release-bound EML (CC-BY 4.0) and the
distribution host has moved from `pnwherbaria.org` to `ipt.pnwherbaria.org`.
The draft is unsent and remains a draft only; this receipt flags it for
revision rather than rewriting the file, since draft correction is
`requests-and-handoff.md`'s own concern and out of scope for one metadata
receipt.

Independent verification is requested for the raw-body hashes, the
zero-redirect claim, the CC0→CC-BY discrepancy, and the unchanged
`admitted_releases: []` state. This author receipt is not an admission
verdict.
