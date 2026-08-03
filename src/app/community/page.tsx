import type { Metadata } from "next";
import { CommunityLedger } from "@/app/community/CommunityLedger";
import {
  EditorialContainer,
  EditorialDisplay,
  EditorialEyebrow,
  EditorialGrid,
  EditorialLead,
  EditorialPage,
} from "@/components/ui/editorial";

export const metadata: Metadata = {
  title: "Community - PlantGeo",
  description:
    "Strategy requests recorded by PlantGeo accounts and partner workspaces. Private by default, with locations held in the database rather than published.",
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
                A ledger, not a feed.
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
            </div>
          </EditorialGrid>
        </header>
      </EditorialContainer>

      <CommunityLedger />
    </EditorialPage>
  );
}
