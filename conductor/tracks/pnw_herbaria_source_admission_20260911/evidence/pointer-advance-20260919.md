---
type: evidence
status: complete
updated_on: 2026-09-19
---

# `_LATEST.json` pointer advance — botanical-occurrences, production (2026-09-19)

Owner-authorised production write (2026-09-19). Advances the botanical-occurrences lane's
checksum-bound `availability/_LATEST.json` pointer (layer-lanes §4a) to name generation
`956c0be71910469005fb494d92aac035223be49d1f5c895c0b1206a716b16ac4`, the already-published,
already-`_COMPLETE` UBC v16.43 release that the legacy `current.json` already named. This is
the recovery item flagged in `.omc/ultrapilot-20260918/W1-D.md` risk #1: without it, production
`/current` answers `503 pointer_missing` because the bucket predates §4a's pointer document.

No source file in the repository was modified. This is an operations-only write against the
production object store (bucket `plantgeo-parquet-9ymvp7gv`, service `plantgeo-parquet-api`),
performed with `advance_latest_pointer()` from
`services/agri-data-service/src/agri_data_service/pipeline/direct/botanical_occurrences/publish.py`.
No CLI verb wraps this function yet (grepped `interface/` — no hits), so it was invoked via a
one-off `uv run --no-sync python` driver script (not committed) run from
`services/agri-data-service`. Credentials (`OBJECT_STORE_*`) were pulled from the
`plantgeo-parquet-api` Railway service and injected only into that one-off process's environment;
they were never written to disk and (after one early command-history slip, corrected mid-task)
were not printed in the operator's log for the dry-run/write steps below.

## 1. Dry run (read-only, no write)

Resolved `publication_target()` against the live bucket and checked state before writing:

```
_LATEST.json exists: False
legacy current.json -> release_set_id: 956c0be71910469005fb494d92aac035223be49d1f5c895c0b1206a716b16ac4
target generation: 956c0be71910469005fb494d92aac035223be49d1f5c895c0b1206a716b16ac4
target generation _COMPLETE present: True
manifest readable via read_manifest(): True
manifest published_at: 2026-09-13T13:18:49.937501+00:00
manifest counts: {'associations': 30440, 'identifications': 0, 'nonspatial_occurrences': 177728, 'occurrences': 15220, 'raw_occurrences': 192948, 'releases': 1}
```

All preconditions held: `_LATEST.json` was absent (bridged read only), the legacy pointer already
named the target generation, and that generation's `_COMPLETE` marker and manifest were both
present and readable. Proceeded to write.

## 2. Write

Called `advance_latest_pointer(target, "956c0be71910469005fb494d92aac035223be49d1f5c895c0b1206a716b16ac4")`
once. It re-derives the digest from the generation's own manifest rather than trusting a caller
value, and would have refused (returned `None`) had `_COMPLETE` or the manifest been missing —
neither happened.

```
advance_latest_pointer wrote the pointer.
generation_id: 956c0be71910469005fb494d92aac035223be49d1f5c895c0b1206a716b16ac4
manifest_key: botanical-occurrences/956c0be71910469005fb494d92aac035223be49d1f5c895c0b1206a716b16ac4/manifest.json
manifest_sha256: 6fecc4a911d4ee5b357cc17fd56d29e4898d13e6a553773e23275e14ae704110
pointer_schema_version: 1
published_at: 2026-09-13T13:18:49.937501+00:00
pointer_written_at: 2026-09-19T02:45:17.011994+00:00
```

Re-read `_LATEST.json` from the bucket immediately after the write and decoded it with
`parse_latest_pointer()` — the values above are that decode, not the caller's in-memory copy.

## 3. Wire verification

**`GET https://plantgeo-parquet-api-production.up.railway.app/api/v1/botanical-occurrences/current`:**

```json
{"generation_id": "956c0be71910469005fb494d92aac035223be49d1f5c895c0b1206a716b16ac4", "manifest_key": "botanical-occurrences/956c0be71910469005fb494d92aac035223be49d1f5c895c0b1206a716b16ac4/manifest.json", "manifest_sha256": "6fecc4a911d4ee5b357cc17fd56d29e4898d13e6a553773e23275e14ae704110", "pointer_kind": "latest_v1", "pointer_schema_version": 1, "pointer_written_at": "2026-09-19T02:45:17.011994+00:00", "product": "botanical-occurrences", "published_at": "2026-09-13T13:18:49.937501+00:00", "qc_policy_version": "botanical-qc-v1", "release_set_id": "956c0be71910469005fb494d92aac035223be49d1f5c895c0b1206a716b16ac4", "state": "current", "taxonomy_recipe_version": "source-names-v1"}
```

`pointer_kind: "latest_v1"` (was `legacy_current_json` before the write). Generation id matches.

**`GET https://plantgeo.aevani.com/api/botanical-occurrences?bbox=-123,47,-122.8,47.2&zoom=8`:**

Returned `"state":"aggregate"`, `"servingRung":"grid-0.05"`, 36 cells, and:

```json
"pointer":{"generationId":"956c0be71910469005fb494d92aac035223be49d1f5c895c0b1206a716b16ac4","manifestChecksum":"6fecc4a911d4ee5b357cc17fd56d29e4898d13e6a553773e23275e14ae704110","manifestKey":"botanical-occurrences/956c0be71910469005fb494d92aac035223be49d1f5c895c0b1206a716b16ac4/manifest.json","pointerKind":"latest_v1","pointerSchemaVersion":1,"pointerWrittenAt":"2026-09-19T02:45:17.011994+00:00","publishedAt":"2026-09-13T13:18:49.937501+00:00"}
```

`pointer.pointerKind: "latest_v1"`, same generation id. Both live surfaces now resolve through
the checksum-bound pointer, not the legacy bridge.

Parquet-api log tail was not separately captured for the bridge-warning-disappearing check (both
live reads above already answered `latest_v1` on the first request after the write, which is the
behavior the warning-disappearing check exists to confirm; no follow-up read was needed to
observe a persisting warning).

## Verdict

**ADVANCED.** `_LATEST.json` now names generation `956c0be71910469005fb494d92aac035223be49d1f5c895c0b1206a716b16ac4`
in production; both the parquet-api `/current` endpoint and the `plantgeo.aevani.com` proxy resolve
through `pointer_kind: latest_v1`. `advance_latest_pointer` is idempotent by manifest re-derivation,
so re-running it against the same generation is safe if needed.

Follow-up (per `AGENTS.md`'s recorded plan, not done here): retiring the `current.json` bridge
(`_resolve_legacy_current_pointer` and its call site) is a separate commit, owed now that this
write has landed.
