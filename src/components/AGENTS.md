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

## Community data intervention fields and provenance

`intervention/DataInterventionFields` is shared by both intervention submission surfaces. A
collection plan asks for a lane and collection method; a dataset submission also asks for its
observation day and an HTTP(S) evidence link. Field labels use instance-specific IDs because
the map workspace and community modal can coexist. Collection payloads omit hidden date/link
values retained from a submission draft, so those values cannot silently publish or block a plan.

`intervention/DataInterventionSummary` renders the same lane, method, observation day and safe
link in submission history, feed, moderation and map details. Its origin label is independent of
publication or review status. Every supported data intervention writer produces community data;
only an exact community origin is labelled, while missing, unknown and legacy `verified_source`
values remain unknown. Stored JSON is not a governed-source attestation. Details are validated
before rendering a link, and observation days retain their calendar date without localization.

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
