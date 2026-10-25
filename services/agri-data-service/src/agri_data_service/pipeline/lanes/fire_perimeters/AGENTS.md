# `pipeline/lanes/fire_perimeters/` — the fire-perimeters config-lane strategy package

A NEW package (`s-fire-perimeters`, track `config_driven_ingestion_20260926`, Phase 4): `p4-extract`
moved nothing here because fire-perimeters' modules were not shared with any non-lane caller, unlike
`evacuation_zones`/`watersheds`/`burn_severity`/`drought`. Everything below is authored fresh beside
`pipeline/direct/fire_perimeters/**`, which stays the active legacy implementation
(`executor = "legacy"` in the shipped TOML) until the G6 cut-over (plan Phase 5).

| module | what |
|---|---|
| `wfigs.py` | the strategy (`STRATEGY`): probes `editingInfo.lastEditDate`, fetches and conforms the `_Current` walk, settles and builds rows |
| `support.py` | DuckDB geometry repair to WKB — restated from `pipeline/direct/fire_perimeters/support.py`, byte-identical to ITS chain specifically (the pass-through-and-flag variant; `burn_severity/support.py`'s always-repair variant is a DIFFERENT chain — see `support.py`'s own module docstring, corrected after ring review) |

Imports: `foundation`, `ingest`, `warehouse`, `pipeline/runner/contract.py` and this package only.
Never another layer's `pipeline/lanes/<layer>/` and never `pipeline/direct/**`
(`tests/test_layer_import_contract.py`'s `test_lanes_do_not_import_each_other`).

## Why this strategy carries no watermark ladder, and where that costs something (H1)

The legacy `pipeline/direct/fire_perimeters/watermark.py` reads the previously published version
back out of the object store, row-count-short-circuits, and content-digests it against the fresh
fetch — all of it hand-built because `resolve_static_lane` and PostgreSQL's `max(updated_at)` gate
are what the generic runner's `static_lookup` handling and S11 rewrite rule do for every OTHER
config lane today. This module leans on both: `probe_edge` answers only "what does WFIGS' own
`editingInfo.lastEditDate` say right now" (`pipeline/runner/AGENTS.md`, "Static lookup"), and
`settle`'s `Written.source_digest` is compared against the last RECEIPT by the runner itself, never
against a re-read partition.

**But S11 is never reached on a forward turn `turn.py::_static_forward`'s day-only gate already
calls current** (corrected after ring review; an earlier revision of this file implied S11 covers
the same ground the legacy ladder did, which overstates it). The gate is `served_day >= watermark_day`
— a bare `date` comparison — so a SECOND genuine WFIGS edit landing on the SAME UTC day
`editingInfo.lastEditDate` already named is masked until the day rolls over, for up to ~24 h on an
hourly cron, with S11 never even consulted. This is the same class of gap
`docs/lanes/watersheds.md` §8 item 1 names for the sibling static-lookup lane in this track; it is
pinned here too (owner register row `static_freshness_contract`) rather than silently inherited.
See `docs/lanes/fire-perimeters.md` §8 and `wfigs.py`'s own module docstring for the full writeup,
and `tests/lanes/fire_perimeters/test_wfigs.py`'s same-UTC-day pinning test for the mechanism made
concrete.

## Known residuals (H1/H2/H3) — stated, not fixed in this slice

None of the three below can be closed from inside this dark, read-only-reviewed slice; each is
pinned here, in `wfigs.py`'s own module docstring, and in `docs/lanes/fire-perimeters.md` §8 so the
Phase 4 adversarial review and the Phase 6 QA round inherit them explicitly rather than discover
them at G6.

- **H1 — same-UTC-day re-edits are masked until rollover.** See the section just above.
- **H2 — compare parity with the legacy writer is NOT achievable as designed, and MUST NOT be
  attempted before a contract fix lands.** An earlier revision of this package's own report claimed
  `--mode forward --compare` would show zero unexplained delta because `feature_id`/`status`/
  `data_available_at` are kept byte-identical to `pipeline/direct/fire_perimeters/rows.py` — that
  claim was REFUTED on re-read and is corrected, not repeated, here. Two problems survive those
  three constants matching: (1) a full-row digest still includes `updated_at`, each writer's OWN
  capture instant, so an identical population still diffs on every row between any two independent
  fetches; (2) the two writers resolve DIFFERENT days for the same version — this strategy's day is
  `editingInfo.lastEditDate` (§7's named watermark), the legacy writer's is
  `source.fetched_at.date()` (whenever ITS turn happened to run) — and those two clocks need not
  agree, especially across a UTC-midnight boundary. The fix needs a volatile-column-aware compare
  AND day alignment, owned by neither writer alone; `--compare` should not be run for this lane
  until it exists.
- **H3 — `editingInfo.lastEditDate` has never been read against the live service, and a wrong
  assumption here is a SILENT failure mode.** `lanes/_providers/nifc-wfigs.toml` already carries a
  `TODO(s-fire-perimeters)` saying so. If the field does not behave as `probe_edge` assumes,
  `probe_edge` answers `invalid`/`blind` every turn, the gate holds the window every time (O-R3-1: a
  probe that cannot answer never fails the run), and the lane exits 0 while publishing nothing,
  forever — indistinguishable in a log from a genuinely quiet WFIGS day. `docs/lanes/
  fire-perimeters.md` §8 carries the G5-blocking live-probe checklist line and a one-paragraph
  content-digest fallback sketch (precedent: `pipeline/lanes/evacuation_zones/watermark.py`) for if
  the field proves unusable.

## Compare-step parity (G6) — REQUIRES the H2 contract fix; do not attempt before it lands

`feature_id` keeps the legacy writer's `direct:<uniqueFireIdentifier>` namespace, `status` keeps
`"published"`, and `data_available_at` stays `None` — all three byte-identical to
`pipeline/direct/fire_perimeters/rows.py`, matching three of the facts a comparison needs. They are
NOT sufficient on their own: see H2 above for the two problems (a volatile `updated_at` column, and
two writers resolving different days for the same version) that a byte-identical set of constants
cannot close.

## What this slice did not port

`pipeline/direct/fire_perimeters/forward.py`'s lane-day locking, four-rung ladder verification,
contention retries and `--republish-current` handling are the generic runner's job for a config lane
(`pipeline/runner/AGENTS.md` "Static lookup", `CA17`) and are not restated here. Geometry repair IS
ported (byte-identical to its own variant — see `support.py`'s module docstring); it is implemented
but exercised in this slice only through `tests/lanes/fire_perimeters/` unit coverage of
`plan_requests` / `probe_edge` / `content_digest` — the real DuckDB round trip over a fetched
population is untested here (Wave O rules: no test runs) and should be a named item in the Phase 4
per-lane adversarial review and the Phase 6 QA round before G6.

**The per-incident repair detail (L3, restored after ring review) is computed but not reported.**
`FirePerimeterSnapshot.rows` carries `GEOMETRY_REPAIRED_KEY`/`GEOMETRY_REPAIRED_AREA_CHANGE_KEY` per
row again (`wfigs.py`, matching `pipeline/direct/fire_perimeters/rows.py:319-320`'s legacy keys),
and `FirePerimeterSnapshot.repairs` exposes them exactly as the legacy writer's own `.repairs`
property does. What does NOT exist is anywhere for a per-incident area-change list to surface in
the generic runner's S5 report the way the legacy writer's terminal report and
`fire_perimeters_forward_geometry_repaired` stderr event do — `Written.dropped_rows` means rows
never written, not rows written-but-repaired, so overloading it would be actively misleading, and
this slice has no other report hook to add one to. An aggregate repaired-count surfacing in the S5
report (not just the per-row property this slice restored) is a fair ask for a future revision of
the runner's report contract, not something fixable from inside one lane's strategy.
