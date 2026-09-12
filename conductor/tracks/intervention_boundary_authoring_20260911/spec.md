---
type: track-spec
track: intervention_boundary_authoring_20260911
status: in_progress
---

# Intervention boundary authoring

This bounded companion supplies real map geometry to the canonical
[community publication track](../community_engagement_completion_20260805/spec.md).
It does not own a second review or publication system.

Contributors choose Polygon, rectangle, or an explicit Point. Polygon/rectangle
authoring shows every accepted vertex, a distinct first vertex, a next-point preview,
and a filled draft. Finish validates the site before returning to the consent form.
Undo, clear/restart, edit and cancel are available without submitting. Cancel keeps
the previously saved geometry and form values. Keyboard and touch operate the same
draft; simultaneous map gestures must not place unrelated markers or start analysis.
Every disabled gesture handler returns to its prior state on exit, including drag pan.

Use bounded geographic coordinates, a closed ring and spherical area. Reject local
self-intersections, repeated vertices, degenerate area, pole/antimeridian ambiguity,
and excess vertices/bytes. These drawing limits are no looser than the server limits.
Do not introduce invented survey precision or automatic shape repair without consent.
The initial editor supports a single exterior ring; complex MultiPolygon submissions
already accepted by the API are preserved until the user explicitly replaces them.

The editor owns its temporary preview source/layers, restores them after a style swap,
and removes listeners and temporary paint on unmount. A Point is never silently
substituted for a drawn boundary. Publication remains an expert action and location
disclosure requires explicit contributor consent.

Not in scope: cadastral snapping, holes, MultiPolygon authoring, circle/freehand UX,
production seeding, deployment, environmental data ingestion, or lifecycle migration.

Acceptance includes geometry behavior tests, keyboard/touch/cancel/style tests and
the canonical local submit/review/publish/visible-map acceptance. Completion requires
an independent review plus exact tested revision and evidence, not only a toolbar render.
