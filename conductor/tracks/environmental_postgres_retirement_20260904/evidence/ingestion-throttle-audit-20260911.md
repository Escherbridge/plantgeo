---
type: operational-audit
status: investigated
observed_at: 2026-09-11T03:37:51Z
---

# Ingestion throttling and missing later days

The reported intake throttle is real in the NASA POWER climate source. At 02:45 UTC
on September 11, the solar-radiation day result reports HTTP 429 at
`na-sample:1deg:p050.00:m113.00` while fetching June 28, 2026. The source deferred
that incomplete day and did not publish an absence. That response proves throttling
for the request; it does not establish a permanent block or identify its quota rule.

The same run reports 761 requests against a 794-request turn budget and ten
availability extensions. Complete per-product log fragments show September 5
publications for temperature, dew point, precipitation, humidity, surface/profile
soil wetness and wind, with 397 source rows each. The aggregate reports ten extensions;
a fragmented root-zone result names outcome published, but this audit cannot independently
attribute an extension or a complete day result to that malformed fragment.
The preceding 01:44 report names September 6 publications;
earlier reports returned all-fill values and refused those unsettled dates.

The raw Railway log contains duplicated/interleaved JSON around transport chunk
boundaries. It is not one valid JSON report. The audit extracted only individually
complete product-result objects; malformed fragments remain preserved and are not
silently repaired. Production physical Parquet objects were not reread by this audit.

## Provider and date semantics

The climate source has four concurrent requests and a per-turn budget; concurrency
is not a request-start rate. It shares one cell-day response across eleven products,
but meteorology and solar use different target days. Solar's configured 75-day lag
places its September 11 planning ceiling at June 28. Meteorology's configured
five-day lag places its ceiling at September 6. These are operator policies; neither
ceiling proves the provider has released complete data through that date.

NASA's [API documentation](https://power.larc.nasa.gov/docs/services/api/) names 429 as
Too Many Requests. Its [request tutorial](https://power.larc.nasa.gov/docs/tutorials/service-data-request/api/)
asks clients not to exceed five concurrent requests and warns that excessive requests
may lead to blocked access. No exact per-second or rolling request quota was established.
The current four-request concurrency is below that documented recommendation, but it
does not guarantee that every request is admitted.

The current climate day code lumps quota refusal into `source_unsettled`, and product
summaries can say `published` even when the chosen day was deferred. A successful
scheduler tick consequently does not prove the product advanced. The code also drops
Retry-After information in its shared bounded HTTP response. These are actionable
diagnostic and pacing concerns, distinct from missing source releases.

## Fresh scheduler checkpoint

The pinned executor is `90251911-636a-4836-9a49-6affc6f2fbd7`, source
`0ae1528ed4655fcd198966877b91abdf9c472f31`. A read-only operational metadata audit at
03:37:51 UTC inspected four indexed checkpoints and at most five work items per run.
It did not query environmental observations, retry a source, or mutate configuration.

| Lane | Recorded state | Implication |
| --- | --- | --- |
| Burn direct forward | September 8 08:55 scheduled bucket, succeeded, one attempt, no last error, zero failure streak | No current burn throttle is established by its checkpoint. Its old five-cohort discovery contract still misses newer partial products. |
| NASA POWER climate | September 11 02:40 bucket, succeeded, one attempt, no last error | Source-level solar 429 is hidden beneath the successful run status. |
| Sensor direct forward | September 9 14:20 bucket, failed, five attempts, held | Still needs the separately prepared evidence correction and governed resume. |
| Signal | September 9 13:00 bucket, failed, five attempts, held | Still needs the separately prepared coordinate repair and governed resume. |

The generic failure text for the two held lanes is `scheduled_command_exit`, status 1.
It is not evidence of a provider quota. The latest deployment's bounded log search
from September 10 16:53 to September 11 03:40 returned no explicit MTBS 429/403 failure.
That does not prove no earlier event or different ingestion surface was throttled.

## Saved evidence and next change

Local raw receipts are `.omc/research/executor-intake-logs-20260911-0330.json`,
`executor-intake-failures-20260911.json`, and `ingestion-live-checkpoints-20260911.log`.
The bounded operational script is `audit-ingestion-checkpoints-20260911.py`.
NASA documentation responses are captured in `nasa-power-quota-docs-search-20260911.json`
and `nasa-power-official-quota-docs-20260911.md`.

The separately verified MTBS checkpoint is local commit
`2a4333716a7018e6393c41a190d5f7254fbd2404`. Its exact committed Python archive
matches full quality receipt digest
`sha256:142a9dc64510af1e96e4c75c3afd5b7a78a7f68295d6cc11e6ce61149c6e2151`
over 1,307 files. It is not deployed or published. The following local batch adds
provider backpressure and climate presentation fixes. Neither this audit nor its
tests authorize publication of the prepared MTBS, sensor, or signal candidates.

## Prepared behavior

- NASA POWER request starts are spaced by 0.5 seconds across a turn, with four
  concurrent requests. This is an operator policy, not an asserted NASA quota.
  One charged start permits one HTTP request; hidden transport retries and redirects
  are disabled for this source. Other bounded HTTP callers keep their existing policy.
- Rate limits, access denial, exhausted local budgets, and all-fill source dates
  have separate outcomes. A deferred selected day makes the product incomplete and
  the report partial; successful cached days may still publish. Ordinary source
  deferral remains eligible for the next scheduled turn rather than creating a held job.
- Valid NASA Retry-After constraints are retained in a checksummed bucket object,
  limited to 4 KiB and three conditional-write attempts. Later constraints win.
  A subsequent turn makes no missing-source request before the saved deadline.
  Invalid or unreadable saved state fails closed; persistence failure is an explicit
  operational error and cannot claim that a durable pause was established.
- MTBS access denial or exhausted rate limiting no longer triggers geometry page
  downshift or a complete source retry. The documented oversized-geometry responses
  retain downshift. Valid 429 waits through 60 seconds are honored fully; longer
  waits defer instead of retrying early. The broader executor retry policy is unchanged.
- Climate legends and hover details share renderer identifiers and show values,
  units, source dates and attribution; contour bands show ranges. Climate queries
  include a bounded neighbor margin at the served cell pitch. Visible coverage is
  counted in the original viewport. A published day without overlapping cells or
  enough contour neighbors stays distinct from an unpublished day.

The 397-cell intake footprint, five-day meteorology lag and 75-day solar lag are
unchanged. Weather aggregate squares remain declared cell footprints. This work
does not establish coverage outside the configured source lattice, station-level
weather locations, or source data that has not been released.

## Verification record

Independent source reviews covered NASA pacing, exact request accounting and
durable cooldowns; MTBS refusal propagation and Retry-After handling; and climate
legends, hover and positive-area viewport support. All were clear before the first
integrated sweep. The sweep found export-list ordering and six frontend failures.
The complete correction batch was independently reviewed: the two z0 fixtures now
use the writer's actual five-degree origin and retain exact geographic assertions;
four query expectations require the intended neighbor margin.

The final full app sweep passed data-boundary, TypeScript, ESLint and tests:
144 test files passed, two skipped; 2,182 tests passed, 13 skipped; 148.84 seconds.
The six test-routing checks also passed. ESLint reported zero errors and 1,045
warnings, including 503 from a temporary extracted copy of the previous service
archive's vendored JavaScript. This was not a warning-free run. The full logs are
`ingestion-final-app-gate-20260911.json` and its per-stage files under local research.
An independent reviewer checked the report against those logs.

The second full Python sweep passed format, lint and mypy but exposed an unchanged
exact floating-point assertion in the real Parquet agent-read parity test:
`4.720000000000001` versus `4.72`. Its 6,029 passing, 149 skipped and one expected-failure
results remain preserved in `ingestion-final-python-release-gate-20260911.log`.
The separately reviewed correction allows only finite weighted means within four
ULPs of the reference, with no relative tolerance. Counts, dates, identifiers,
minimum/maximum values and the separate physical-distance tolerance retain their
contracts. Runtime calculations and publication quality checks did not change.

The subsequent candidate run passed format, mypy and pytest (176.30 seconds), but
lint required the two finite-value assertions on separate lines. That mechanical
correction preserves the comparison and has its own independent review. The final
Python receipt-producing sweep passed format (0.22 seconds), lint (0.21), mypy (2.28)
and pytest (193.94). `ingestion-python-candidate-final-gate-20260911.log` records the
successful invocation. The receipt was generated at 04:26:28 UTC on September 11,
with digest `sha256:c7441adac63cdfe6985763f5abfc5d0d1e28de7db1252aef5cc933c28bd6e9e3`
over 1,310 files. Successful checks must be verified again from an isolated archive
of the committed service before claiming that committed bytes match the checked tree;
`ingestion-release-committed-verification-20260911.json` is the separate local receipt
for that final gate. No local PostgreSQL is used by these checks; database integration
variables are removed rather than set to blank values. Database integration skips
are not evidence that those integrations ran successfully.

Release remains pending. Remote main was last observed at `0ae1528`, and the QA task
has no new production approval. Current MTBS publication, pre-2018 recovery, the held
sensor and signal corrections, and static-soil admission retain their separate
verification and operator steps. This evidence closes none of those work items.
