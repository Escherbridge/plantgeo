import { JsonLd } from "@/components/content/JsonLd";
import { MapLegalNote, PlanFunnelGate } from "@/components/content/PlanFunnelGate";
import { SiteTypeBlocks } from "@/components/content/SiteTypeDocument";
import {
  EditorialContainer,
  EditorialEyebrow,
  EditorialHeadline,
  EditorialPage,
  EditorialProse,
} from "@/components/ui/editorial";
import { GUIDE_AUTHOR, type GuideArticle } from "@/lib/content/guides";
import { guideStructuredData } from "@/lib/content/structured-data";

export function GuideDocument({ page }: { page: GuideArticle }) {
  return (
    <EditorialPage>
      <JsonLd data={guideStructuredData(page)} />
      <EditorialContainer>
        <header className="pt-section pb-roomy">
          <EditorialEyebrow>{page.eyebrow}</EditorialEyebrow>
          <EditorialHeadline as="h1" className="mt-comfortable">
            {page.h1}
          </EditorialHeadline>
        </header>
        <EditorialProse>
          <SiteTypeBlocks blocks={page.blocks} sources={page.sources} />
        </EditorialProse>
        <PlanFunnelGate />
        <div className="pb-chapter">
          <MapLegalNote
            author={GUIDE_AUTHOR}
            lastReviewed={page.lastReviewed}
            includeCatalogueNote={page.slug === "national-map" || page.slug === "us-topo"}
          />
        </div>
      </EditorialContainer>
    </EditorialPage>
  );
}
