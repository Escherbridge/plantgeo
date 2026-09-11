---
type: plan
track: offline_export_service_20260908
status: ready_for_authorized_publication
created: 2026-09-08
---

# Plan — offline export service

## Current scope and remaining ledger — September 11

The September 9 owner decision rejected the standalone `plantgeo-export` service
and selected the existing in-repository canonical-snapshot builders. Phase 1's
standalone service and the old “where does the service live” question below are
superseded design history; do not restart them. The five ERA5 product lanes were
built in that cutover. All three NASA POWER temperature histories were published
September 10 with 1,560 fully verified days each across four rungs; see the
[operational retrospective](../../retros/parquet_operational_checkpoints_20260911/README.md).

The September 11 evidence reconciles the discarded design with the selected builders. It does not
invent retroactive approval of the standalone service and it does not authorize publication.

- [x] Reconcile each original phase with the chosen in-repository builder,
  evidence and independent review; explicitly mark discarded standalone-service
  deliverables superseded instead of implementing them again.
- [x] Bind the complete eight-lane source/history/rung manifest and verify each
  declared horizon; do not rebuild the proved temperature window.
- [x] Record the measurable current verification and local-candidate costs and disclose that the
  historical stage/build/upload telemetry was not preserved; do not invent a comparison. The old
  baseline remains recorded.
- [x] Construct the deferred **relative-humidity 1981–2017** availability history locally using
  the supported bounded publication contract and exact receipts. This supersedes
  the earlier 1981–1985-only deferred window below; no upload or apply receipt is recorded.
- [x] Refresh the warm capability/serving result for the exact candidate and
  hand remaining runtime/forward gaps to gapless and production acceptance.

Historical phase checkboxes and verdicts below are preserved as the review ledger;
this checkpoint governs their present scope.

Six phases. Each ends with an adversarial review in a separate context, recorded as a one-line
verdict. A phase with no verdict is unreviewed, not done.

## Phase 0 — Prove the staging premise (no writes)

- [ ] Census `raw-canonical/signal-observation/snapshot=prod-20260826-full-signal-v1/` per lane:
      object count, bytes, month coverage, and the `source=`/`product=`/`support=` triple each of the
      eight lanes reads. Expected ~8,796 objects / 1.96 GiB total.
- [ ] Confirm from the manifest that `row_count`, `partition_count`, `batch_count` and
      `observation_day_min/max` match what `build_soil_moisture_from_canonical_snapshot.py` pins.
- [ ] For each of the eight lanes, record: expected first day, last day, day count, cells per day.
      `soil-field-vpd` already holds **462** correctly-shaped live day partitions — establish exactly
      which days, because it must be RESUMED, not rebuilt.
- [ ] **Deliverable:** a per-lane manifest committed to the track's `evidence/`. No lane proceeds
      without one.

**Verdict:** **PASS** — exact selected-builder manifest and checksumed zero-finding live audit.

## Phase 1 — The staging layer

- [ ] `stage pull` — download the pinned snapshot to local disk, verify each part against the
      manifest's digest, and record a local inventory. Idempotent and resumable; a re-run costs a
      HEAD per object, not a GET.
- [ ] `stage verify` — re-verify local state without network.
- [ ] Refuse to proceed if the manifest SHA-256 differs from the pinned constant. A silently moved
      snapshot must fail loudly, not produce a plausible wrong build.

**Verdict:** **SUPERSEDED** — manifest-pinned direct streaming and create-only writes replaced the
discarded standalone stage service; no replacement service or local mirror was built.

## Phase 2 — The local transform

- [ ] One PARAMETERISED builder for the five ERA5-Land lanes (`soil-field-vpd` + four
      `soil-temperature-*`): same `source=open-meteo-era5-land-archive`, `support=era5-land-0.1deg`,
      1,470-cell grid. Five `ProductSpec` entries, not five scripts.
- [ ] A second instance for the three NASA POWER air-temperature lanes:
      `source=nasa-power-daily`, `support=surface`, 397-cell `nasa-power-0.5-degree` grid.
- [ ] Emit day-grain output locally: `kind=observed/zoom=NN/year/month/day/part-N.parquet` for
      zoom ∈ {13,9,5,0}, plus a real `PartitionCompletion` per (day, rung) — `completed_at`,
      `part_count`, `row_count`, `run_id`, `schema_version`. **Never a breakdown sidecar.**
- [ ] Dedup precedence and lineage alignment must match the proven builder exactly; cite it.
- [ ] Never emit `kind=physical` to a live prefix.

**Verdict:** **PASS** — the two selected parameterized builders own all eight day-grain observed
ladders and emit real completion markers.

## Phase 3 — Bulk upload and verification

- [ ] `push` — high-concurrency upload of the local tree, refusing to overwrite an existing key
      unless `--replace` is passed explicitly.
- [ ] `verify` — re-census the live prefix and assert per-rung `parts == markers` and that every day
      carries the full ladder, before anything downstream runs.
- [ ] Record objects written, bytes, wall-clock, and effective objects/sec. This number is the
      track's whole justification; if it is not far better than ~150/min, say so plainly.

**Verdict:** **PASS WITH DISCLOSED TELEMETRY GAP** — current verification is measured and zero-finding;
the September 9 stage/build/upload timings were not preserved and are explicitly unavailable.

## Phase 4 — Availability, without re-reading the world

- [ ] Emit the `availability-bootstrap-input-v1` document **from local state**, since the builder
      already knows every digest and row count it just wrote. This is the step that removes the
      ~150 objects/min compile.
- [ ] Keep the document under `MAX_INPUT_BYTES` (64 MiB, ~1,092 bytes/row ⇒ ~61k rows). Trim the
      window and RECORD the deferred days; never raise the cap.
- [ ] Validate offline (`availability-bootstrap` with no `--apply`), then stop and ask.
- [ ] **The apply is owner-confirmed.** Watch for the 2026-09-07 hang: `idle in transaction` on
      `Client/ClientRead` with no object-store connection and nothing written. If it recurs, kill,
      confirm no stale transaction, and re-run rather than waiting.

**Verdict:** **PASS FOR LOCAL CONSTRUCTION** — 13,514 days / 54,056 fully digested rows are split into
three parser-valid inputs below 64 MiB. Upload and apply remain unauthorized and unperformed.

## Phase 5 — Close

- [ ] Re-probe `getSliderCapabilities` warm and record serving/withheld counts.
- [ ] Update `conductor/RUNBOOK.md` with a named section (append only — another session shares it).
- [ ] Record the wall-clock comparison against the 2026-09-07 baseline.

**Verdict:** **PASS FOR OFFLINE SCOPE** — warm serving is healthy and the immutable publication and
rollback handoff is complete. Runtime/forward closure and post-apply acceptance remain delegated.

## Serialized files no agent may edit in parallel

`pipeline/parquet/lane_registry.py`, `execution/job_executor_service.py` (LANE_SPECS),
`interface/cli/data.py`, `QUALITY_RECEIPT.json`, `parquet_ops/snapshot_products.py`.
A join agent lands registrations after review.

## Historical owner questions and disposition

1. **Deferred history: resolved for local construction.** The candidate now covers all of
   1981-01-01..2017-12-31 in three bounded inputs; publication remains owner-authorized work.
2. **Object count: deferred outside this track.** Re-partitioning would change the serving contract.
3. **Where the service lives: superseded.** No standalone service was built; the selected
   in-repository builders remain authoritative.
