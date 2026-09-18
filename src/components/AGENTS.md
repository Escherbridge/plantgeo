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

## ParquetLayerFaultBanner

Extracted from `LayerManager.tsx`'s inline JSX (readability pass 2026-09-18): the alert-stack
markup that renders `parquetLayerFaults` was a 20-line unnamed `<div>` block sitting between the
data-layer components and `QueryPointLayer`. It is pure presentation — no hooks, no map instance,
no effect ordering — so it moved out with no behavior change; `LayerManager` still computes the
fault list itself (that computation stays local: it reads a dozen render-scope query results and
is not yet a second call site, so it is not extracted per the "second use" rule).
