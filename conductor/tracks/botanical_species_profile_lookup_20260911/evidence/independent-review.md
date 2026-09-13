---
type: independent-review
track: botanical_species_profile_lookup_20260911
reviewer: local-independent-code-api-review
status: approved-for-bounded-local-integration
reviewed_at: "2026-09-12T00:53:29Z"
---

> **Carried forward from `codex/botanical-species-profile-lookup` commit `edc6afd`, 2026-09-12.**
> Everything below, including the `6,086 passed` figure, describes THAT branch's own tree at
> that commit, not the current `claude/herbaria-botanical-lanes` port. This port's own sweep on
> the merged tree is reported separately in
> [port-notes-20260912.md](port-notes-20260912.md); it found 4,566 of 4,603 relevant backend
> tests passing, with the remainder gated on pending shared-file registrations (see
> `shared-registration-20260912.patch`), not run against this content.

# Independent local code and API review

This review covers the bounded local implementation on
`codex/botanical-species-profile-lookup`, based on
`bc7b5e1ff5dbb6eac929d5db927acd6b7ff2ea4a`. The reviewer did not author the
implementation, source descriptor, WCVP candidate or integration receipt.

**Verdict: approve the bounded local code, API and agent implementation for commit
and integration.** All five identified defects are resolved, and the final
verification receipts describe the independently recomputed service tree. No
material code finding remains open. This document grants no source, scientific,
production-data or deployment acceptance.

The latest instruction after the host shutdown restricts this continuation to
local work. It supersedes earlier permission to attempt a Railway census. This
review opened no private environment file, contacted no remote service, ingested
no source, queried no database and ran no tests. Production authoring census and
source admission remain pending. Existing production row counts, review states,
provenance and licence completeness are unknown. The preserved WCVP bytes remain
an **unaccepted local fixture/candidate**.

## Scope and method

The reviewer read the staged and unstaged implementation and its tests, including:

- Warehouse identity, assertion and source models; canonical validation;
  correction/withdrawal reconciliation; separate growth, fuel, fire-response,
  agricultural-role, companion and objective-effect sections.
- Native Arrow schemas, deterministic input-bound release identity, all five
  required artifacts, bounded decoding, checksum and reconciliation verification,
  immutable writes, conditional pointer movement, replay and rollback.
- Local storage path validation and atomic writes, and the offline source adapter
  and CLI. Reading that code did not execute ingestion or publication.
- The pinned reader, `create_app` HTTP registration, query validation, evidence
  continuation, response and time budgets, and typed refusal behavior.
- `species_information`, `WAREHOUSE_TOOLS`, Anthropic tool objects, OpenAI schema
  and execution dispatch, model instructions, graph ledger/sufficiency behavior,
  and MCP `tools/list` and `tools/call` dispatch.
- Local synthetic regression tests, Conductor scope and the earlier findings.
  Test execution and its final receipt belong to the integration owner.

Installed Sanic source was inspected to verify default HTTP JSON encoding;
no network documentation fetch was used. This is an engineering and evidence
contract review, not a claim of professional botanist credentials or validation
of planting effects.

## Finding dispositions

| Finding | Current disposition and inspected evidence |
| --- | --- |
| Canonical identity documentation differed from the API identity | Source evidence identifies `authority=WCVP`, `authority_version=16` and the string `plant_name_id`. The descriptive source-release label remains separate. |
| An approved correction to missingness left the old value serving | Fixed in `_decide`: approved corrections suppress the retained original regardless of replacement eligibility. Tests cover unknown/restricted replacements and unapproved corrections. |
| Canonical IDs and same-source assertions could borrow another admitted taxon's source record | Fixed in inventory/assertion validation: canonical IDs equal authority source-record IDs, and same-source/version assertions bind to the canonical record. Regressions cover relabeling and borrowing. |
| Unknown-taxon responses bypassed the response ceiling | Fixed: unknown and published results share `_bounded_response`. A regression checks refusal of an oversized unknown manifest. |
| HTTP serialization could exceed the measured UTF-8 response ceiling | Fixed and inspected: one compact UTF-8 encoder supplies the measured ceiling, exact HTTP body and agent payload. All four registered-route cases pass: Unicode exactly at and above 2 MiB for both read-serving application profiles. |

No other material code defect was identified in this pass. The exact reviewed
tree and final verification binding are recorded below.

## Local byte evidence

The reviewer compared 28 tracked PNW files against exact bytes from
`git show bc7b5e1:<path>`: 12 files in the separately owned
`pnw_herbaria_source_admission_20260911` packet and 16 tracked PNW research
artifacts. Every file was identical. No PNW source or packet was changed.

The five portable Parquet files match the existing integration receipt's hashes
and sizes, totaling 55,323 bytes. This was a byte comparison only; the candidate
was not rebuilt, republished, ingested or accepted by this reviewer.

Candidate identity:
`bspf-0f6b58aba4610edf9b6bcde1e7a0da0c44cf3e4671c1b624b13d7ed95b5ab501`.

| Candidate artifact | Bytes | SHA-256 |
| --- | ---: | --- |
| taxa | 8,782 | `42583d98e56a74aa48ba0e1bc6f3c24ed48275ce6494fe2310c96ae34186b71d` |
| assertions | 11,969 | `49320848d79779c4fa7c9646428b690bef89c7cab5702c417ffc05c65e6658a6` |
| decisions | 11,757 | `1590c4446cdf7d70f6dfe5cb038aca3b92a820e94b4870b3d09d0b562049ad69` |
| profiles | 12,742 | `05152ecb8552f768cf444a7a2fe1c7d5a7a8a70de90dc573a01911fe6f11cf92` |
| manifest | 10,073 | `f4db0f8fc52357e2fcee754ea50a46c335bb30cc108adffca1c17ed4cfd452c0` |

The historical candidate receipt records four taxa, six direct synonyms, eight
categorical source assertions and 188 field decisions. These are fixture counts,
not a census of PlantGeo production. The two populated categorical fields per
taxon do not establish quantitative growing requirements or fuel properties.

## Assessment and remaining gates

| Dimension | Final assessment |
| --- | --- |
| Correctness | All five identified defects are corrected in source and the final full Python sweep passes. Exact identity, pinned release, explicit knowledge states and no relational fallback are consistent. |
| Security and integrity | Local paths and immutable artifact keys are bounded; required data receipts, release metadata and deterministic reconciliation are verified on reads. No new database query or production mutation belongs to serving. |
| Performance | Taxon/assertion/object limits, process-wide reader admission, caller deadline and evidence pagination are explicit. The 2 MiB limit governs profile JSON; protocol envelopes add framing overhead. |
| Maintainability | One domain contract and reader serve the registered HTTP, model and MCP surfaces. Directory documentation records the static-lookup exception. |
| Agent honesty | Source/release/licence/context, decisions and field states are retained. Static profile calls contribute zero environmental rows, and sufficiency excludes this reference tool. Prompts and responses prohibit unsupported ranking and planting conclusions. |

Before any source acceptance or production publication, an authorized PlantGeo
authoring census must establish actual relational data and preservation
requirements. Reviewed original rows, UUIDs and evidence must be preserved, with
explicit canonical crosswalks and separate attributable assertions. No fixture
may overwrite or be represented as superseding those rows.

Source admission, per-value scientific review, production storage readback,
deployed HTTP/MCP smoke checks and deployment remain separate pending gates.
Quantitative growth requirements, roles, companion effects, fuel properties and
fire-response claims cannot be filled from categorical lifeform, family membership,
nearby occurrences or general knowledge. Recommendation and objective-effect
validation belong to their separate Conductor track.

## Final verification binding

The reviewer independently recomputed the service digest using the repository's
stdlib-only `quality_receipt.compute_tree_digest`, checked the p4/source evidence
hashes and all five gate-log hashes, and parsed the final JUnit XML. No test was
rerun in this review lane. The official service receipt and its evidence copy are
byte-identical. The service digest covers 1,328 files and matches both p4 and the
official full-quality receipt.

| Bound evidence | SHA-256 or identity |
| --- | --- |
| Final p4 `integration-receipt.json` | `5d4b6a712c4a6486de34386f0d75eef5c6dcc1895e2d11c738619c95da4584f7` |
| Service tree | `b0ddaeeb60a4374bbd5c98b96de6e679de19ec5b2de206beacdb90eebf4b3674` |
| Digest domain | `plantgeo.agri-data-service.quality-receipt.v2` |
| `verification.json` | `fffecc734a8d55646148591214e75147b14cf1b1b18e0974024ee228ea850439` |
| `python-quality-receipt.json` | `babaeb1ac075d26073273f9a4c80ba302d25653177404fee7bc377ab8b52e335` |
| Final raw JUnit XML | `78fd08f91f2032dcb3c7776d2440295b4a58dae95b6f2eff25a366b818c6ebed` |
| Preserved source descriptor | `d2c094b2e97f4cbba53aa13998286102f682af80cf8e8606331f6284ad0dc966` |

The final `scripts/check.py --write-receipt` invocation passed Ruff formatting,
Ruff lint, mypy and the full Python test suite. The JUnit evidence contains 6,237
cases: **6,086 passed, 150 skipped and one expected failure**, with zero failures
or errors. All 111 botanical cases passed. Database-dependent checks were skipped
with their external database variables absent; this is no deployed-schema or
production-database validation.

The JavaScript data-boundary and TypeScript checks passed. ESLint completed with
zero errors and 542 existing warnings. The reviewer extracted all 26 tracked
warning-bearing file paths from the log and confirmed their bytes match the base.
The changed-JavaScript selector returned `mode: none`, with no test command
selected; no full frontend-suite pass is claimed. The earlier unsuccessful
sweeps and the combined fixture/scratch fixes remain disclosed in
`verification.json`.

The eight source evidence hashes in final p4 match disk. Scoped `.gitattributes`
files preserve the raw bytes bound by evidence hashes across Git line-ending
settings and recognize CR-at-EOL while retaining trailing-whitespace checks;
only exported patch grammar has a whitespace exemption. The five WCVP artifact
hashes and all 28 PNW files were checked again
and remain unchanged. These checks preserve the local candidate; they do not
admit it or infer any production authoring rows.

HTTP evidence exercises the handlers selected from real `create_app` registration;
model and MCP evidence exercises registered schemas and local dispatch. Live
network serving, live model-provider calls and production HTTP/MCP smoke tests
were outside this continuation. The verdict applies only to the exact local
service tree above, with source admission, scientific review and the production
authoring census still pending.
