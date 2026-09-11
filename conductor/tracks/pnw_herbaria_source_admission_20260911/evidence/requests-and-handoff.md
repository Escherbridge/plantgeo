---
type: source-admission-handoff
status: blocked
updated_on: 2026-09-11
---

# Clarification drafts and downstream handoff

These messages are drafts only. No institution was contacted, agreement signed,
terms accepted or data requested externally. They are not permission grants.

## WTU draft

Recipient role: WTU Herbarium collection/data manager, using the contact route
published in the [WTU metadata](https://www.pnwherbaria.org/data/providermetadata.php?code=WTU).
Confirm the current recipient before sending.

Subject: WTU vascular public specimen export: release and reuse clarification

PlantGeo is evaluating the public WTU vascular specimen dataset for a data-only
pilot. Proposed use is immutable source archiving, normalization, public maps,
downloads and documented-taxon summaries, including possible commercial use.
We exclude all images, media, restricted records and withheld localities.

Your provider page labels specimen facts CC0. Please confirm the applicable
terms for the public WTU_Vascular_DwCA.zip export and provide standalone EML and
meta.xml, its complete-public-population scope, a dated immutable release
identifier/checksum, and an earlier equivalent complete release. Please describe
occurrence/annotation identifier persistence, corrections/deletions, sensitive
coordinate suppression/generalization, required citation, archive retention and
whether any written permission or signed agreement is needed for these uses.
If so, please provide the agreement for our operator's review; this message
does not accept any terms or request restricted data.

## UBC draft

Recipient role: UBC Herbarium vascular collections/data contact; verify against
[current institutional IPT metadata](https://data.canadensys.net/ipt/resource?r=ubc-vascular-specimens).

Subject: UBC vascular IPT v16.43: public-coordinate scope and release custody

PlantGeo is evaluating public UBC vascular specimen facts from Canadensys IPT
resource ubc-vascular-specimens, v16.43, package
07fd0d79-4883-435f-bba1-58fef110cd13/v16.43. We have inspected standalone EML,
which states CC0 including commercial redistribution, and have not downloaded
occurrence data or images.

Please confirm whether this institutional export suppresses sensitive records
and coordinates, how withheld/generalized fields are signaled, and whether
it is appropriate for public redistribution at its supplied spatial precision.
Please clarify any additional institutional terms or signed agreement, and
confirm that v16.43 and v16.42 are equivalent complete public exports with
stable native occurrence identifiers and retained immutable bytes. Please
provide published archive checksums, correction/deletion policy and preferred
version-specific attribution where available. We propose data-only immutable
archiving and normalization followed, under separate review, by public maps,
downloads and summaries including possible commercial use. No images, private
coordinates or restricted exports are requested. Any agreement would require
our operator's separate review and signature.

## Operator and independent reviewer checklist

- [ ] Resolve the exact institution, collection, distributor and public population.
- [ ] Bind EML, provider/portal policy and record terms to the same release.
- [ ] Confirm copying, transformation, retention, public/API/download and commercial
  redistribution permissions for the intended surface.
- [ ] Resolve any conflicting additional terms; obtain required permission/agreement
  from the actual rights authority. Record signer, authority, scope, date,
  expiry/termination and permitted audit retention. Do not auto-sign.
- [ ] Verify source withholding/generalization applies to the chosen distribution;
  review residual free-text locality risks and intended display precision.
- [ ] Approve custody owner, allowlisted URLs, ceilings and fail-closed archive controls.
- [ ] Report the pre-acquisition permission verdict to the parent before transfer.
- [ ] Perform a permitted quarantine capture, bind archive/member/EML/meta hashes,
  reconcile counts and schema, then independently review.
- [ ] Compare two complete equivalent releases for native identity and corrections.
- [ ] Issue an exact-release admission verdict and allowed-use manifest.
- [ ] Separately authorize downstream build; publication, scheduling, APIs/UI and
  production remain outside this source-admission authorization.

No public CC0 signature requirement has yet been proved. The drafts address
unresolved policy/identity facts; institutional loan or image forms must not
be substituted without demonstrating that they govern this dataset.

## Occurrence-plane handoff

Recipient: [botanical_occurrence_parquet_lane_20260911](../../botanical_occurrence_parquet_lane_20260911/spec.md).
Admitted collections: none. Ingestion handoff status: blocked.

Once admitted, transfer the reviewed manifest containing exact archive and member
hashes/locations, EML/meta hashes, collection/distributor identity, rights and
public-coordinate scope, two-release identity findings, field map/profile/count
reconciliation, parser recipe, limits, custody/retention rules, exclusion reasons,
correction/tombstone and withdrawal procedures. The target may normalize only
that admitted population. A data-only pilot is now intended; there is no implicit
runtime implementation or production authorization in this evidence pass.

Keep sparse occurrences, support evaluation, aggregates and environmental joins
in the governed Parquet plane. Use event intervals separately from source-release
availability; do not treat a collection event as a daily ecological observation
or an environmental absence. One distributor per collection/release; never union
UBC portal and institutional versions as independent specimens.

## Parent-owned species-profile boundary

The owner separately requested a versioned nonspatial growth-requirements lookup,
`botanical-species-profile`, keyed by canonical taxon ID. Parent owns that track;
this source track records only the interface and source-admission implications.

[Label-plane readiness, August 14](../../../../services/agri-data-service/ml/research/label-plane-readiness-2026-08-14.md)
reported `agri.species` empty at that historical census, with trait columns already
present. This pass made no new production census. The current
[Species model](../../../../services/agri-data-service/src/agri_data_service/models/species.py)
has identity/name and growth habit, precipitation, pH, light, drought/salt tolerance
and other trait fields. It does **not** itself provide canonical authority-version
identity or per-value provenance/review columns; companion-relationship evidence
columns are a separate model. The old report's broad governance wording must not
be read as proof those per-value columns already exist. Boolean defaults must
not turn missing evidence into a negative trait.

Use `agri.species` or a compatible reviewed authoring surface for draft/reviewed
curation. The explicit source-of-truth transition is approval/export into one
immutable profile Parquet release. Only the approved release pinned by a response
may feed APIs/agents/recommendations; a missing profile release must refuse,
never fall back silently to live database values. Every value needs source,
license, review state, valid/retrieved dates, canonical-taxon mapping/version and
release lineage. The parent determines any authoring-schema change separately.

A second trait source is deferred enrichment: fill missing values under an
explicit priority/conflict rule, preserving contrary evidence rather than silently
overwriting higher-priority values. This does not block a properly licensed first
trait-source pilot. These occurrence archives have not been admitted as trait
sources and do not prove growth requirements. Establishment/suitability traits
must remain distinct from species-objective effect evidence and causal efficacy.
