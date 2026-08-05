import type { Metadata } from "next";
import { InterventionFeed } from "@/app/feed/InterventionFeed";
import {
  EditorialContainer,
  EditorialDisplay,
  EditorialEyebrow,
  EditorialGrid,
  EditorialLead,
  EditorialPage,
} from "@/components/ui/editorial";

export const metadata: Metadata = {
  title: "Feed - PlantGeo",
  description:
    "Every intervention proposed by a PlantGeo contributor and still awaiting expert review, each one linked to its site on the map.",
};

export default function FeedPage() {
  return (
    <EditorialPage>
      <EditorialContainer>
        <header className="pt-section pb-roomy">
          <EditorialGrid>
            <div className="col-span-4 md:col-span-9">
              <EditorialEyebrow>
                Feed — Proposed interventions, awaiting review
              </EditorialEyebrow>
              <EditorialDisplay className="mt-comfortable">
                What people want to plant.
              </EditorialDisplay>
            </div>
          </EditorialGrid>

          <EditorialGrid className="mt-roomy">
            <div className="col-span-4 md:col-span-7 md:col-start-5">
              <EditorialLead>
                Every entry below is a recommendation a contributor put on the
                ground and asked to have published — reforestation, silvopasture,
                cover cropping, biochar, keyline earthworks. None of them is on
                the public map yet; each is waiting for an expert to publish or
                reject it. Follow any entry to see the site it names.
              </EditorialLead>
            </div>
          </EditorialGrid>
        </header>
      </EditorialContainer>

      <InterventionFeed />
    </EditorialPage>
  );
}
