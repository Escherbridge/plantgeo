---
type: runbook
track: offline_export_service_20260908
created: 2026-09-08
---

# RUNBOOK — offline export service

Operational reference for the cutaway exporter. Append named sections; never rewrite the header.
`conductor/RUNBOOK.md` remains the project-wide log and is shared with concurrent sessions.

## 0. The numbers this track exists to beat

Measured 2026-09-07/08 against production.

| fact | value |
|---|---|
| bucket total | 14.00 GiB / 1,047,868 objects |
| serving prefixes | 9.49 GiB / 998,486 objects (~10 KB average) |
| `raw-canonical` (the staging target) | 1.96 GiB / 8,796 objects |
| `derived-canonical` | 2.54 GiB / 40,585 objects |
| bootstrap evidence compile | ~150 objects/min, serial |
| relative-humidity apply | hung at 3h08m |

**The bottleneck is object COUNT, not bytes.** Two gigabytes downloads in minutes; a million round
trips do not.

## 1. Environment

```bash
cd services/agri-data-service          # credentials live here, gitignored
# every python command:
UV_NO_SYNC=1 uv run --no-sync python ...
```

`uv sync` without `--no-sync` strips pytest. Credentials come from
`services/agri-data-service/.env` — read them, never echo them, never commit them, never put them in
a fixture.

## 2. Preflight, every session

```bash
# 1. Which lanes are actually withheld right now, and why
curl -s "https://plantgeo-main-production.up.railway.app/api/trpc/environmental.getSliderCapabilities" \
  | python -c "import json,sys; d=json.load(sys.stdin)['result']['data']['json']; \
      print('serving', len(d['layers'])); \
      [print(' ', w['layerName'], w['reason']) for w in d['withheldParquetCapabilities']]"
```

**Call it twice.** The first request after a deploy is cold and can exhaust the 8 s coverage timeout,
returning `coverage_unavailable` for EVERY layer — which looks exactly like a total outage and is not
one. Warm is ~0.35 s. This cost a false alarm on 2026-09-07.

## 3. The six traps, in the order they will bite

### 3.1 `layout` decides everything
`SnapshotProduct.layout` is `daily` or `monthly`. Only `daily` may be promoted by copy. A month-grain
root copied onto a live prefix puts month files where `classify_partition_day` looks for a `day=`
segment: the lane advertises nothing AND the objects must be hunted down and deleted. This happened to
the air-temperature trio on 2026-09-07 — 1,908 unreadable objects, reverted same day.

### 3.2 `data_root` has two trees
`layer=<slug>/snapshot=<id>` **or** `derived-canonical/signal-observation/lane=<slug>/snapshot=<id>`.
Always census `product.data_root`. A census of only the first reported the three `soil-wetness-*`
lanes as holding nothing anywhere while each held 6,240 parts.

### 3.3 `_complete.json` is two different documents
```
SIDECAR  {base_lineage_sha256, contract_version, day, input_manifest_sha256, lane,
          part_count, part_key, part_sha256, product_parameter, row_count, tier}
MARKER   {completed_at, part_count, row_count, run_id, schema_version}
```
`classify_partition_day` reads the second. Given the first it finds no terminal state and the compiler
refuses the lane with *"no day holds the exact required-rungs ladder (0, 5, 9, 13) in one terminal
state"* while every part sits there correct. **A lane can be 100% present and advertise nothing.**

### 3.4 `MAX_INPUT_BYTES` is 64 MiB
`availability_index.py:90`. At ~1,092 bytes/row that is ~61,000 rows ≈ 15,250 lane-days. Relative
humidity's full 16,598 days compiled to 69.12 MiB and was refused. **Trim the window with `--since`
and record the deferred days. Never raise the cap.**

### 3.5 `kind=physical` never goes to a live prefix
A byte-identical audit copy of raw source parts, no zoom segment. `grep -rn "kind=physical" src/`
finds exactly one hit and it is a comment — no reader opens it.

### 3.6 Evidence must be uploaded before `--apply`
`--apply` reads back the evidence objects the compiler wrote locally. Skip the upload and it fails
with `receipt object '...' is missing`. Upload first, then apply.

## 4. The apply hang, and how to tell it from slowness

On 2026-09-07 the relative-humidity apply ran 3h08m and wrote nothing. Diagnosis that settled it:

```sql
SELECT pid, state, wait_event_type, wait_event, now()-xact_start AS age,
       left(regexp_replace(query,'[[:space:]]+',' ','g'),110)
FROM pg_stat_activity WHERE datname = current_database() AND pid <> pg_backend_pid();
```

**Stuck** looks like: `idle in transaction` / `Client/ClientRead`, age in hours, last statement
`pg_try_advisory_lock(...)`, `ungranted locks: 0`, and — decisively — **no established connection to
the object store**:

```powershell
Get-NetTCPConnection -OwningProcess <pid> | Where-Object State -eq 'Established'
```

A healthy apply holds BOTH the DB proxy and an HTTPS connection to the object store. CPU alone cannot
tell the two apart: both sit near 0.2% of a core. Nothing written for hours plus no object-store
socket is the signal.

**Recovery:** kill the process, confirm no stale `idle in transaction` older than two minutes, re-run.
The apply is idempotent and the uploaded evidence is not lost.

## 5. Verify a lane before believing it

```python
# per-rung parts vs markers, and days holding the FULL ladder
parts[zoom] == markers[zoom]  for every zoom in (0, 5, 9, 13)
len(days_on_all_rungs) == len(all_days)
```

A lane with `parts != markers` on any rung, or with days missing from the ladder, will be refused by
the compiler — better to learn that from a census than from a two-hour apply.

## 6. Known-good sequence for one lane

1. Census `product.data_root`; check `layout` first.
2. Stage source locally; verify against the manifest digest.
3. Transform locally to day grain, writing real `PartitionCompletion` markers.
4. Bulk-upload; refuse to overwrite without `--replace`.
5. Re-census the live prefix: parts == markers, full ladder every day.
6. Compile the bootstrap input (or emit it from local state).
7. Upload evidence.
8. Validate offline. **Stop. Owner-confirm.**
9. `--apply`, watching for the §4 hang.
10. Re-probe capabilities warm, twice.

## 7. Live blockers not owned by this track

- **`climate-nasa-power-direct-forward` is dead-lettered**, `attempt_count` 6/6, since
  2026-09-07T08:32Z, error `scheduled_command_exit: command exited with status 1`. The three
  air-temperature lanes get no NEW days until it is fixed, independent of any backfill. Nine other
  lanes also hold dead-letters, `matview-refresh` with **214**.
- **`soil-survey`**: job time budget 1,230 s against a 3h20m run, killed before
  `_finalize_written_day`; 959 parts at `zoom=13` with **zero** markers. Also pinned
  `servingReader: "postgresql"`, so a completed export surfaces as `reader_not_parquet` next.
- **Postgres cost**: `layers` has 33,273,668 sequential scans against a 48 kB table; `features` is
  8,635 MB for 247,706 live rows; `pg_stat_statements` is **not installed**, so query cost cannot be
  attributed today. Serving-path problem, charted separately.
