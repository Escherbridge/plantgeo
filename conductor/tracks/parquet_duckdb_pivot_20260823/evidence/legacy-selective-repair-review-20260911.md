---
type: independent-review
date: 2026-09-11
status: bounded-pass
---

# Independent review of selective legacy repair and weather regression

**Bounded PASS for the reviewed source and reconciliation. No concrete P0–P3
findings remain in this review.** This is a static review before the final
combined quality sweep. It does not claim executed tests, release acceptance,
production recovery, track closure or task archive safety.

Reviewer: `/root/legacy_repair_review`, separate from the implementation author
and reconciliation author. The [machine receipt](legacy-selective-repair-review-20260911.json)
pins every reviewed file, the original author correspondence, the immutable
comparison and the documentation snapshots.

## Source and evidence correspondence

The initial independent check compared all 41 assigned working-tree files with
the author correspondence and with both immutable legacy snapshots and the
preserved candidate `cc64e6e3e1de8dda164beeee0e7e22aa30088b76`. All raw/LF hashes,
byte counts and candidate/legacy-parent/legacy-tip hashes matched. The author
correspondence has SHA-256
`5cfe186e96b198dc27f125b752eb6d7adf055fd911742e0989cc5155632e7c39`.
Twenty-eight files exactly match the legacy tip; thirteen selectively compose
its missing behavior with accepted lane work.

The subsequent weather repair adds three implementation/test files. Root also
adds weather request-binding guidance to map and panels `AGENTS.md`, the only
two files that intentionally differ from the initial 41-file snapshot. Those
additions were reviewed separately and the final 44 unique paths are pinned in
the machine receipt. No implementation was changed by this reviewer.

The complete [legacy reconciliation](legacy-candidate-reconciliation-20260911.md)
was independently checked against Git. Its 139-path net manifest equals the
union of paths touched by all five legacy-only commits. All 417 mode/blob
coordinates match. The actual merge base, 72 identical files, two preserved
deletions, 90 present dispositions, five supersessions and 44 original omissions
are reproduced exactly. The advisor commits have the same stable patch ID;
the two equivalent MTBS commit trees differ only in the historical recovery
document identified by the reconciliation. All six supporting intake receipt
hashes match.

The original 44 omissions are covered by the 41 reviewed repair paths and three
documentation resolutions:

- The 10,635-byte throttle audit is restored exactly from `3a5f3902`, SHA-256
  `14734fe11c98b78221200c604e276cebc60de95c6e2cb011bff5537591fb732e`.
- The current retirement plan links that dated audit and supersedes its old
  candidate/release request with the current integration packet.
- Accepted September 11 sensor revalidation supersedes the old preparation
  statement. The [telemetry preservation](legacy-telemetry-preservation-20260911.md)
  accurately carries the other missing deployment and diagnostic-lifecycle
  paragraphs from the pinned 20,720-byte historical source. Its request counts,
  timings, deployment IDs, CPU limitations, deactivation and pending-patch
  qualification agree with that source. Raw historical captures were not
  re-fetched or reverified, and the document says so.

There is no unresolved legacy omission after these reviewed working-tree
resolutions. The original immutable `cc64e6e` HOLD diagnosis remains intact;
the new review resolves its omissions without rewriting that historical result.

## Contract review

| Area | Independent assessment |
| --- | --- |
| NASA requests and budgets | The pacing lock checks monotonic deadline and latched refusal before and after waiting. It charges only permitted starts. NASA uses one transport attempt and no redirects. The tests assert exact start times, unchanged counts on refused starts, and no hidden retry or redirect request. |
| Durable cooldown | Bounded identity/checksum/date validation fails closed. Three CAS attempts retain a monotonic deadline; expired objects remain stored. Refusal is latched before asynchronous persistence. Tests cover a later competing hint, effective retained maximum, malformed/read/CAS failures, later turns making no requests during a pause, and completed positive responses surviving another request's refusal. This is the documented once-per-turn constraint, not a distributed per-request lock or a provider quota guarantee. |
| Publication reporting | Written and governed-absence counts come from actual day outcomes. Unfinished selected work is incomplete/partial. Provider access/rate limits, local request/time budgets and unsettled source data retain separate outcomes through the adapter/finalizer. Tests check zero-write, mixed, full and absence summaries and ordinary deferral exit behavior. |
| MTBS refusals | Access or exhausted throttle refusals survive the generic finalizer and stop nested source retries. Only the documented 413/500 geometry-size statuses downshift pages. Valid longer 429 waits defer instead of being shortened. Capture failure journals retain bounded status/role/time without response bodies or credential headers. Tests assert exact calls, no publication objects and no capture manifest on refusal. |
| Accepted retirement composition | `prepare_capture` still forwards the optional existing DuckDB connection to `prepare_snapshot`; the refusal repair changes only the request diagnostic path. Accepted current-snapshot preparation and derivation seams remain. |
| Climate viewport and terminal state | The read halo uses the served cell pitch. Visible counts and geometry overlap use the original viewport; contour support may use halo rows. Hole and edge-only tests prevent a false visible band. Published-empty results and insufficient contour neighbors stay published with served-day/truncation metadata. Governed absence retains its evidence and terminal state. |
| Reader and renderer preservation | The climate reader delta is only the halo import/call, preserving selected-day, abort and response-identity checks. Shared IDs retain field/isoline-only types and retired point cleanup. Tests assert all four rung footprints, original-view counts, insufficient neighbors, terminal evidence and high-zoom served-form fallback. |
| Climate UI | Legends share palette/units and label the selected display setting. Native disclosure controls expose expanded state, Escape focus return and off/on reset. Hover distinguishes a cell reading from an interpolated color range and suppresses malformed measurements. The tests assert these behaviors rather than only component existence. |

Security, performance, correctness and maintainability review found no concrete
defect in this bounded change. Positive protections include refusal metadata
bounds, finite request/CAS budgets, explicit terminal evidence and preservation
of accepted ownership and rendering contracts. No broader security or performance
certification is implied.

## Weather regression appendix

The three-file weather addition was checked against `cc64e6e` and the actual
point-reader and drawn-day helper contracts. `LayerManager` rejects a settled
envelope whose requested day differs from the weather row. An explicitly marked
placeholder may retain its prior frame and prior-day caption while loading;
the settled missing-day result clears it. `FireDetails` requests the same weather
day and accepts numeric cards only when the response's real
`proximity.requestedDay` matches. The point reader populates that field. Fire
summary requests remain on the independent fire date.

The new combined component regression asserts populated `2026-08-01`, pending
`2025-04-28` with an honestly captioned retained map and cleared cards, the landed
missing day, a delayed populated old answer rejected by both map and cards, and
an explicit return that restores the earlier day. It also checks both weather
query dates, the unchanged fire date `2026-07-30`, and forecast horizon zero.
These are statically reviewed assertions, not a report that the test passed.

The [Wind & Weather handoff](wind-weather-handoff-20260911.md) and all four owner
plans retain the sampled estimates/observations identity, horizon zero, exact
recorded history gap, source/floor/lag reconciliation and sparse-support limits.
They prohibit filling current-poll history with reanalysis under the same
identity. The local regression does not establish missing historical coverage
or deployed map/details agreement.

The final combined source still needs the root-owned boundary, type, lint,
frontend and full Python receipt sweep, followed by exact-tree/closure review.
No tests, builds, commits, production actions or task archiving were performed
by this reviewer.
