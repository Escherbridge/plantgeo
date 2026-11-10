import type { Metadata } from "next";
import { JsonLd } from "@/components/content/JsonLd";
import {
  EditorialContainer,
  EditorialDisplay,
  EditorialEyebrow,
  EditorialLink,
  EditorialPage,
  EditorialProse,
} from "@/components/ui/editorial";
import { RSS_ALTERNATE } from "@/lib/content/rss";
import { collectionStructuredData } from "@/lib/content/structured-data";

const TITLE = "Site types — PlantGeo";
const DESCRIPTION =
  "Kinds of land a plan might cover. The researched pages are not written yet, and this page does not publish figures.";

export const metadata: Metadata = {
  title: TITLE,
  description: DESCRIPTION,
  alternates: { canonical: "/site-types", types: RSS_ALTERNATE },
  openGraph: { type: "website", url: "/site-types", title: TITLE, description: DESCRIPTION },
};

export default function SiteTypesPage() {
  return (
    <EditorialPage>
      <JsonLd
        data={collectionStructuredData({
          name: "Site types",
          description: DESCRIPTION,
          path: "/site-types",
          items: [],
        })}
      />
      <EditorialContainer>
        <header className="pt-section pb-roomy">
          <EditorialEyebrow>Site types</EditorialEyebrow>
          <EditorialDisplay className="mt-comfortable">
            The researched pages are not written yet.
          </EditorialDisplay>
        </header>
        <EditorialProse className="pb-chapter">
          <p>
            This section is for kinds of land a person might open a map to plan: the wildland–urban
            interface, a riparian or watershed edge, a homestead or small parcel, a school garden or
            campus, and rangeland. No researched page for any of them is published yet, so none of
            those names is a link, and this page does not quote a figure.
          </p>
          <p>PlantGeo publishes no forecasts.</p>
          <p>
            The{" "}
            <EditorialLink href="/plan/sample-ada-county">sample plan</EditorialLink> is a
            demonstration on real map data, and it is not a customer.
          </p>
          <p>
            <EditorialLink href="https://aevani.com">Catalogue items from Aevani</EditorialLink> can
            be added to a plan later; nothing from that catalogue is for sale yet.
          </p>
        </EditorialProse>
      </EditorialContainer>
    </EditorialPage>
  );
}
