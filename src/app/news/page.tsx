import type { Metadata } from "next";
import { JsonLd } from "@/components/content/JsonLd";
import {
  EditorialContainer,
  EditorialDisplay,
  EditorialEyebrow,
  EditorialPage,
  EditorialProse,
} from "@/components/ui/editorial";
import { RSS_ALTERNATE } from "@/lib/content/rss";
import { collectionStructuredData } from "@/lib/content/structured-data";

const TITLE = "News — PlantGeo";
const DESCRIPTION = "Announcements of public changes to PlantGeo. Nothing has been posted yet.";

export const metadata: Metadata = {
  title: TITLE,
  description: DESCRIPTION,
  alternates: { canonical: "/news", types: RSS_ALTERNATE },
  openGraph: { type: "website", url: "/news", title: TITLE, description: DESCRIPTION },
};

export default function NewsIndexPage() {
  return (
    <EditorialPage>
      <JsonLd
        data={collectionStructuredData({
          name: "News",
          description: DESCRIPTION,
          path: "/news",
          items: [],
        })}
      />
      <EditorialContainer>
        <header className="pt-section pb-chapter">
          <EditorialEyebrow>News</EditorialEyebrow>
          <EditorialDisplay className="mt-comfortable">Nothing has been announced.</EditorialDisplay>
          <EditorialProse className="mt-roomy">
            <p>
              This page lists public changes to PlantGeo after they happen. There are none to
              report yet.
            </p>
          </EditorialProse>
        </header>
      </EditorialContainer>
    </EditorialPage>
  );
}
