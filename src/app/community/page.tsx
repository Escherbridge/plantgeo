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
    "Strategy requests publish their location, title, and description on the public map immediately. Anyone can read a request; sign in to read comments. Intervention proposals follow a separate review process.",
};

export default function CommunityPage() {
  return (
    <EditorialPage>
      <EditorialContainer>
        <header className="pt-section pb-roomy">
          <EditorialGrid>
            <div className="col-span-4 md:col-span-9">
              <EditorialEyebrow>
                Community — Public strategy requests
              </EditorialEyebrow>
              <EditorialDisplay className="mt-comfortable">
                Put a strategy request on the map.
              </EditorialDisplay>
            </div>
          </EditorialGrid>

          <EditorialGrid className="mt-roomy">
            <div className="col-span-4 md:col-span-7 md:col-start-5">
              <EditorialLead>
                People using PlantGeo record where a regenerative strategy ought
                to go — keyline earthworks, silvopasture, reforestation, biochar,
                water harvesting, cover cropping. A submitted request publishes
                its location, title, and description on the public map immediately,
                without a review queue. Anyone can read it, including people who
                are not signed in.
              </EditorialLead>
              <EditorialLead className="mt-comfortable">
                Intervention proposals describe a drawn point or boundary for
                expert review. Proposals submitted for publication review are
                shared with signed-in readers while awaiting a decision, and
                become public after approval. Find proposals awaiting review in the{" "}
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
