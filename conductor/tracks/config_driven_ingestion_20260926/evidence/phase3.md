---
type: evidence
track: config_driven_ingestion_20260926
phase: 3
updated_on: 2026-10-01
---

# Phase 3 evidence

## Dark implementation and deployment

| row | evidence | result |
| --- | --- | --- |
| implementation | commits `853b4c99` and `a7906d6c` | Modern USGS daily lane, registration and read-switch preparation landed. `enabled=false`, `gap_fill_enabled=false`; web and agent still serve legacy water. |
| prior validation | Claude trace `629f5259-61fa-476f-b05b-5b6df325ad4d.jsonl`, original lines 13962 and 13990 | Trace records the web gates and Python receipt sweep before the September 29 UTC push. Historical evidence only; not a fresh check of the current recovery batch. |
| deployment verification | Railway production read, October 2 02:09 UTC (October 1 local) | Main `eb784b4b-32ed-45a8-96ec-bfe6c6874198`, Parquet `c350407b-e95c-48ae-bf86-e5dd840c9757`, executor `156e5f92-5438-4227-8af9-e28aff0973fd`: SUCCESS at `a7906d6c`. |
| live serving | Public application probes saved in `.omc/research/receipt-investigation-20261001/` | 23 date capabilities returned. Bounded viewport water read: 20 rows; temperature: 4 cells. Dark daily-water lane is not yet a served or fully ingested corpus. |
| modern / legacy source parity | October 2 02:22 UTC, ten gauges named in `docs/lanes/water-gauges.md` §8.1, September 20–24 | Both APIs returned 200; all 50 site/named-day/value pairs matched, with no duplicate modern identities or next page. Captures and request receipts: `.omc/research/receipt-investigation-20261001/water-validation/`. Source parity only; this does not prove the new persisted stream or forward/gap-fill equality. |
| G3 / G4 | shipped TOML and stream constants | Not executed. History-depth and 72-hour live-forward requirements remain open. |

## Recovery review, October 1 local

A separate read-only adversarial review found two blockers before G3:

| finding | demonstrated failure | disposition |
| --- | --- | --- |
| W3-R1, HIGH | Gap-fill only revisits missing/incomplete physical ladders. A historical day published from seven of eight tiles has a full ladder, so its missing source tile disappears from the repair backlog. Forward's 550-day revision window cannot reach 1990. | Implemented durable source-completeness debt distinct from ladder debt, with fresh-turn historical recovery regression; independently reviewed. Execution checks below. |
| W3-R2, MEDIUM | Count-only partial rewrite admission accepts seven answered tiles over six even if the seven omit a tile previously published. | Implemented coverage-identity proof and interruption-safe pending receipts, with shifted-failure regressions; independently reviewed. Execution checks below. |
| readiness evidence | No tracked Phase 3 evidence or review verdict existed although the dark implementation was deployed. | This file records baseline, findings, subsequent checks and the gate boundary. |

No author runs tests. All fixes precede the separate review and integrated sweep; results below remain unproven until observed.

## Related serving and source findings

These production failures were identified while recovering the config-driven work. They do not justify skipping G3/G4 or substituting a source day basis.

| row | evidence | implication |
| --- | --- | --- |
| shortwave stalled frontier | Two October 2 UTC executor turns, 00:48 and 01:47: 397-cell fan-outs on September 26/25 return all-fill or exhaust budget; zero shortwave rows; backlog 88. | The legacy newest-first selection repeats unready days. Existing spec §2 already records the UTC/LST mismatch and FR-5 requires source-edge gating. |
| current UTC / LST probe | October 2 02:15–02:16 UTC, NASA POWER daily point, Seattle (47.6, -122.3), `ALLSKY_SFC_SW_DWN,T2M`, May 1–October 1: UTC solar last valued June 30; LST solar last valued September 27. Three September-only UTC probes (Seattle, Boise, central PNW) returned no solar values. Raw responses and parameters: `.omc/research/receipt-investigation-20261001/source-edge/`. | A six-day solar lag measured without explicit UTC is not the current UTC publication edge. This is a sampled provider-edge measurement, not a whole-region completeness proof. Preserve UTC; fan-out still proves source support and absence. |
| shortwave serving refusal | Availability source ceiling September 14 precedes October 2 freshness minimum September 16. Public September 14 read returns 503. | Historical refusal follows the existing freshness contract. Scheduling repair alone cannot provide recent UTC measurements absent upstream; the planned settled/provisional successor remains required. |
| soil survey | Capability reports `lane_never_written` for the retired generic lane; admitted-release reader returns 22 polygons at zoom 13, and `soil_survey_zoom_in` at zoom 8. | Status must use admitted static-release proof, preserve no date slider and detail-only rendering, and distinguish unavailable proof from never published. |

## Current recovery batch

- Shortwave: bounded UTC edge measurement and an oldest-first eligible walk; no fabricated data, time-standard switch or freshness bypass.
- SSURGO: bounded admitted-release status used by capability/UI evidence.
- Runner/water: durable source-unit debt and coverage-preserving partial publication.

The local G3 proposal also enables the modern water lane and its gap-fill, preserving legacy
serving. These local configuration edits are preparation for review and the receipt sweep;
they do not record owner authorization, a push or a live activation.

| author prediction | expected check impact | disposition |
| --- | --- | --- |
| SSURGO release status | No known semantic failure; new Python test literals may need formatting. Index proof intentionally does not certify viewport geometry. | Authoring stopped; independent review approved after body-fault fix. |
| Legacy UTC shortwave edge | Existing generic skip tests remain meteorology-only; solar fixtures now include the bounded edge probe and its three-request reserve. No known semantic failure reported. | Authoring stopped; independent review approved after anchor fix. |
| Runner source completeness and G3 proposal | New receipt/summary contracts, historical recovery and shifted-tile regressions; activation changes the explicit gap-fill allowlist and shipped-lane dispatch expectation. | Authoring stopped; independent review approved after interruption and forward-ladder fixes. |

Authors ran no tests, lint, type-check or builds. After independent review approval, the
separate monitor normalized changed Python formatting, staged the exact service input paths,
and started the integrated Python and web sweeps. Check results, quality receipt and production
authorization remain pending. This batch does not establish Phase 3 completion or authorize
the G4 serve switch.

## Independent recovery reviews

| slice | verdict | finding and disposition |
| --- | --- | --- |
| SSURGO status | APPROVED after fixes; checks pending | A deadline or dropped connection during the status response body escaped the bounded-fetch error taxonomy and could reject healthy day capabilities. The author normalized recognized body failures while preserving caller cancellation and unrelated programming exceptions; controlled-stream regressions authored. The independent reviewer re-read the fix and approved the complete slice. |
| Legacy shortwave | APPROVED after fixes; checks pending | Two oldest all-fill gaps without a later persisted publication consumed 3 + 397 + 397 requests on every fresh turn, despite the probe showing later valued dates. The full-fetch publication anchor and four-process restart regression resolve this case and passed independent re-review. Probe evidence alone remains insufficient for absence. |
| Water / runner / proposed G3 | APPROVED after fixes; checks pending | Historical source debt and shifted coverage are addressed. A durable pending receipt now records intended coverage before partition mutation, refusing partial recovery after interrupted publication. Forward discovers and repairs ladder debt independently of source-digest changes. The independent reviewer approved both fixes and their exact regressions. |

The earlier framework workflow's two review lanes and fix pass are recorded in Claude trace
`629f5259-61fa-476f-b05b-5b6df325ad4d.jsonl`, source lines 12928–13284, and the deployed
commit is `e605b1fe`. The track's missing Phase 1/2 evidence and contract-freeze files must not
be treated as fresh validation of the recovery changes or as proof that later gates ran.

## Prepared G3 release scope

Owner authorization received during this continuation: "make sure to push up and verify prod
as you go". The reviewed repair and G3 activation batch may be pushed after the final checks
pass, followed by production verification. The owner capped this continued run to two further
hours, ending October 2 at 05:06:13 UTC. The full-history and 72-hour G4 prerequisites remain
in force.

Read-only Railway status, deployment history and service-config captures are saved as
`.omc/research/receipt-investigation-20261001/railway-pre-gate-*.json`. No environment changes
are staged there. The live deployment remains `a7906d6c` for main, Parquet API, executor and
Martin, all SUCCESS. The proposed web/service change set matches the watch configuration for
those four services. ML watches `/services/plantgeo-ml-service/**`, and strategy knowledge
watches `/services/strategy-knowledge/**`; this batch touches neither.

The activation proposal retains the legacy stream for web and agent reads. Its new modern
daily stream must still prove persisted source parity, forward/gap-fill equality, named-day
semantics, no source or ladder debt back to September 30, 1990, and at least 72 hours of clean
forward operation before the separate G4 request. A green local sweep will not satisfy those
live acceptance rows.

## Integrated verification, first sweep

The nonauthor monitor ran the full Python receipt command and every required web gate after
the review fix batch. It did not restart tests or apply semantic fixes between gates.

| gate | result | evidence / disposition |
| --- | --- | --- |
| locked Python dependencies | PASS | `uv sync --locked --all-extras`: 93 resolved, 91 checked. |
| Python format | PASS | Changed Python files normalized by the monitor before staging; format check passed. |
| Python lint | FAIL, 3 findings | Shortwave probe return count, literal HTTP 429 in its test, and six-argument water test helper. Batched author fixes pending re-review. |
| Python types | PASS | Full mypy gate passed. |
| Python tests | FAIL: 6,492 passed, 1 failed, 97 skipped, 1 xfailed | Shifted-tile refusal regression exposed report sorting of same-day tuples whose stream is a string or `None`. Fixing deterministic optional-stream ordering without dropping report evidence. |
| web boundary / types / lint | PASS | All three full gates passed. |
| web tests | FAIL: 3,533 passed, 1 failed | A generic test still required observation dates for SSURGO. Corrected to assert its separate snapshot/null-date/release-proof contract; daily assertions remain intact. |
| web production build | FAIL, local dependency installation | Installed `@tailwindcss/oxide` 4.2.2 lacked its declared entry files. Monitor restored only missing files from the exact lockfile tarball after SHA-512 verification; package and lockfile hashes stayed unchanged. Build retry pending. |
| quality receipt | REFUSED, correctly | No failed sweep was certified. Previous receipt dated September 29 remains unchanged. |

Logs and locked dependency repair evidence are under
`.omc/research/receipt-investigation-20261001/quality/`. The failed gates do not authorize a
push. All discovered fixes are being batched before the next integrated verification pass.

## Final integrated verification

The three author fix batches passed separate re-review before the monitor started the final
full sweep. No implementation bytes changed during that run.

| gate | result | evidence |
| --- | --- | --- |
| Python format / lint / mypy / full pytest | PASS | Full receipt command: 2.24 s / 0.24 s / 5.76 s / 509.83 s. The passing wrapper prints gate results rather than pytest totals; no final count is inferred. |
| web data boundary / types / lint | PASS | Full gates; lint reports zero errors and 554 warnings. |
| web tooling / full Vitest | PASS | 12 tooling tests; all 3,534 Vitest tests across 241 files. |
| production web build | PASS | 54.9 s; verified locked oxide restoration resolved the local build defect. |
| fresh Python quality receipt | PASS, independently verified | Generated `2026-10-02T03:11:05.257983Z`; `sha256:c5f08a9e2512f22dbf10aa91dbaa3df86e364c961dee5d5622eaa62d2ce02c7c`, 1,164 input files. |

Final logs and result records are in
`.omc/research/receipt-investigation-20261001/quality/final/`. The owner-authorized commit and
push follow this verified state. Production deployment identity, serving probes and G3 runtime
evidence are still to be recorded; the green local sweep does not close those rows.
