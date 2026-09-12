---
type: track-spec
slug: pnw_land_contact_experience_20260911
status: planned
---

# PNW land context and public contact experience

## Planning status and outcome

This is a **registered planned packet**. [conductor/tracks.md](../../tracks.md)
remains the sole current work registry and indexes it as `planned`.
Completing these planning documents does not complete or activate the track.
This work authorizes no implementation, ingestion, outreach, publication or
deployment. Those actions require their applicable later authorization and gates.

Help a person exploring a location or bounded project area understand its parcel,
published land-use evidence, utility territory and public-land management, then
find an evidenced public route for the next conversation. Rich browsable details
should answer what the place is, why an office applies, what help it documents,
what references to provide and which uncertainties remain.

The experience depends on
[pnw_land_context_reference_plane_20260911](../pnw_land_context_reference_plane_20260911/spec.md).
Its [source inventory](../pnw_land_context_reference_plane_20260911/evidence/source-inventory.md)
is the shared admission and source-evidence reference. The existing research
informed this packet; it does not establish live data coverage, source admission
or implemented behavior.

Apply the [September 12 coordination gates](../pnw_land_context_reference_plane_20260911/evidence/land-herbaria-coordination-20260912.md)
to any later botanical association. Private-name exclusion does not make a
specimen-derived parcel ID safe to expose: preserve admitted precision and
withholding in the panel, agent and draft. Ordinary land lookup and public
records help remain independently governed.

## Scope and four toggles

Limit product scope to Washington, Oregon and Idaho. The upstream query envelope
is `[-125, 42, -111, 49]`; intersect with the WA/OR/ID administrative footprint
because the rectangle includes other territory. Do not imply full-state coverage
where only particular counties, agencies or providers are admitted.

| Toggle | Place information | Useful public routes |
| --- | --- | --- |
| Parcels and land use | Namespaced parcel ID, county, location, acreage with source/method, published nonpersonal ownership category, independently sourced use facets and official record link. | Assessor/recorder records help, local planning, Extension, conservation district and USDA office where applicability is established. |
| Electric utility service territories | Published retail/distribution territory, provider and type, geometry/source version, overlaps and source qualification. | New service, interconnection, engineering/large load, development and relevant regulator routes. |
| BLM lands | Verified BLM surface-manager records plus separately resolved administrative office and program relationships. | Responsible field office and documented rights-of-way, land-use or other program routes. |
| State-managed lands | Source-backed public agency owner/manager/steward and distinct land interests, with jurisdiction relationships. | Actual managing agency and regional/program route; do not send every state tract to one land department. |

Gas, water, wastewater, transmission and physical utility assets are outside the
initial electric scope. Future utility subtypes need their own admission and
meaning; they do not inherit electricity's geometry, coverage or contacts.

Private parcel-owner names and personal owner contact fields are excluded by
default from payloads, panels, agent answers, draft inquiries and exported/copied
context. Their absence is an intentional product choice, not missingness or a
readiness blocker. Named public managing agencies and source-published
professional contacts remain in scope. Do not derive personal contact details
from official record links or scraped documents.

Assessor use, crop-cover summaries, Census urban membership and local zoning are
separate optional facets with their own source/version. Crop or urban membership
does not decide zoning, farming rights or permitted development. Parcel records
and GIS intersection do not establish title. A utility territory does not promise
address-level service, capacity, cost or interconnection. BLM/state toggles do not
constitute all public lands or authorities; absent records never mean private,
unrestricted or unregulated land.

## Selection and persistent browsing

Hover or keyboard focus presents concise feature identity, category, source
vintage and contact-route summary. Click, tap or an explicit keyboard selection
pins a persistent detail panel. A hover surface is dismissible and remains
available while the user moves into it; essential information and actions are
also available in the pinned panel. Do not force link browsing through a
transient pointer tooltip.

Selection uses a picked/searched point, explicit parcel/tract or selected bounded
project area. Never substitute the viewport centre. For a selected area, return
the intersecting features and their applicable routes across all enabled groups,
with stated truncation/pagination and coverage; do not reduce the project to its
centroid or arbitrarily choose the first overlap. Points on a boundary and
conflicting published assignments can have several candidates.

The panel supports:

1. **Place details:** coordinates/selected area, parcel/tract/territory references,
   source-backed size/category/use, public ownership versus management, and
   official source record.
2. **Relevant parties:** distinct office/program cards, with topic and regional
   fit, public business phone/email/form and official links.
3. **Why this route applies:** published jurisdiction/native office key or
   reviewed crosswalk, relationship evidence, match method and unresolved scope.
4. **Documented help:** service actually described by the source, required
   parcel/tract/location references, and a useful inquiry suggestion.
5. **Evidence and time:** source/version, coverage, contact verification time,
   current-reference/history meaning, and stale or unverified items.
6. **Related advice:** topic-matched regional advisers with their stated service
   area and program limits, visibly distinct from responsible authorities.

Browse multiple features without losing the selected project area. Deduplicate
a shared office card while retaining every relevant feature/relationship and
program route. Geometry evidence uses the reference plane's exact source-resolution
lookup; display simplification and nearest-office distance cannot determine
responsibility. Inferred candidates are labelled as candidates, not responsible
offices or matched specialists.

## Contact and help claims

Keep the following route meanings distinct in UI labels, responses and drafts:

| Route | Permitted claim |
| --- | --- |
| Records assistance | A published records/help office can be asked about its documented property-record or recorded-document services. |
| Responsible agency/program | Source-backed jurisdiction and task responsibility establish an applicable agency/program inquiry route. |
| Advisory SME | A professional role and published topic/service region establish advisory fit; expertise does not imply approval authority. |
| Contact-process inquiry | A source-backed office route supports asking about the appropriate next contact process; the result is not an identified private owner or representative. |
| Documented introduction/forwarding | Show this capability only when a cited current source expressly offers that service, including its limits. |

A help card for a parcel can say **Ask about the appropriate contact process**
even though no direct private contact is present. It must identify the relevant
office, its official inquiry page/business route, documented help and parcel
references to include. Generic office information is not evidence that the
office knows a responsible person, may disclose personal details, will identify
the owner, or will forward or introduce the user. Unknown capability stays
unknown. No introduction or forwarding service was established by the current
research; the product must not enable a positive capability claim from that
research alone.

Prefer stable role/team routes. Optional professional names require published
role and regional/task context plus content verification. Page reachability and
contact-content verification are different facts. A nearby office, generic
directory entry or agency name alone does not prove specialist fit. Broken or
stale routes retain clear status and offer another source-backed office route
only where one exists; never invent a replacement.

## User-reviewed inquiry drafts

A **Draft inquiry** action assembles editable text for the user to review and
copy. Include the selected location, relevant namespaced parcel/tract IDs,
county/state, user-provided idea, proposed public recipient/role, a question
appropriate to that route and source links. Do not insert private names, infer
ownership/representation, invent project facts or claim permission.

For records help, a draft may ask which records or office handle the inquiry.
It may ask whether an established contact process exists, without implying that
forwarding or disclosure is offered. For utility or public-land requests, use
the actual documented service/program and tract/territory reference. Show why
the suggested recipient is relevant and what remains uncertain before copying.

Generating or copying a draft sends nothing. No background submission, email,
posting, automatic contact, or automated introduction is included. Opening an
official form is user-driven; submitting it or sending a message is a separate
user-authorized action. The panel and agent report a draft as a draft.

## Time and availability

Consume the reference plane's admitted version/availability contract. Four UI
groups do not imply four combined temporal surfaces: required boundary data and
optional crop/use/contact facets can have independent versions. Pin compatible
geometry, relationships and directory releases, retain their identities, and
do not demand matching publication days without an explicit source contract.

Show historical versions only when supported. If the user selects an unsupported
historical day, report history unavailable; a separately identified **Current
reference** view may remain useful but must not appear to describe that day.
Source-effective, source-published, capture and contact-content-verification
times stay distinct. A capture timestamp is not an effective date or a substitute
for a required source watermark.

Current public contact routes used with a historical boundary must be labelled
current; do not imply the person held the role historically. A requested current
directory refresh must not silently swap the selected boundary version.

Distinguish outside pilot, missing jurisdiction/source geometry, admitted empty
result, partial selected-area coverage, unsupported history, unavailable optional
use attributes, unverified office assignment and stale contact route. Missing
optional enrichment does not hide admitted geometry. Deliberately excluded
private fields produce no warning, missing count or repair task.

## Agent parity

The agent uses the same selected point/area, topic, source versions, permitted
field set and availability contract as the map/detail panel. It can explain
intersections/overlaps, find an applicable public office, identify documented
records/contact-process help, find an evidenced adviser, and draft an inquiry.

Each result carries feature and release references, organization/office/role,
public route, route meaning, documented service, relationship evidence, why it
applies, current-versus-historical qualification, verification status and gaps.
Distance is supplemental, never proof of responsibility. Do not silently replace
an unavailable exact day, source, location or route with the nearest candidate.

UI and agent must agree on multiple candidates, partial-area coverage,
nonpersonal parcel details, absent optional facets and unsupported contact
capabilities. If a public help route is not verified, say so and retain the
official source record where available; do not fabricate a usable contact.

## Accessibility and bounded delivery

Provide keyboard selection and equivalent textual feature/contact lists,
predictable focus order, explicit dismissal, focus restoration, and visible
focus. Panel updates announce concise selection/coverage changes. Relationships,
route meanings and stale/missing states cannot rely on colour alone. Links have
descriptive names; public phone/email and official inquiry links are operable
without pointer precision.

On mobile, support tap selection, an accessible area-selection alternative and
a persistent sheet with useful map space, scrolling contact content and adequate
touch targets. Respect reduced motion. Preserve selection while the user explores
evidence or returns from a source link.

Define and measure point/area query, feature/result count, geometry byte,
contact payload, request-to-paint and mobile responsiveness budgets after the
admitted source contracts are frozen. Cancel superseded requests and prevent
old geometry/contact responses repainting a newer selection. Use bounded
pagination with an explicit completeness state; a capped list is not a complete
project-area contact inventory.

## Acceptance and unresolved gates

Acceptance requires representative admitted examples in all three states:
urban parcel records help, agricultural advisory context, electric territories,
BLM surface-to-office responsibility and state agency routing. Include overlapping
jurisdictions, a multi-feature selected area, an unsupported historical day,
partial geography, stale contacts, missing optional facets and deliberate
private-field exclusion. Source gaps remain visible; sample evidence is not
full PNW coverage.

Independent source/relationship, UI/agent-parity and accessibility review must
evaluate the complete implementation separately from its authors, after one
integrated verification sweep. Planning packet review proves only planning
quality. Runtime acceptance, source admission, registry activation and track
completion remain future gates.

Open design inputs include current Oregon utility-feed reconciliation, Idaho
utility and state-interest source/keys, permitted parcel coverage, reviewed
office/topic relationships, contact freshness policy, source watermarks, bounded
area selection/result budgets and shared-file ownership. The reference plane's
source inventory owns evidence and admission; this track must not turn unresolved
candidates into ready layers.
