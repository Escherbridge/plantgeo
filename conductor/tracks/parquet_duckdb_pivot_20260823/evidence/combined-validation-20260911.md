---
type: validation-evidence
date: 2026-09-11
status: complete
---

# Frozen integration candidate: local verification

Candidate `89e8494422b8232c8f16dbffdcf2321c7ea17bc8` (tree `777fe20689dd68c337677cc49e3fd4e63bfd4bcc`) passed all 16 local gates.
This is the six-lane/offline integration plus reviewed selective legacy repair,
the existing-reader weather gap correction and planning-only forecast tracks.
Botanical runtime is outside this frozen result and requires a later combined sweep.

| Check | Result |
| --- | --- |
| Data boundary, TypeScript and ESLint | PASS; lint has 0 errors and 542 warnings. |
| Frontend Vitest | 2,260 passed, 13 skipped; 145 test files passed and 2 skipped. |
| Frontend Node tooling pretest | 6 passed, no failures or skips. |
| Python full format, lint, mypy and pytest | PASS; 6,272 passed, 149 ordinary skips, 1 expected failure, zero failures/errors. |
| Python receipt and archived-source receipt verification | Both PASS. |
| Local web, data-service and job-executor images | All three builds, bounded smoke checks and image identity captures PASS. |

The Python receipt binds `sha256:5bd47b38781161e3bd35e22ff38cb58a0a826516477ba4460dd4e7c5716bdfce` over 1,359 files.
The 149 ordinary Python skips comprise 141 database integration or rehearsal
cases, five capability or retired-lane cases, two live LLM probes and one SQL
packaging fixture. The expected failure records the 18 owner-contingent CLI
transaction boundaries outside conformity C2. Frontend skips comprise nine
climate SQL-contract cases and four PostGIS spatial cases.

All four optional database test environment variables were removed from child
environments; these results do not establish database or live-service acceptance.

## Report-path recovery

The original wrapper exited after all six host checks passed because shell-like
parsing of an unquoted Windows path placed the JUnit report under a malformed
relative filename. An independent reviewer verified the report, original runner,
six gate logs and source state. The continuation moved only that generated report
without changing its bytes and then passed the source guard and remaining image
gates. The original interruption and continuation are retained in the machine receipt.
No completed host gate was repeated. The web Dockerfile performed its normal
embedded boundary/type/lint/test checks during image construction.

## Local image identities

| Image | Local image ID |
| --- | --- |
| web | `32c5b47e63bafa03e527e17ae7afb2587f5093937a36afa6336f0b31b18e71c9` |
| data-service | `0a6191c96386741ff1fcb69553e5e82c804ab4b4ba082d6bf42ceb425096707a` |
| job-executor | `026002869ce39ade5a1af70b36ba8ad64f5eaf0651738b16ab8fa293ebba2608` |

Images use an archive of the exact candidate plus its verified Python receipt.
Web smoke checks only the syntax of `server.js`; Python smokes invoke
`agri-service --help`. All smoke containers use `--network none`; no scheduler,
migration, upload, publication or production action runs.

The [machine receipt](combined-validation-20260911.json) pins every raw and gzip
artifact, command, exit, duration, runner and independent continuation review.
The [release packet](release-packet-20260911.md) remains HOLD for production.
Scoped shrink closure and archive recommendations await the final integration
review and the subsequent botanical candidate requested by parent QA.
