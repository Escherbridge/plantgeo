import type { Metadata } from "next";
import { JsonLd } from "@/components/content/JsonLd";
import { MapLegalNote, PlanFunnelGate } from "@/components/content/PlanFunnelGate";
import { SiteTypeBlocks } from "@/components/content/SiteTypeDocument";
import {
  EditorialContainer,
  EditorialDisplay,
  EditorialEyebrow,
  EditorialPage,
  EditorialProse,
} from "@/components/ui/editorial";
import { GUIDE_AUTHOR, GUIDE_INDEX, GUIDES, guidePath } from "@/lib/content/guides";
import { RSS_ALTERNATE } from "@/lib/content/rss";
import { collectionStructuredData } from "@/lib/content/structured-data";

export const metadata: Metadata = {
  title: GUIDE_INDEX.seoTitle,
  description: GUIDE_INDEX.metaDescription,
  authors: [{ name: GUIDE_AUTHOR }],
  alternates: { canonical: "/guides", types: RSS_ALTERNATE },
  openGraph: {
    type: "website",
    url: "/guides",
    title: GUIDE_INDEX.seoTitle,
    description: GUIDE_INDEX.metaDescription,
  },
  twitter: {
    card: "summary_large_image",
    title: GUIDE_INDEX.seoTitle,
    description: GUIDE_INDEX.metaDescription,
  },
};

export default function GuidesPage() {
  return (
    <EditorialPage>
      <JsonLd
        data={collectionStructuredData({
          name: "Guides",
          description: GUIDE_INDEX.metaDescription,
          path: "/guides",
          items: GUIDES.map((page) => ({ name: page.crumb, path: guidePath(page) })),
        })}
      />
      <EditorialContainer>
        <header className="pt-section pb-roomy">
          <EditorialEyebrow>{GUIDE_INDEX.eyebrow}</EditorialEyebrow>
          <EditorialDisplay className="mt-comfortable">{GUIDE_INDEX.h1}</EditorialDisplay>
        </header>
        <EditorialProse className="pb-comfortable">
          <SiteTypeBlocks blocks={GUIDE_INDEX.blocks} sources={GUIDE_INDEX.sources} />
        </EditorialProse>
        <PlanFunnelGate
          eyebrow="Explore live layers"
          heading="See terrain, flood edges, wetlands, and soils on real ground"
          body="Open the interactive Ada County sample plan to toggle official layers across one parcel, or start a private site plan for your own land."
        />
        <div className="pb-chapter">
          <MapLegalNote
            author={GUIDE_AUTHOR}
            lastReviewed={GUIDE_INDEX.lastReviewed}
            includeCatalogueNote
          />
        </div>
      </EditorialContainer>
    </EditorialPage>
  );
}
