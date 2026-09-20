---
type: agents
---

# src/components/AGENTS.md

Directory-level rationale for components whose "why" is too long for a one-line doc comment.
Add a section here rather than a new file when the next component needs one. Most component
files under `map/` still carry their own extensive inline rationale (ordering, ref discipline,
`style.load`/`styledata` sequencing) rather than a pointer here — that prose is tied to a specific
line's non-obvious behavior, not a directory-level concept, so moving it would separate the
warning from the line it warns about. See `src/components/map/AGENTS.md` for that layer's own
notes; this file covers cross-cutting component concerns only.

## Authentication forms

Registration uses the same browser-safe schema as the API, including optional blank names and
UTF-8 password limits. Registration and credential sign-in always release their loading state
after transport errors so the user can retry. Navigation requires an acknowledged registration
or an explicitly successful sign-in result. The generic registration notice preserves account
privacy, explains that an existing password stays unchanged, and does not claim email verification
is required before sign-in.

## ParquetLayerFaultBanner

Extracted from `LayerManager.tsx`'s inline JSX (readability pass 2026-09-18): the alert-stack
markup that renders `parquetLayerFaults` was a 20-line unnamed `<div>` block sitting between the
data-layer components and `QueryPointLayer`. It is pure presentation — no hooks, no map instance,
no effect ordering — so it moved out with no behavior change; `LayerManager` still computes the
fault list itself (that computation stays local: it reads a dozen render-scope query results and
is not yet a second call site, so it is not extracted per the "second use" rule).
