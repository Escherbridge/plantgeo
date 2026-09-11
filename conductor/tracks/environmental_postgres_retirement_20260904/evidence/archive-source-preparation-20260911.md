---
type: evidence
---

# Bounded FIRMS and NWIS source-only archive preparation — September 11

Status: local implementation and behavioral tests accepted by independent review and the scoped
verification recorded in the [final repair review](independent-repair-review-20260911.md). No
production mutation, deployment, publication, absence decision, availability change, database
write, migration, deletion or executor allowlist change was performed by this work.

## Current chain and ownership boundary

`ingest/commands.py::run_archive_slice` still binds `FeatureWriter` and places its callback in
`ArchiveWalkContext`; `ingest/archive_walk.py::walk_archive_chunk` calls `run_source_backfill`.
These shared command/execution paths remain owned by `gapless_parquet_publication_20260901`.
Their retained source-only `ingest/firms.py` and `ingest/usgs_nwis.py` fetch/parse APIs and pure
normalizers can supply the new product preparation packages without writing observations.
This change does not redirect old callers or imply retirement of their enabled ledger definitions.

The new exclusive product files are `pipeline/fire_detections_recovery/{models,prepare}.py`,
`pipeline/water_gauges_recovery/{models,normalize,prepare}.py`, their directory documentation,
two `scripts/prepare_*_recovery.py` CLIs and matching `tests/parquet/test_*_recovery.py` files.
Existing forward ownership floors stay FIRMS 2026-08-25 and water-gauges 2026-09-02.

## Reviewable candidates and capture facts

Each CLI consumes an independently pinned `source-manifest.json` plus complete content-addressed
source response bytes and produces a new local directory containing unchanged source bytes,
all four registered-schema Parquet rungs, per-part complete SHA-256/length/count, and a hashed
candidate packet with `apply=false`. Source capture clocks remain separate from historical
observation timestamps. Full response hashes are distinct from remote range probes. Operator
capture assertions are not promoted to upstream population proof.

FIRMS preparation requires one exact acquisition day and every product applicable according to
the captured official availability table, capped at eight requests, 16 MiB per product, 64 KiB
availability, 50,000 raw detections and ten minutes of capture time. It refuses malformed or
out-of-scope rows, missing products and conflicting equal-priority identities. It preserves the
existing standard-processing-over-NRT rule after conflict validation. Quoted source numeric and
confidence cells are decoded before passing to the retained pure parser. A full source day is
aggregated once and all rungs conserve its deduplicated detection count.

NWIS preparation accepts 1–15 days, at most 16 canonical request tiles, 8 MiB per complete response,
64 MiB total, 50,000 raw readings and ten minutes of capture time. It validates `00060` discharge,
daily mean statistic `00003`, `ft3/s`, source standard-time offset, publisher midnight/day, finite
coordinates and values, every requested tile and every requested day per returned series. Real
negative discharge remains a value; `-999999` remains an explicitly counted missing reading.
Identical duplicates may collapse; conflicting source support, qualifiers or values refuse the
unit. Malformed/empty or undercovered days cannot become a successful empty data candidate.

The initial source inventory before the 18:35Z probe found no supplied/saved FIRMS or NWIS response
bundle in the current `.omc/research` directory.
No FIRMS credential was read or emitted and no credential-bearing FIRMS request was attempted.
The initial authorized read-only NWIS probe requested a single historical day (2022-08-05), discharge
daily means and a small Sandy River bbox `[-122.2,45.3,-122,45.5]`, without an active-only restriction.
It ran with the tool's default restricted networking, bounded to one request, 2 MiB, a 20-second
HTTP timeout and 30-second wall guard. It failed
with `ConnectError` after about 0.593 seconds at 2026-09-11T18:35:43Z; no response bytes or source
population were obtained. Receipt:
`.omc/research/nwis-daily-capture-20260911/capture.json`, preserved byte-for-byte alongside this
packet as `nwis-archive-source-probe-20260911.json`. This is a connectivity result, not an
upstream absence or evidence that the endpoint has retired. The captured official documentation
search is `.omc/research/nwis-daily-values-docs-20260911.json`; primary service semantics are at
<https://waterservices.usgs.gov/docs/dv-service/daily-values-service-details/>.

The authorized follow-up inspected that launcher, receipt and original tool invocation, confirmed
the default restricted-network execution and failure before response headers or bytes, and repeated
the exact public request **once** using `require_escalated`. This request retained the same day,
bbox, discharge parameter, daily mean statistic and stream-site filter, used no credentials or
active-only site restriction, disabled redirects, and enforced a 60-second overall deadline and
8 MiB response cap. It succeeded with HTTP 200 and `application/json`, identity encoding, at
2026-09-11T19:09:06.158852Z–19:09:07.053523Z, taking approximately 0.906 seconds. The complete
response was 11,815 bytes. This successful same-request comparison supports a restricted-network
boundary explanation for the original failure; its original receipt recorded only `ConnectError`,
so it does not independently identify an OS-level transport cause. The endpoint did answer the
approved follow-up; the first attempt is not retained as evidence of endpoint unavailability.

Both attempts remain separate. The new tracked evidence files are:

- `nwis-archive-approved-probe-20260911.json`: sanitized UTC/request/status/bounds/complete-response
  receipt; SHA-256 `47e6f7cd836954917b8a1f18364c34d45bc0ec94d8693fdabd1bce3405d7fcea`.
- `nwis-archive-public-response-20260911.json`: unchanged complete response bytes;
  SHA-256 `2bc2fcd58e7730ced0fce99c9bd29cbd7e8797a734c9d199cee4dd73e3ec9177`.
- `nwis-archive-public-response-summary-20260911.json`: bounded local inspection tied to both
  preceding full hashes; SHA-256 `bffa381a16ed3cb06151b45722ad2c9cde9a6416496924e3693bab43defe955d`.

The original failure receipt remains `nwis-archive-source-probe-20260911.json`, SHA-256
`74f7df9cdcce3f1035947e4853679a91576ab7921ac4d7ac8ef4ef46fdd0caba`. Local originals for the
successful request are under `.omc/research/nwis-daily-capture-approved-20260911/`.

The saved successful JSON contains **seven sites, seven time series, seven value blocks and seven
readings**. Every returned series declares parameter `00060`, daily mean statistic `00003`, units
`ft3/s`, publisher day `2022-08-05`, and source standard-time offset `-08:00`; each timestamp is
`2022-08-05T00:00:00.000`. Local inspection counted zero empty series, empty value blocks,
empty/missing values, sentinel readings, malformed/non-finite numeric readings, missing timestamps,
identical duplicate grains or conflicting duplicate grains. Returned qualifiers are `A` on all
seven readings. These are counts of this exact saved response, not a canonical-AOI census or proof
of complete historical site population. No additional request, replay preparation, test or
publication was performed for this follow-up.

No real historical archive candidate exists yet. The behavioral tests use clearly synthetic
captured-response fixtures; they are not a source census or a claim of history restoration.

## Exact generic integration proposal

1. Replace the old archive command's observation-writer coupling with product source capture and
   replay callbacks. The generic owner retains job definitions, historical cursor, bounds,
   progress/error states, exclusive lane-day ownership, retries and queue admission.
2. Capture a complete bounded request unit first, including expected day(s)/AOI, product or tile
   population, full response bytes, status/completion and actual UTC capture clocks. An external
   trusted receipt must bind these response identities to the actual request scope; local manifest
   consistency alone cannot prove transport origin or a full governed AOI.
3. Feed that pinned packet into the relevant product preparer. Its four-rung local candidate is
   read-only review evidence. A small AOI or incomplete historic population is not a whole-day
   replacement merely because its tables are valid.
4. Pin the prior complete native/coarse ladder, absences and availability generation. FIRMS needs
   a full-day identity comparison before replacing aggregates, since adding historical cell
   totals can double count. NWIS needs native-grain preservation plus an explicit daily-mean versus
   instantaneous support decision; the current schema cannot distinguish their statistics in a
   shared grain. Reuse `merge_water_gauges_day` only after that semantic conflict is resolved.
5. Under the current owner barrier, write all four immutable rungs, validate readback and use the
   shared terminal/availability finalizer. Preparation does not supply this admission operation.
   Verify reader and selected-day/neighbor parity before disabling a remaining writer or queue.

Rollback packet for either product: retain candidate/source bytes; restore the pinned prior
complete ladder plus its absence and availability generation under the same exclusive barrier.
No admission exists to roll back in this implementation; no prior production reference changed.

## Verification handoff

The two new archive test files passed in the integrated Python fallback; full Python static and
repository dependency/boundary gates also passed. The independent review records the later
affected retry for the two unrelated residual failures and the full scope limits. The source tests
exercise every rung's exact Arrow schema, full-hash tampering, source malformed/undercovered and
conflicting cases, quoted FIRMS values, standard-processing precedence, NWIS daily support,
negative values/sentinel separation and preserved publisher day. No test/lint/type command was
run by this authoring lane before this handoff.
