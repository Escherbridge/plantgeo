# Community ledger

## Public strategy requests

The public-request track retired the private/team-scoped ledger. The route's metadata, hero,
`CommunityLedger`, map-panel help, and request consent must agree: a submitted request publishes
its location, title, and description immediately, with no review queue. Published request detail
is public; comments and like-state reads require sign-in, and posting requests/comments/likes
requires contributor access. These boundaries come from `interventions.submitRequest`,
`getInterventionDetail`, and the `interventionSocial` procedures. Copy must not promise private
requests, anonymous commenting, or a working request-voting control.

Intervention proposals are a separate reviewed submission flow. Consented pending proposals can
be visible to signed-in readers before they are approved for public publication. Both points
and drawn boundaries are supported. The section below records an earlier waypoint cleanup,
before the private ledger was retired; it is not the current request contract.

## Deferred waypoint section — 2026-09-10

The Opportunity waypoints subsection never rendered returned data: it called getPriorityZones,
then displayed No zones published for every successful response. At that checkpoint the protected endpoint
actually returns caller-scoped strategy activity totals, not spatial opportunity waypoints. The
subsection and its unused request were removed; the private/workspace ledger, voting and
request forms still existed at that time. A future waypoint
view needs published spatial opportunities with access controls and actual populated rendering,
not a renamed activity total or a permanent empty placeholder.

Adding a request names the map's actual + Request button.
