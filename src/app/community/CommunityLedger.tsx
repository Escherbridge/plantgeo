"use client";

import {
  EditorialActionLink,
  EditorialContainer,
  EditorialNotice,
  EditorialProse,
  EditorialRule,
  EditorialSection,
} from "@/components/ui/editorial";

/** Explains public requests and links to map submission; see AGENTS.md. */
export function CommunityLedger() {
  return (
    <EditorialContainer>
      <EditorialSection index="01" title="Public requests" id="ledger">
        <EditorialNotice title="Requests live on the map now" role="status">
          <p>
            Strategy requests publish their location, title, and description on
            the public map as soon as they are submitted. Anyone can read a
            published request without signing in.
          </p>
        </EditorialNotice>

        <EditorialProse className="mt-comfortable">
          <p>
            Open the map and click a request to read its details. Sign in to read
            comments. Contributor access is required to post requests, add
            comments, or like a feature.
          </p>
          <p>
            A request asks for a strategy at a location; it does not claim that an
            intervention has been built. Requests are published without the
            expert review required for intervention proposals.
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
            created from the map. Sign in with contributor access, open the map,
            centre it on the location you have in mind, open the community panel,
            and choose + Request. Check the pin location, title, and description,
            then confirm that they can be published before posting.
          </p>
          <p>
            To propose an intervention for review, choose + Recommend and draw a
            point or boundary. Submitted proposals can be visible to signed-in
            readers during review; they become public after a reviewer approves them.
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
