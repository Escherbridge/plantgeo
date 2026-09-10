# Community ledger

## Deferred waypoint section — 2026-09-10

The Opportunity waypoints subsection never rendered returned data: it called getPriorityZones,
then displayed No zones published for every successful response. The current protected endpoint
actually returns caller-scoped strategy activity totals, not spatial opportunity waypoints. The
subsection and its unused request are removed; the real private/workspace ledger, voting and
request forms remain. The backend summary stays available to its real consumers. A future waypoint
view needs published spatial opportunities with access controls and actual populated rendering,
not a renamed activity total or a permanent empty placeholder.

Adding a request names the map's actual + Request button.
