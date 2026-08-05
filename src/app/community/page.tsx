import type { Metadata } from "next";
import { CommunityLedger } from "@/app/community/CommunityLedger";
import {
  EditorialContainer,
  EditorialDisplay,
  EditorialEyebrow,
  EditorialGrid,
  EditorialLead,
  EditorialLink,
  EditorialPage,
} from "@/components/ui/editorial";

export const metadata: Metadata = {
  title: "Community - PlantGeo",
  description:
    "Strategy requests recorded by PlantGeo accounts and partner workspaces. Private by default, with locations held in the database rather than published. For sites drawn and submitted for review, see the feed.",
};

export default function CommunityPage() {
  return (
    <EditorialPage>
      <EditorialContainer>
        <header className="pt-section pb-roomy">
          <EditorialGrid>
            <div className="col-span-4 md:col-span-9">
              <EditorialEyebrow>
                Community — Strategy requests, private by default
              </EditorialEyebrow>
              <EditorialDisplay className="mt-comfortable">
                A ledger, not a broadcast.
              </EditorialDisplay>
            </div>
          </EditorialGrid>

          <EditorialGrid className="mt-roomy">
            <div className="col-span-4 md:col-span-7 md:col-start-5">
              <EditorialLead>
                People using PlantGeo record where a regenerative strategy ought
                to go — keyline earthworks, silvopasture, reforestation, biochar,
                water harvesting, cover cropping. Those records stay with the
                account or the partner workspace that made them. This page shows
                you yours, and tells you plainly when something is not available
                to show.
              </EditorialLead>
              <EditorialLead className="mt-comfortable">
                A request is not a proposal. A request says a strategy ought to
                go somewhere and keeps the where to itself; a proposal is a site
                someone has drawn and asked to have reviewed. Proposals are
                shared with every signed-in account, and they live in the{" "}
                <EditorialLink href="/feed">feed</EditorialLink>.
              </EditorialLead>
            </div>
          </EditorialGrid>
        </header>
      </EditorialContainer>

      <CommunityLedger />
    </EditorialPage>
  );
}
