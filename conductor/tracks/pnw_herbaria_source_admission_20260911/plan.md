---
type: track-plan
track: pnw_herbaria_source_admission_20260911
status: blocked
---

# Plan

The September 11 metadata pass is complete; no specimen archive was downloaded.
The owner subsequently authorized a bounded data-only acquisition after the
pre-acquisition source gates pass. Those gates remain open. The
[packet](evidence/admission-packet.md) records the measured metadata, unmeasured
archive obligations and [handoff](evidence/requests-and-handoff.md).

## A0 — freeze the candidate set

- [x] Re-open the current portal download inventory and WTU/UBC provider pages.
- [x] Record exact collection keys, access URLs, advertised counts, dates and
  terms; keep images and non-admitted collections out of scope.
- [x] Define the intended PlantGeo use and distribution surface against which
  collection terms will be judged.

## A1 — bounded metadata and archive inventory

- [ ] Fetch EML, field mappings and response metadata under the documented caps.
  Partial: UBC v16.43 standalone EML and three HTML metadata receipts captured;
  WTU EML and both archive field maps remain unmeasured.
- [ ] If terms permit, capture each complete archive once into quarantine and
  verify the ZIP/member safety, hashes, counts and schema.
- [ ] Reconcile accepted, out-of-envelope, excluded-by-rights, nonspatial,
  duplicate-native-record and quarantined counts.

## A2 — release and identity proof

- [ ] Compare at least two equivalent complete releases before treating a native
  identifier or source watermark as stable.
- [ ] Specify correction, withdrawal and source-shrink behavior without treating
  a partial export as a deletion feed.
- [ ] Issue one admission/refusal packet per collection and obtain independent
  governance review.
- [x] Keep the author and independent governance/archive-safety reviewer in
  separate task contexts; the reviewer owns the final admission verdict.

The downstream Parquet and experience tracks remain planned until A2 admits at
least one exact collection release.

## September 11 bounded evidence outcome

- [x] Produce separate WTU and UBC blocked admission decisions, source register,
  custody requirements, quarantine/reconciliation rules and unsent requests.
- [x] Identify UBC institutional v16.43 and v16.42 as comparison candidates;
  do not confuse the newer institutional release with the older portal copy.
- [x] Record the parent-owned hybrid species-profile boundary and historical
  agri.species evidence without editing the profile or occurrence implementation.
- [x] Independent reviewer owns the final verdict in evidence/independent-review.md:
  both admissions blocked; bounded metadata packet accepted.
- [x] One final documentation/JSON/local-link/hash/whitespace verification sweep; see evidence/verification.json.

The final report supplies the bounded commit and remaining gates to parent and
integration tasks; no merge or push is authorized by this packet.

Admission remains blocked on WTU release/EML evidence, UBC institutional
coordinate-policy applicability and both collections' unmeasured archive/schema
and native-ID stability receipts. A two-release UBC pilot consumes the two-archive
budget and must defer WTU rather than silently widening acquisition.
