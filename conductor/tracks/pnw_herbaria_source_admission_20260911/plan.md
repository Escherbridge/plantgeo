---
type: track-plan
track: pnw_herbaria_source_admission_20260911
status: active
---

# Plan

The September 11 metadata pass is complete; no specimen archive was downloaded.
The owner subsequently authorized a bounded data-only acquisition after the
pre-acquisition source gates pass. Those gates remain open. The
[packet](evidence/admission-packet.md) records the measured metadata, unmeasured
archive obligations and [handoff](evidence/requests-and-handoff.md).

The track is active under `b9b7bf4`; the two collection admissions are blocked.
The authorized future stage may inspect, hash and inventory permitted archives
in quarantine and prepare an ingestion handoff after the collection rights,
withholding and archive-control gates pass. No acquisition occurs during this
baseline synchronization. The existing two-archive ceiling is unchanged.
See [baseline synchronization](evidence/baseline-synchronization.md) for current
track relationships and the verification receipt for this merge.

## A0 — freeze the candidate set

- [x] Re-open the current portal download inventory and WTU/UBC provider pages.
- [x] Record exact collection keys, access URLs, advertised counts, dates and
  terms; keep images and non-admitted collections out of scope.
- [x] Define the intended PlantGeo use and distribution surface against which
  collection terms will be judged.

## A1 — bounded metadata and archive inventory

- [ ] Fetch EML, field mappings and response metadata under the documented caps.
  Partial: UBC v16.43 standalone EML and three HTML metadata receipts captured;
  a September 12 refresh captured seven metadata responses and confirmed the UBC
  EML hash. A second September 12 pass (redirect-refusing, GBIF-registry-
  corroborated) captured **release-bound WTU EML for two complete releases**
  (v1.0 2025-11-06, v1.1 2026-03-12, GBIF UUID `8310f570-f762-11e1-a439-00145eb45e9a`)
  from the actual distribution host `ipt.pnwherbaria.org`, and found the
  release-bound licence is CC-BY 4.0, not the CC0 the portal page implied — see
  [evidence/wtu-gbif-identity-20260912.md](evidence/wtu-gbif-identity-20260912.md).
  Both archive field maps remain unmeasured because archive transfer is gated.
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

Admission remains blocked on WTU coordinate-policy applicability and release-bound
terms reconciliation, UBC institutional coordinate-policy applicability, and both
collections' archive-custody, ZIP/member-safety, schema and native-ID stability
receipts. The external `claude/herbaria-botanical-lanes` worktree contains pending,
uncommitted implementation and evidence work; it does not close these acquisition
gates. A two-release UBC pilot consumes the two-archive budget and must defer WTU
rather than silently widening acquisition.
