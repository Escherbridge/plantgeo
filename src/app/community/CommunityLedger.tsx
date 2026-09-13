"use client";

import {
  EditorialActionLink,
  EditorialContainer,
  EditorialNotice,
  EditorialProse,
  EditorialRule,
  EditorialSection,
} from "@/components/ui/editorial";

/**
 * The `/community` page after strategy requests became public.
 *
 * This page WAS the private ledger: a `community.getRequests` list scoped to the reader's own
 * account or a workspace they belong to, with a `community.voteOnRequest` button beside each row
 * and copy explaining that coordinates never leave the database. Phase 3 of
 * `public_strategy_requests_20260913` retired all five of those procedures along with the
 * `strategy_requests` table behind them, because a request is now a published `geo.features` row
 * that everyone can already see on the map.
 *
 * What replaces the list is a pointer, not a rebuilt list. A panel or page that re-lists public
 * map features would be a second, worse view of the map -- and the one thing the page must not
 * lose, the route to submitting a request, lives on the map anyway (the community panel's
 * "+ Request" button, pinned to the map centre). The signed-in/signed-out split went with the
 * private data: there is nothing here to gate any more.
 */
export function CommunityLedger() {
  return (
    <EditorialContainer>
      <EditorialSection index="01" title="The ledger" id="ledger">
        <EditorialNotice title="Requests live on the map now" role="status">
          <p>
            Strategy requests used to be recorded here, readable only by the
            account that submitted one or by a workspace it was shared with. They
            are now ordinary features of the public map: published the moment they
            are submitted, drawn in their own colour, and open for anyone to read,
            comment on and respond to.
          </p>
        </EditorialNotice>

        <EditorialProse className="mt-comfortable">
          <p>
            Open the map and click a request to see who asked for it, what they
            asked for and the conversation underneath. Nothing needs to be
            approved first &mdash; a request is an ask, not a claim about what has
            been built, so it skips the review queue that intervention proposals
            go through.
          </p>
        </EditorialProse>

        <div className="mt-comfortable flex flex-wrap gap-tight">
          <EditorialActionLink href="/">Open the map</EditorialActionLink>
          <EditorialActionLink href="/feed" tone="outline">
            Proposals awaiting review
          </EditorialActionLink>
        </div>
      </EditorialSection>

      <EditorialSection index="02" title="Adding a request" id="submit">
        <EditorialProse>
          <p>
            A strategy request is anchored to a point on the ground, so it is
            created from the map rather than from this page. Open the map, centre
            it on the parcel you have in mind, open the community panel, and
            choose + Request; the request is recorded at the map&rsquo;s centre
            point and appears there immediately for every reader.
          </p>
          <p>
            Drawing a site and asking to have it reviewed is the other, heavier
            move: that is a proposal, it carries a drawn boundary, and it only
            reaches the map once a reviewer approves it.
          </p>
        </EditorialProse>
        <div className="mt-comfortable flex flex-wrap gap-tight">
          <EditorialActionLink href="/">Open the map</EditorialActionLink>
          <EditorialActionLink href="/about#principles" tone="outline">
            How this platform handles what you share
          </EditorialActionLink>
        </div>
      </EditorialSection>

      <EditorialRule weight="massive" className="mb-chapter" />
    </EditorialContainer>
  );
}
