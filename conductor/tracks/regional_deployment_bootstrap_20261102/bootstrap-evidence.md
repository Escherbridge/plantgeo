---
type: validation-report
---
# Inactive bootstrap and agent identity batch

Starting checkpoint: `edd49302` on `data/regional-bootstrap`. This batch implements the inactive regional compiler/offline preflight described in `bootstrap-design.md` and closes the agent transport findings in `agent-identity-followup.md`. It does not admit custom sources or complete production regional setup.

Independent review identified an artifact-size mismatch: compilation could write a file larger than preflight accepted. The author corrected per-artifact and embedded-manifest limits before output, with near-limit roundtrip and oversized-refusal tests. Final source review cleared that finding. Separate agent review confirmed both remote bridge operations use the shared identity transport and Python checks identity before tool dispatch; late admission errors remain sanitized 409 refusals instead of argument errors. Reviewers did not run tests.

All source edits were frozen and staged before the integrated gate. The Python changed-test selector requires full-suite fallback for the new execution package and CLI. The web selector also selected full scope; unrelated user untracked files remain untouched. Full Python format/lint/mypy/pytest with receipt-on-success and full web boundary/type/lint/tests ran together. Logs: `.omc/research/bootstrap-python.log`, `.omc/research/bootstrap-web-results.json` and `.omc/research/bootstrap-*.log`.

The first full web sweep passed boundary, lint and all 253 test files / 3,642 tests. Typing failed only on the new header-fixture union requiring an explicit `HeadersInit` annotation. Python passed format and mypy, but lint found three issues and pytest reported 14 failed, 6,896 passed, 96 skipped and one expected failure. Ten failures exercise the compiler, three agent fixtures use an unregistered tool name, and the architecture inventory test still expects no execution subpackages. No receipt was issued.

The correction batch preserved immutable provider endpoints while serializing each validated endpoint explicitly, retained runtime Pydantic model imports, corrected agent fixtures to use a registered tool and updated the explicit domain inventory without an isolation exemption. Independent reinspection cleared the corrections.

The corrected full Python sweep passed format, lint, mypy and pytest. Its receipt was generated at `2026-11-03T07:30:59.929369Z`, covering 1,270 files with digest `sha256:84047af29807e032970d241192875d2e994055c169c0ee1d7cbe98b68a8bf461`. Passing pytest stdout is not retained by the wrapper, so no final count is inferred from the failed run. Full web typing and all seven affected transport tests passed after the annotation-only correction; the preceding full 253-file/3,642-test result remains the full-suite evidence, and the follow-up is explicitly scoped.

Final logs: `.omc/research/bootstrap-python-final.log`, `.omc/research/bootstrap-type-check-final.log`, `.omc/research/bootstrap-web-followup.log`. No new deployment or source admission occurred in this checkpoint.
