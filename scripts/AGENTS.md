# Validation routing

`test-surface.mjs` selects local frontend test batches; see `docs/testing.md` for commands.
Use Vitest's installed `related` command for dependency traversal, then a separate batch for
filesystem contracts because those references do not appear in the import graph. Keep the
source-only batch first so unrelated contracts cannot hide a no-tests result.
Removed, shared, or unclassified runtime paths select the full suite. A missing base ref
is an error; a scoped pass never represents a full release check.

Keep selection tests focused on missed-change risks: staged/unstaged/untracked files,
renames/deletions, cross-stack fixtures, full-suite fallback, and invalid options.
Do not add tests whose only purpose is pinning an obsolete implementation or archive text.

The Docker build runs the same tooling tests before Vitest. Its context explicitly
includes `test-surface.mjs` and `tests/test-surface.test.mjs`, and its build stage
installs Git for the temporary-repository integration test. The application runtime
does not install Git; `.git` remains excluded from the build context. Keep these
build requirements aligned when adding executable tooling to the full test command.
