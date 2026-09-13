---
type: source-governance-audit
status: proposal-pending-owner-signoff
captured_on: 2026-09-13
---

# Custody and archive-control preflight: a proposal, not a decision

## What this is and is not

The [source-governance audit](source-governance-audit-20260912.md) and the
[admission packet](admission-packet.md) both name "reviewed custody and
archive-control preflight" as a pre-acquisition gate for WTU and UBC, and
both say this track "supplies no completed custodian appointment, retention
duration/trigger or approved disposal decision." That is still true after
this receipt. Appointing a custody owner and approving a retention policy
are operator decisions, not something a research or implementation pass can
elect on its own. What follows is a concrete proposal built from controls
already implemented and tested in
[`pipeline/direct/botanical_occurrences/`](../../../../services/agri-data-service/src/agri_data_service/pipeline/direct/botanical_occurrences/),
so the operator has a specific document to approve, amend, or reject rather
than an open-ended question.

## Proposed custody owner

**Not named here.** The audit is explicit that this is an operator
appointment. The proposal below is written so any named owner can approve it
as-is; the "owner" field in the manifest schema below is a placeholder an
operator fills in before the first transfer.

## Proposed transfer manifest and controls (implemented, tested)

These are not aspirational; they are the actual behavior of
[`fetch.py`](../../../../services/agri-data-service/src/agri_data_service/pipeline/direct/botanical_occurrences/fetch.py)
and
[`quarantine.py`](../../../../services/agri-data-service/src/agri_data_service/pipeline/direct/botanical_occurrences/quarantine.py),
covered by
[`tests/direct/botanical_occurrences/`](../../../../services/agri-data-service/tests/direct/botanical_occurrences/):

| Control | Implementation | Evidence |
| --- | --- | --- |
| Allowlisted transfer hosts | `ipt.pnwherbaria.org`, `data.canadensys.net`, `www.pnwherbaria.org` only; any other host raises `TransferRefusedError` before a socket opens | `fetch.py::ALLOWED_HOSTS` |
| HTTPS only | non-HTTPS scheme refused before transfer | `fetch.py::fetch_archive` |
| Redirect refusal | every redirect raises `RedirectRefusedError`; no automatic re-request | `fetch.py::_RefusingRedirectHandler` |
| Explicit permission gate | a transfer proceeds only when a manifest names the exact URL with `permission_verdict: "granted"`; collection-level or prefix grants do not satisfy this | `fetch.py::permission_granted` |
| Per-archive/total byte caps | 64 MiB compressed per archive, 128 MiB compressed total, 2 GiB decompressed total, enforced mid-stream, not just after the fact | `foundation/botanical_occurrences/limits.py::ADMITTED_LIMITS`, `fetch.py`'s mid-transfer cap check |
| Attempt/time budget | 8 HTTP attempts, 30s/request, 600s wall deadline | same `ADMITTED_LIMITS`, `fetch.py::fetch_archive` |
| No credential exposure | `Authorization`, `Proxy-Authorization`, `Set-Cookie` headers are redacted before any receipt is written | `fetch.py::_safe_headers` |
| Archive-safety controls, exhaustive | path traversal, absolute paths, drive letters, UNC paths, symlinks, encrypted members, nested archives, member-count cap, per-member and aggregate compression-ratio caps, CRC verification, casefold/NFC name-collision detection, required-member presence, all collected rather than stopping at the first hit | `quarantine.py::inspect_archive`, `tests/direct/botanical_occurrences/test_quarantine.py` (one test per control) |
| No extraction to disk | every member is streamed through `ZipFile.open` in chunks; nothing is ever written to a filesystem path derived from an archive member name | `quarantine.py` module docstring: "NOTHING IS EXTRACTED TO DISK" |
| XXE/DTD refusal | `meta.xml`/`eml.xml` bytes are scanned for DOCTYPE/entity constructs before any parser sees them, independent of whether `defusedxml` is installed | `archive_descriptor.py::_FORBIDDEN_XML_PATTERNS` |
| No automatic scheduling | nothing calls `fetch_archive` except the operator-invoked CLI (`python -m agri_data_service.pipeline.direct.botanical_occurrences fetch`); no cron, no lane registration triggers a transfer | `fetch.py` module docstring; `pipeline/parquet/lane_registry.py`'s `botanical_occurrences` entry is a refusal adapter only (see [shared-registration.patch](../../botanical_occurrence_parquet_lane_20260911/evidence/shared-registration.patch)) |

## What the operator still needs to decide

1. **Custody owner.** Who is accountable for the quarantine directory's
   access control, and who approves each transfer's permission manifest
   before the CLI is run.
2. **Quarantine location.** A path outside Git and outside any public
   bucket, access-restricted to the named custody owner. This proposal does
   not name a path; the implementation accepts any local directory or `s3://`
   target via `--destination`/`publication_target`, so the choice is
   deployment-specific.
3. **Retention duration and trigger.** How long quarantined bytes are kept
   after a collection is admitted or refused, and what event (admission,
   refusal, a fixed calendar period) starts the clock. The audit
   specifically warns against "indefinite raw retention" as a default.
4. **Withdrawal/deletion record.** What gets written down when quarantined
   bytes are deleted, so a later question about what was held and for how
   long has an answer.
5. **The permission manifest's actual content.** `permission_granted()`
   checks a manifest's shape; it does not and cannot check that a grant was
   real. The manifest is only as trustworthy as the record of who granted
   it and on what authority, which point 1 in the WTU/UBC outreach
   ([wtu-gbif-identity-correction-20260913.md](wtu-gbif-identity-correction-20260913.md),
   [requests-and-handoff.md](requests-and-handoff.md)) is what would supply.

None of these five are things this receipt elects. They are the specific,
bounded remaining decisions once this proposal's technical controls are
accepted or amended.

## Status

This does not admit anything. `admitted_releases` remains empty in
[admission-decisions.json](admission-decisions.json). No archive has been
transferred; no operator has appointed a custody owner or approved a
retention policy. This receipt narrows the "custody and archive-control
preflight" gate from an open question to five specific decisions plus a
concrete, already-implemented control set an operator can approve as one
piece rather than several.
