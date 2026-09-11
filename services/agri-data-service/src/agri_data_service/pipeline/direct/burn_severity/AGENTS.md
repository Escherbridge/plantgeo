# MTBS product repair boundaries

The parent `direct/AGENTS.md` records the source-direct writer and current-capture
contracts. Current snapshot v1 is deliberately limited to the 2018–2026 population;
`older_capture.py` and `older_recovery.py` cannot widen its descriptor, staging queue,
publisher or serving eligibility.

## Older recovery preparation

Offline older preparation and current rollback replay open an in-memory DuckDB
connection with extension autoinstall and autoload disabled. They load the already
installed spatial extension before any candidate work and fail immediately if it
is unavailable. That exact loaded connection is passed through current-capture
preparation and every older rung, avoiding the shared deriver's install-on-missing
fallback. The optional connection argument leaves existing current-snapshot
callers unchanged. A missing-extension regression verifies that neither replay
path issues `INSTALL` or creates candidate output.

The September 11 inventory records 3,077 fires from 1984–2017 inside
[-125,42,-111,49]. This count is a dated observation, never an acceptance constant.
The recovery capture independently inventories all 34 years, obtains geometry in
25-row pages, then rechecks each count and complete attribute inventory. It uses the
existing fixed Forest Service URL and public GET transport. The current transport's
2,000-row per-year inventory, 400-request, 20 MiB per-response, 400 MiB total source
and 600-second limits remain in force. The isolated total row limit is 4,000, chosen
September 11 to contain the measured 3,077 rows with a bounded refusal margin. It
does not increase the current publisher's 2,000-row limit.

The source manifest is `mtbs-older-recovery/v1`. Replay checks raw decoded entity
hashes, exact request parameters/order, capture times, count/attribute/geometry
identity, and the canonical source-content digest. A complete capture proves the
bounded query population only. `upstream_population_complete=false` and
`fire_season_completeness=not_assessed` avoid inventing complete-season evidence.
Matching attributes cannot exclude a geometry-only change during a mutable capture.

Preparation uses the ordinary MTBS normalizer, polygon repair and direct derivation.
It retains the registered 23 columns at z13/z9/z5/z0 and splits parts by ignition year
to keep each geometry operation bounded. Every rung keeps each source fire exactly
once. The distinct `mtbs-older-recovery:<manifest-sha256>` identity cannot be admitted
as a current snapshot or an old annual release. Source corrections and withdrawals
require full replacement of the stated older population; an empty valid replacement
must suppress the older population rather than fall back. Availability is the UTC
day after capture closes. Ignition dates, old release announcements, and preparation
time do not backdate these newly observed source bytes. The 600 MiB artifact limit
is inherited from current preparation.

## Local command and preservation packet

`scripts/prepare_mtbs_older_recovery.py --capture --out <new-directory>` performs
only bounded source reads and local evidence writes. `--from-capture <directory>
--manifest-sha256 <digest> --current-capture <directory> --current-prepared <directory>
--current-manifest-sha256 <digest> --out <new-directory>` verifies all older source
evidence, reproduces the current capture and checks its actual saved four rung
blobs, then emits the older candidate and a blocked recovery packet. Both modes have
a child-local 600-second watchdog. There is no apply, stage, bucket, database,
deployment or migration command. Source capture and candidate preparation are
separate invocations so neither phase silently starts the other.

The recovery packet binds exact candidate hashes and current preservation hashes.
It does not prove current production state, durable archival, admission or execution.
Before an operator action it still owes a reviewed older descriptor/catalogue path,
fresh deployed revision/leases/ownership, physical and availability rollback pins,
an explicit collision policy for same-day current and older captures, exact action
authorization, and selected-day/agent-neighbour/all-rung readback. Existing 2018–2026
full replacement semantics, the explicit partial 2023–2026 seasons, and historical
refusal/truncation behavior remain independent invariants. Do not restore an old
PostgreSQL writer or Railway cron during rollback.
