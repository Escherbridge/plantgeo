import type { Metadata } from "next";
import { JsonLd } from "@/components/content/JsonLd";
import { SiteTypeBlocks } from "@/components/content/SiteTypeDocument";
import {
  EditorialContainer,
  EditorialDisplay,
  EditorialEyebrow,
  EditorialPage,
  EditorialProse,
} from "@/components/ui/editorial";
import { RSS_ALTERNATE } from "@/lib/content/rss";
import { SITE_TYPE_AUTHOR, SITE_TYPE_INDEX, SITE_TYPES, siteTypePath } from "@/lib/content/site-types";
import { collectionStructuredData } from "@/lib/content/structured-data";

export const metadata: Metadata = {
  title: SITE_TYPE_INDEX.seoTitle,
  description: SITE_TYPE_INDEX.metaDescription,
  authors: [{ name: SITE_TYPE_AUTHOR }],
  alternates: { canonical: "/site-types", types: RSS_ALTERNATE },
  openGraph: {
    type: "website",
    url: "/site-types",
    title: SITE_TYPE_INDEX.seoTitle,
    description: SITE_TYPE_INDEX.metaDescription,
  },
};

export default function SiteTypesPage() {
  return (
    <EditorialPage>
      <JsonLd
        data={collectionStructuredData({
          name: "Site types",
          description: SITE_TYPE_INDEX.metaDescription,
          path: "/site-types",
          items: SITE_TYPES.map((page) => ({ name: page.crumb, path: siteTypePath(page) })),
        })}
      />
      <EditorialContainer>
        <header className="pt-section pb-roomy">
          <EditorialEyebrow>{SITE_TYPE_INDEX.eyebrow}</EditorialEyebrow>
          <EditorialDisplay className="mt-comfortable">{SITE_TYPE_INDEX.h1}</EditorialDisplay>
        </header>
        <EditorialProse className="pb-chapter">
          <SiteTypeBlocks blocks={SITE_TYPE_INDEX.blocks} />
        </EditorialProse>
      </EditorialContainer>
    </EditorialPage>
  );
}
