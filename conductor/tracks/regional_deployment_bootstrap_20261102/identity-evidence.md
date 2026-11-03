---
type: validation-report
---
# Identity and admission implementation evidence

Work starts from foundation checkpoint `23b5fa60` on `data/regional-bootstrap`. This report describes a validated local checkpoint, not a deployed release or completed regional bootstrap.

## Scope

Python introduces shared canonical identity vectors, separate geographic/definition/stream-support digests, immutable geographic namespace reservation and pre-operation verification, request/response identity headers, and explicit refusal of unadmitted custom acquisition. Web work consumes the same protocol and isolates serving caches, persisted data and operational cache state by definition. Source-specific admission and regional bootstrap remain subsequent required work; a namespace receipt alone must never authorize observations or governed absences.

## Review findings and disposition

- Accepted: normalize equivalent signed-zero geographic definitions in receipt bytes, not only their hashes. Receipt generation now uses canonical geography token hex and enforces its byte cap before creation.
- Accepted: regenerate Unicode vectors using escaped/codepoint inputs, preventing Windows shell encoding from replacing intended multibyte cases with question marks.
- Accepted: refuse unadmitted custom executor work before provider access, including legacy scheduled dispatch. Public CLI inspection found no generic exposed ingestion group requiring an unrelated rewrite; internal direct legacy entry points remain unsupported until source admission covers them.
- Accepted independent review: namespace transport/read/cleanup failures need sanitized errors while preserving the primary refusal. Corrected, independently reinspected and covered by the passing full Python gate.
- Accepted independent review: namespace checks must use bounded worker admission that retains capacity until the underlying worker finishes after cancellation. Corrected using existing `run_bounded_read` without opening DuckDB, and independently reinspected. Regression covers a blocked one-slot worker, requester cancellation, prompt refusal of another request, and eventual slot reuse.
- Accepted independent web review: scope checks must occur after asynchronous IndexedDB generation checks and after cold-path persistence, before publishing or returning results. Corrected with tests delaying those later boundaries, and independently reinspected.
- Accepted independent web review: rejected browser responses must have unread bodies cancelled without masking the identity refusal if cancellation fails. Corrected with best-effort cancellation and stream tests, and independently reinspected.
- Accepted root review: every tRPC response path, including body-size refusals, must state its actual definition so a custom request error is not misreported as missing identity. Corrected and independently reinspected, including 413 and in-flight 409 regressions.

The backend author and reviewer are separate. Runtime authorship remains serial. This is targeted follow-up review, not an additional dev-drive cycle.

## Validation

The full web gate passed boundary, type checking, lint (zero errors, 542 baseline warnings) and 252 test files / 3,635 tests. The first full Python sweep passed formatting but found eight lint issues, two middleware typing errors and 14 pytest failures (6,869 passed, 96 skipped, one expected failure). No receipt was issued.

The collected corrections explicitly extend the strict wire model and frozen fixtures, replace incomplete soil test stubs with actual Region objects, preserve packaged storage adapters before touching Boto-only attributes, and register typed middleware using the installed Sanic callable API. Independent review found no weakening. The shared coverage JSON fixtures are also consumed by the web Parquet client test; that affected file was rerun as a scoped follow-up, not another full web sweep.

The shared-fixture web follow-up passed all 84 tests. The corrected full Python gate passed format, lint, mypy and pytest against the restaged source. It used isolated temporary files and removed inherited PROJ/test-database overrides for that process. `QUALITY_RECEIPT.json` was generated at `2026-11-03T06:41:05.851896Z`, covering 1,260 files with tree digest `sha256:afa125f75d92ec1014126dc429b3f90ebbdcbcb71416832af7f258e887dd85ca`. The wrapper records pytest success without a final test-count summary; no count is inferred from the earlier failed run.

Evidence logs: `.omc/research/identity-python-final.log`, `.omc/research/identity-web-results.json`, and `.omc/research/identity-web-shared-fixture-final.log`. These are local checks; no production activation or custom-source admission is implied.
