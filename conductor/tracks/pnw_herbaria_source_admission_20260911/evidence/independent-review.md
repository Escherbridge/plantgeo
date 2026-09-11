---
type: independent-review
status: blocked
reviewed_on: 2026-09-11
review_scope: metadata-and-source-admission
---

# Independent source-admission verdict

**WTU vascular: BLOCKED. UBC vascular: BLOCKED. No exact occurrence release is
admitted, and this review does not authorize an archive transfer.** The packet is
suitable as a bounded source investigation and downstream blocker record. It is
not a completed archive/schema/identity pilot or a completed source-admission track.

This review was performed in a separate agent context from the author. It covers
[metadata](../metadata.json), [specification](../spec.md), [plan](../plan.md),
[admission packet](admission-packet.md), [machine-readable decisions](admission-decisions.json),
[requests and handoff](requests-and-handoff.md), and [source register](source-register.md).
The reviewer read the immutable viability note from commit
`35624ddbaf93670b839333ae51d7ef15e1f5a85e`, compared material source claims with
retained primary HTML/XML and policy extracts, and checked the historical species
census against the current model. No network fetch, corpus acquisition, image
download, institution contact, database query or final verification sweep was
performed by this reviewer.

## Findings and disposition

| Finding | Disposition |
| --- | --- |
| A2 metadata originally claimed future runtime ingest/test directory ownership despite the evidence-only execution boundary. | Author corrected ownership to a future archive/schema/identity evidence receipt within this track; reviewer confirmed the correction. |
| The metadata capture recipe uses default HTTP redirect handling; receipts retain requested/final URLs rather than every intermediate redirect. | Author corrected the source register to describe the recorded URLs and explicitly disclose missing intermediate history; reviewer confirmed the correction. The future archive protocol must validate each redirect before following it. |
| Git's inherited text normalization could alter the four exact HTML/XML captures when staged, invalidating their HTTP receipt hashes. | Author added four exact-filename `-text` entries in the research directory attributes and a staged-blob SHA comparison against each HTTP receipt. Reviewer inspected the bounded attributes and verification recipe; execution remains part of the parent's final sweep. |

The second finding limits retrospective transport claims; it does not weaken the
decision to keep all specimen releases blocked. Four retained successful direct
captures total 186,764 bytes according to their receipts. Initial and final
recorded URLs are metadata endpoints. These facts do not independently prove an
entire network history, and metadata SHA values do not certify occurrence bytes.

The byte-preservation correction is appropriate: only the four source captures
are exempted from Git text normalization, while JSON receipt formatting may use
LF without changing metadata values. The verification recipe compares the raw
bytes of each staged Git blob with its recorded source SHA, supplementing the
working-tree length/hash checks. The reviewer did not rerun these checks or
certify their result; a passing parent receipt is still required before commit.

## Evidence assessment

- WTU's captured vascular section identifies the portal DwCA route, separates CC0
  specimen facts from image terms, and supplies the stated collection/index
  counts and update timestamps. Portal inventory supplies the smaller public
  export count. The packet correctly leaves count differences unexplained and
  does not convert them into measured withdrawals or withheld-record counts.
- UBC's captured IPT HTML supports v16.43 and v16.42 as candidate publisher
  versions with the advertised dates and counts. Standalone v16.43 EML supports
  the package identifier, UBC collection identifiers, publication day, dataset
  citation and CC0 declaration including commercial reuse. These are source
  metadata assertions, not inspected archive/schema/record-level rights results.
- The portal inventory explicitly describes omission of sensitive localities
  and nonpublic records. No matching institutional-coordinate policy was found
  in the reviewed UBC EML. Transferring the portal's suppression guarantee to the
  institutional IPT route would be unsupported; the packet correctly refuses it.
- No applicable signed digital-data agreement requirement was established. The
  retained physical-loan policies and unrelated institutions' restrictions do
  not establish such a requirement for these public WTU/UBC facts. This is not a
  legal conclusion that no additional obligation could apply. The exact proposed
  requests and operator checklist are usable drafts and must remain unsent
  without authorization to contact the institutions.
- Darwin Core field names are explicitly proposed inspection targets. Portal
  format documentation is distinguished from actual IPT or ZIP member mappings.
  Archive sizes, hashes, safety, row populations, native identity stability and
  corrections remain unmeasured; the decision JSON uses nulls rather than zeros.

## Gate and custody assessment

WTU needs release-bound EML/terms, public-population identity and stable release
evidence before the permitted quarantine stage can begin. UBC needs its chosen
institutional distribution's public-coordinate/withholding applicability resolved.
Both require an approved custody owner, retention/access policy, allowlisted
transfer plan and enforceable archive controls. Report the pre-acquisition verdict
to the parent before transferring any specimen bytes.

After those gates pass, collection of permitted bytes into quarantine can measure
the archive checks that metadata cannot establish. Neither a successful download
nor a published version number completes admission. Whole-member safety, archive
and member hashes, EML/record-rights agreement, schema/population reconciliation
and independent two-release identity review remain necessary. A v16.43/v16.42 UBC
comparison consumes both archive slots in the inherited two-archive envelope;
WTU must remain deferred unless scope is separately revised.

The packet provides appropriate fail-closed controls for path traversal, member
collisions, XML external resolution, unexpected contents, byte/row/time ceilings,
restricted coordinates and rights conflicts. These are proposed controls, not
tested archive-processing implementation. Candidate source removals require
complete-equivalent comparison; partial exports and exclusions cannot author
deletion tombstones. Withdrawal must also suppress derived generations and
prevent rollback from restoring a withdrawn locality.

## User pivot and downstream boundaries

The latest conditional data-only acquisition authorization is reflected without
silently converting it into unconditional transfer permission. Images,
publication, scheduling, API/UI work and production mutation remain excluded.
The occurrence handoff contains no admitted release and cannot authorize
downstream ingestion yet. Spatial occurrences, support, aggregates and
environmental joins remain in the governed Parquet plane.

The species-profile handoff correctly treats the August 14 empty `agri.species`
census as historical. The current Species model has trait fields but lacks
per-value provenance/review and canonical authority-version identity; companion
relationship evidence columns do not supply those guarantees for traits. The
packet correctly warns against treating Boolean defaults as evidence of a
negative trait. It does not claim to have queried current production state.

Parent ownership of the profile track is preserved. Draft/reviewed curation may
use a reviewed database surface, with a single approval/export transition to an
immutable canonical-taxon-keyed profile Parquet release. Serving must pin that
release and refuse a missing release rather than silently query live drafts.
Per-value provenance, licence, review state, dates and lineage remain mandatory.
Deferred enrichment from a second trait source cannot silently overwrite stronger
evidence or block an otherwise admissible first pilot. Suitability traits remain
separate from species-objective effect evidence.

## Verification limits

This verdict evaluates source assertions and governance logic using the retained
captures. It does not certify external pages beyond those captures, perform a
full EML XSD validation, inspect archive bytes, prove absent sensitive data, or
measure identifier stability. The parent must finish the single integrated
documentation/JSON/local-link/hash/whitespace sweep and record its receipt after
all author corrections. Full application tests are outside this documentation
review. Keep the track blocked and `admitted_releases` empty until the source
and archive gates receive a new independent verdict.
