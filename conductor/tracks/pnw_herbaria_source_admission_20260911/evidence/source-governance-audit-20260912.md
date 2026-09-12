---
type: source-governance-audit
audited_on: 2026-09-12
status: blocked
review_status: pending-independent-review
baseline_commit: 64f4f892bd2b744cc097c7f76a1f239997b80f52
baseline_tree: f8697da694a3f9fbbf28e70ff7581bd59546bfd6
---

# PNW Herbaria retained-evidence governance audit

WTU vascular and UBC vascular remain blocked; no occurrence release is admitted.
This is an offline audit of the local main baseline above in an isolated
worktree, not a new source observation or a permission verdict. The only authored
artifact is this receipt. No specimen, archive, image, media or new bulk data was
acquired; no network request, provider contact, service, database, storage,
writer, scheduler, API, UI or deployment operation was performed.

## Evidence examined and authority

The current [plan](../plan.md), [specification](../spec.md) and
[metadata](../metadata.json) consistently distinguish an active governance track
from blocked admissions and an empty `admitted_releases`. Completed A0/metadata
milestones do not complete A1 archive profiling or A2 identity/admission work.
The metadata's `next_gate` is a summary, not a waiver of the detailed controls.

This audit reconciles the [admission packet](admission-packet.md),
[collection decisions](admission-decisions.json), [source register](source-register.md),
[unsent requests and handoff](requests-and-handoff.md),
[September 12 metadata refresh](metadata-refresh-20260912.md),
[independent admission review](independent-review.md),
[baseline synchronization](baseline-synchronization.md) and its
[review](synchronization-review.md). The original [verification](verification.json)
and [synchronization verification](synchronization-verification.json) concern
their historical inputs; their passing statuses do not certify this successor.
The synchronization's byte-preservation statements describe that merge, not a
claim that subsequently refreshed documents still equal the original packet.

Retained direct evidence consists of the September 11
[three HTML receipts](../../../../.omc/research/pnw-admission-http-receipts-20260911.json)
and [EML receipt](../../../../.omc/research/pnw-admission-eml-http-receipt-20260911.json),
plus September 12's [seven-response receipt](../../../../.omc/research/pnw-admission-metadata-refresh-http-receipts-20260912.json)
and their local bodies. Their hashes identify captured metadata only. Advertised
counts, sizes and publication dates remain publisher assertions, not measured
archive populations. No external link was reopened for this audit.

## Exact collection gaps

| Collection | Retained evidence | Unresolved gate and sequencing |
| --- | --- | --- |
| WTU vascular, local key `pnw:WTU:vascular` | The retained provider vascular section labels specimen facts CC0 separately from images; the portal advertises 267,636 public records, 52.9 MB and 2026-06-04. Provider collection/index counts are 268,851/268,342. The route is the mutable `WTU_Vascular_DwCA.zip` filename. | No retained standalone release-bound EML, actual `meta.xml`, immutable publisher release binding or equivalent prior complete release. The exact terms, complete public population and coordinate policy must be bound to the chosen export before capture; generic CC0 HTML does not close that preflight. |
| UBC vascular, local key `pnw:UBC:vascular` | Institutional resource `ubc-vascular-specimens`, UUID `07fd0d79-4883-435f-bba1-58fef110cd13`; standalone v16.43 EML declares CC0 and publication day 2026-09-01. Institutional HTML advertises v16.43: 192,948 records/31 MB; v16.42: 192,783 records. | No retained authoritative withholding/generalization statement applicable to the chosen institutional IPT distribution. No actual archive field map, complete population, archive-rights agreement or native-ID stability measurement for either version. Both comparison releases must satisfy the same rights/public-population contract. |

WTU's EML gap and field-map gap must not be collapsed into a circular requirement
to inspect an archive before permission to capture it. The decisions JSON puts
exact EML/release/rights evidence in pre-acquisition and actual field-map/count
measurement in post-capture. The WTU draft requests standalone EML and `meta.xml`;
none is retained. Obtain release-bound EML/terms and release evidence without
specimen transfer under a separately permitted metadata/clarification step.
An authoritative standalone field map could inform that step, but cannot certify
the future archive. If release-bound preflight evidence remains unavailable,
WTU stays blocked; this receipt does not authorize downloading to discover terms.
After a permitted capture, measure and hash the actual `meta.xml` and reconcile
it with EML, members and rows. Portal format documentation is not that measurement.

The retained [portal inventory](../../../../.omc/research/pnw-portal-inventory-20260912.html)
says sensitive localities are omitted and nonpublic records excluded. That is a
portal-distribution assertion. It cannot establish the broader institutional
UBC IPT export's sensitivity contract. The required UBC evidence must identify
which distribution/releases it governs, what is suppressed or generalized, how
withholding is signaled, and the precision suitable for intended redistribution.
Residual free-text locality and record-level rights risks remain review subjects.
Do not recover withheld coordinates through other sources, media or inference.
Absence of a policy in retained evidence is not proof that no institutional
policy exists or that the uninspected archive contains sensitive coordinates.

UBC's [retained v16.43 EML](../../../../.omc/research/pnw-ubc-eml-16.43-20260912.xml)
has package `07fd0d79-4883-435f-bba1-58fef110cd13/v16.43`; both dated HTTP
receipts record 14,208 bytes and SHA-256
`e73735e4eafcb1a233514948f72631ceced3dd22e30445d45f34e593f7293654`.
This establishes a repeatable metadata identity in those receipts, not immutable
archive custody. No v16.42 EML or archive has been measured in this packet.
Do not substitute the older UBC portal copy (191,189 advertised records,
2026-06-04) for an equivalent institutional comparison or union both as new
specimens. WTU count differences are unreconciled, not measured withholding or
deletion counts.

## Immutable identity, quarantine and withdrawal

The release contract remains prospective: bind distributor, collection,
publisher version/publication evidence, complete-public-population scope,
archive SHA-256/bytes, ordered member hashes/bytes/CRC, EML and `meta.xml` hashes,
parser recipe/version, policy snapshots, transport receipt and reviewer verdict.
Neither mutable filename, version URL, HTTP validator nor retrieval time proves
immutable archive bytes. Content identity does not establish native occurrence
identity. Both archives' hashes, member/profile receipts and byte counts remain
null in the decisions JSON, and native-ID stability remains unmeasured.

Before collection, appoint a custody owner and approve an actual access/retention
policy, immutable-manifest procedure and deletion/withdrawal record. The retained
packet requires these controls but supplies no completed custodian appointment,
retention duration/trigger or approved disposal decision. Do not invent a period
or treat indefinite raw retention as permitted. Any authority clarification must
cover raw custody, transformations, intended redistribution, attribution and
permitted audit retention after termination. No applicable signed digital-data
agreement requirement has been established by the retained packet; unsent drafts
are neither grants nor accepted terms. Physical-loan and image terms are not
substitutes for this dataset's permissions.

Quarantine must be access-restricted, outside Git and public buckets, with
allowlisted URLs and each redirect validated before following it. No authenticated
fallback, member execution, embedded URL following or media fetching is allowed.
Required controls reject traversal/absolute/drive/UNC paths, symlinks, normalized
or case-colliding duplicates, encrypted/nested archives, CRC/hash mismatch,
size/ratio anomalies and unsupported executable/schema content. Disable XML
external entities/network resolution. Enforce streaming byte, row and time caps;
missing or conflicting rights/schema keep the release quarantined. These are
specified controls, not implemented or tested safety evidence in this audit.

Corrections require new immutable revisions. Source shrink is only a candidate
withdrawal after two equivalent complete exports or an explicit publisher
correction; partial exports, timeouts, schema failures and changed rights filters
must not generate deletion tombstones. Rights/sensitivity withdrawal must block
affected serving generations and derived summaries, rebuild affected artifacts
and caches, verify suppression and permit rollback only to a still-authorized
generation. Retain only audit evidence allowed by the reviewed retention
decision. These future procedures have not been exercised; there is no serving
release from this track to certify or withdraw today.

## Retrospective transport limitation

The September 12 refresh and source-register addendum infer no redirect hop from
equal requested/final URLs. That inference is stronger than the stored evidence:
the retained [refresh recipe](../../../../.omc/research/pnw-admission-metadata-refresh-20260912.py)
uses default `urllib.request.urlopen` redirect handling and records no per-hop
history. Equal endpoints cannot exclude intermediate redirects returning to the
same URL. Read those claims as **no redirect visible in the recorded endpoints**,
not proof of zero hops or allowlist enforcement throughout transport. This is
the same evidence limitation disclosed for September 11; the historical captures
are preserved unchanged. The recipe also checks a 180-second session budget
between requests and uses a 30-second request timeout; this is not an executed
proof of a hard streaming/decompression deadline for future archives.

## Next gate for the bounded data-only pilot

1. Retain authoritative release-bound evidence closing WTU's EML/terms/public
   population/identity gap or UBC's institutional coordinate-policy gap, for
   every release actually selected. Identify an equivalent prior release for
   native-ID comparison; WTU's remains unknown and UBC v16.42 is only a candidate.
2. Approve the concrete custody owner, access/retention/disposal policy,
   attribution, allowlisted transfer manifest and enforceable archive controls.
   Report the pre-acquisition permission verdict to the parent before any
   transfer. Prior conditional owner authorization does not close these gates;
   this evidence-only task authorizes no acquisition or provider contact.
3. Only a subsequent permitted quarantine pilot may measure exact bytes,
   member safety, EML/record-rights agreement, field mapping and population
   reconciliation. Compare native-ID continuity, reuse, missing/duplicate keys
   and corrections across two complete equivalent releases. A diagnostic sample
   cannot certify completeness or identity stability.
4. Obtain an independent exact-release admission/refusal verdict and allowed-use
   manifest before occurrence handoff. Downstream implementation, publication,
   scheduling, API/UI and production require their separate gates. Occurrence
   evidence remains distinct from nonspatial traits and objective-effect evidence.

The inherited envelope is unchanged: two archives, one transfer at a time,
64 MiB compressed per archive, 128 MiB compressed total, 2 GiB decompressed total,
32 members, 600,000 core rows, two million extension rows, eight HTTP attempts
including redirects/retries, 30 seconds/request and ten minutes wall time.
Overruns defer/fail; advertised MB are not measured MiB. A two-release UBC pilot
uses both archive slots and defers WTU. Neither slot has been used according to
the retained decisions/refresh; this task used none. No third archive or widened
budget is implied.

## Verification and review boundary

The final offline sweep for this one-file change checks the staged diff and
whitespace, OKF `type` frontmatter, track JSON consistency and local Markdown
file targets. It also compares the eleven retained direct metadata bodies with
their receipt lengths/SHA-256 in the working tree and baseline Git blobs.
External URLs are not probed. Results and the immutable final commit/tree are
reported by the task after that sweep; this receipt does not predeclare a pass.
Application tests and runtime/archive certification are outside this change.
Independent review before integration remains pending; this author audit grants
no admission, transfer or integration approval.
