import { JsonLd } from "@/components/content/JsonLd";
import { SiteTypeBlocks } from "@/components/content/SiteTypeDocument";
import {
  EditorialContainer,
  EditorialEyebrow,
  EditorialHeadline,
  EditorialPage,
  EditorialProse,
} from "@/components/ui/editorial";
import type { GuideArticle } from "@/lib/content/guides";
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
        <EditorialProse className="pb-chapter">
          <SiteTypeBlocks blocks={page.blocks} sources={page.sources} />
        </EditorialProse>
      </EditorialContainer>
    </EditorialPage>
  );
}
