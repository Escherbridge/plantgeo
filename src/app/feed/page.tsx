import { FeedIntro } from "@/app/feed/FeedIntro";
import { InterventionFeed } from "@/app/feed/InterventionFeed";
import { PublicJsonLd } from "@/components/content/PublicJsonLd";
import { publicPageMetadata } from "@/lib/public-metadata";
import {
  EditorialContainer,
  EditorialDisplay,
  EditorialEyebrow,
  EditorialGrid,
  EditorialPage,
} from "@/components/ui/editorial";

const TITLE = "Feed - PlantGeo";
const DESCRIPTION =
  "Every intervention proposed by a PlantGeo contributor and still awaiting expert review, each one linked to its site on the map.";

export const metadata = publicPageMetadata({
  title: TITLE,
  description: DESCRIPTION,
  path: "/feed",
});

export default function FeedPage() {
  return (
    <EditorialPage>
      <PublicJsonLd name={TITLE} description={DESCRIPTION} path="/feed" />
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
              <FeedIntro />
            </div>
          </EditorialGrid>
        </header>
      </EditorialContainer>

      <InterventionFeed />
    </EditorialPage>
  );
}
