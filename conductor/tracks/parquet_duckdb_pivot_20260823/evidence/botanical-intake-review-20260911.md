---
type: independent-review
track: parquet_duckdb_pivot_20260823
status: bounded-pass-composition-only
---

# Botanical full-intake composition review — 2026-09-11

**Bounded PASS. No P0–P3 findings were identified in the reviewed composition.**
This independent review covers the exact 65-path botanical intake and its shared
registrations, preservation of the accepted environmental behavior, and explicit
pending admission/publication gates. It does not accept a source release, grant
production authority, or replace the required verification of the new candidate.

The pre-intake HEAD is `9b46e86155a65e56a5186fcd087fea944bdf5ba1`.
The source is `edc6afdeb23f339b40049ec0828551c2fe1a4d45`, tree
`01ad55b6220a13604e8fbf8a4ceda773e35d5658`, with sole parent/base
`bc7b5e1ff5dbb6eac929d5db927acd6b7ff2ea4a`. HEAD remained unchanged throughout
this review. The reviewed result is staged; it has not acquired a new verification
receipt by virtue of intake.

The portable [author intake manifest](botanical-intake-20260911.json) is 53,764 bytes,
SHA-256 `c56fe3a0ee08cbc62d7b07f6018a4e63d82461b7bb13d0452530531abf9bc2c7`.
It is byte-identical to the original scratch author manifest. Its pins were checked
against independently read Git trees, index entries and current file bytes rather
than accepted from the author's verdict. The companion
[review JSON](botanical-intake-review-20260911.json) retains all 65 path receipts,
immutable modes/blob IDs, current hashes, assertion review and scope limits.

## Complete path accounting

The manifest path set equals the source commit's complete 65-path change set.
Sixty index entries preserve the exact source blob and mode. Three files compose
source changes with the accepted candidate, one metadata file changes only its
pending-gate field, and one quality receipt preserves the pre-intake result.
Exactly 64 intake paths therefore change relative to pre-intake HEAD. The initial
staged inventory contains no path outside that set; all 65 working files match
index content in their actual raw or declared text form and remained unchanged
at final review. There are no unmerged index entries.

Binary evidence is compared as bytes without line-ending normalization. The five
portable Parquet artifacts match their recorded sizes and SHA-256 values, totaling
55,323 bytes. They remain the same four-taxon unaccepted local candidate. This
review verifies their custody hashes, without regenerating, publishing or granting
scientific/source admission to their contents.

## Shared-file composition

| File | Independent result |
| --- | --- |
| `agent/tools.py` | Exact reconstruction from pre-intake plus only the botanical import, the `record_profile_tools(_record)` scope around the existing yield, and one `species_information` registry entry. |
| `agent/AGENTS.md` | Exact union of the new static botanical section and the complete accepted pre-intake document. The existing exact-day feature admission guidance is retained. |
| `conductor/tracks.md` | Exact pre-intake text except the replaced botanical-profile row and the added source-admission row. Every other environmental, forecast and botanical track row remains intact. |
| Source-admission `metadata.json` | All source metadata matches after excluding `next_gate`; the corrected gate now requires production census, reviewed-row preservation and independent source admission, with production authorization separate. |
| `QUALITY_RECEIPT.json` | Exact pre-intake bytes, SHA-256 `7fb279078524a8f674a47252c7c3d3ed1156a0d8625f0b09a4e935c3fd20e836`. This receipt is historical evidence and is not valid for the composed source. |

The retained `registered_census_lanes` import, `_feature_surface_refusal` function,
its pre-storage call in `query_feature_value_near_point`, and exact-partition and
governed-absence wording are unchanged. The refusal still executes before
`warehouse.lane_window` or subsequent storage reads. Existing snapshot-specific
and vegetation temporal-semantics refusals are therefore preserved by composition.

The final content has one botanical import, one recorder binding, one warehouse
registry entry and one botanical guidance section. Other shared registration files
are exact source blobs. This independently supports one complete intake with no
second application of `shared-registration.patch`; the retained patch is source
evidence. The author's action receipt records one no-commit cherry-pick and no
patch application. This review did not execute either operation.

## Registration and assertion inspection

The HTTP blueprint is added only to `combined_local` and `published_reader`;
`receiver_writer` retains its previous route set. The model registry is the common
source for Anthropic, OpenAI schemas and MCP dispatch. The new recorder reports
`profile_count` with `row_count=0` and `evidence_domain=botanical_reference`.
The sufficiency denominator excludes the static species tool, preserving the
existing environmental tool population and avoiding a claim of local/day coverage.

The new tool and HTTP handler use the same bounded reader. They require the complete
authority/version/taxon identity and exact `bspf-<sha256>` release, retain explicit
unknown/refused states, and do not introduce an editable species-database fallback.
The reader retains the shared admission boundary, a 14-second deadline, assertion
pages capped at 100 and an exact compact UTF-8 response ceiling of 2 MiB. The prompt
and MCP instructions distinguish static reference evidence from selected-day
observations, local suitability, ranking and planting advice.

The relevant test assertions were read statically, not executed:

- `test_botanical_species_profile_agent.py` checks matching model/MCP schemas,
  canonical required fields, release syntax, assertion limits, a SQL-refusing
  session provider, exact ledger contents, unchanged environmental sufficiency,
  raw citation/release metadata, MCP dispatch, unknown/unpublished/integrity
  outcomes, and rejection of name-only/latest requests.
- `test_botanical_species_profile_routes.py` locates and dispatches the actual app
  factory's single registered route, checks both reader profiles and exclusion from
  the writer profile, and asserts malformed requests are refused before object
  reads. It checks byte-exact Unicode response budgeting, status/refusal semantics,
  integrity faults and continuation identity.
- Shared graph/provider and direct-package tests retain the environmental tools
  while explicitly accounting for the one nonspatial reference tool. The direct
  package exceptions identify its offline release publication contract rather than
  declaring an environmental day/bbox cron or writer lane.

## Gates that remain open

The admitted scope is local source composition. The source-admission spec, plan,
metadata and evidence continue to describe the four-taxon WCVP bundle as an
unaccepted candidate. The earlier source-author descriptor and historical
census-unavailable note remain preserved evidence, not an independent source
verdict or a statement about current production table contents.

Production authoring census, preservation of existing reviewed UUIDs/evidence,
canonical crosswalk decisions and independent botanical/data-governance admission
remain pending. Further ingestion, immutable publication/readback and pointer
changes, access configuration, deployment and deployed HTTP/model/MCP acceptance
require their separate authorized gates. USDA/TRY/FEIS enrichment, quantitative
requirements, fuel assertions, objective effects and recommendation ranking remain
unadmitted or assigned to their successor contracts. No local candidate authoring
or engineering review implies those permissions.

The first candidate's full verification evidence and the botanical source's own
historical verification remain separate. After the root freezes the composed
candidate, the already-reviewed botanical runner must perform its fresh full sweep
and image gates. This review ran no tests, builds, product imports, database or
network operations. It changed only review evidence, made no implementation edit
or commit, and does not approve later parent QA document changes outside these
65 paths. Existing peer implementation review is retained as provenance; this
bounded composition pass does not claim a second comprehensive scientific review.
