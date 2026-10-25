# `pipeline/lanes/fire_perimeters/` — the fire-perimeters config-lane strategy package

A NEW package (`s-fire-perimeters`, track `config_driven_ingestion_20260926`, Phase 4): `p4-extract`
moved nothing here because fire-perimeters' modules were not shared with any non-lane caller, unlike
`evacuation_zones`/`watersheds`/`burn_severity`/`drought`. Everything below is authored fresh beside
`pipeline/direct/fire_perimeters/**`, which stays the active legacy implementation
(`executor = "legacy"` in the shipped TOML) until the G6 cut-over (plan Phase 5).

| module | what |
|---|---|
| `wfigs.py` | the strategy (`STRATEGY`): probes `editingInfo.lastEditDate`, fetches and conforms the `_Current` walk, settles and builds rows |
| `support.py` | DuckDB geometry repair to WKB — restated from `pipeline/direct/fire_perimeters/support.py`, byte-identical to its chain |

Imports: `foundation`, `ingest`, `warehouse`, `pipeline/runner/contract.py` and this package only.
Never another layer's `pipeline/lanes/<layer>/` and never `pipeline/direct/**`
(`tests/test_layer_import_contract.py`'s `test_lanes_do_not_import_each_other`).

## Why this strategy carries no watermark ladder

The legacy `pipeline/direct/fire_perimeters/watermark.py` reads the previously published version
back out of the object store, row-count-short-circuits, and content-digests it against the fresh
fetch — all of it hand-built because `resolve_static_lane` and PostgreSQL's `max(updated_at)` gate
are what the generic runner's `static_lookup` handling and S11 rewrite rule do for every OTHER
config lane today. This module leans on both: `probe_edge` answers only "what does WFIGS' own
`editingInfo.lastEditDate` say right now" (`pipeline/runner/AGENTS.md`, "Static lookup"), and
`settle`'s `Written.source_digest` is compared against the last RECEIPT by the runner itself, never
against a re-read partition. See `wfigs.py`'s own module docstring for the full argument and the two
deviations (`fetch` bypassing `ProviderClient`; the restated repair chain) a reviewer should weigh
before G6.

## Compare-step parity (G6)

`feature_id` keeps the legacy writer's `direct:<uniqueFireIdentifier>` namespace, `status` keeps
`"published"`, and `data_available_at` stays `None` — all three byte-identical to
`pipeline/direct/fire_perimeters/rows.py`, because the G6 compare run (`--mode forward --compare`,
plan Phase 5) diffs this strategy's rows against the legacy writer's and must show no unexplained
delta between two implementations of the SAME snapshot, not between two different snapshots.

## What this slice did not port

`pipeline/direct/fire_perimeters/forward.py`'s lane-day locking, four-rung ladder verification,
contention retries and `--republish-current` handling are the generic runner's job for a config lane
(`pipeline/runner/AGENTS.md` "Static lookup", `CA17`) and are not restated here. Geometry repair IS
ported (byte-identical, `support.py`); it is implemented but exercised in this slice only through
`tests/lanes/fire_perimeters/` unit coverage of `plan_requests` / `probe_edge` / `content_digest` —
the real DuckDB round trip over a fetched population is untested here (Wave O rules: no test runs)
and should be a named item in the Phase 4 per-lane adversarial review and the Phase 6 QA round before
G6.
