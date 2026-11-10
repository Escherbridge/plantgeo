import type { Metadata } from "next";
import { DocumentPage } from "@/components/layout/DocumentPage";
import { EditorialLink, EditorialProse, EditorialSection } from "@/components/ui/editorial";

export const metadata: Metadata = {
  title: "Page not found — PlantGeo",
  description: "This page is not on the site.",
  robots: { index: false, follow: false },
};

export default function NotFound() {
  return (
    <DocumentPage
      eyebrow="404"
      title="This page is not on the site."
      lead="The address does not match a public page."
    >
      <EditorialSection index="01" title="Elsewhere" id="elsewhere">
        <EditorialProse>
          <p>
            <EditorialLink href="/">Open the map</EditorialLink>
            {" · "}
            <EditorialLink href="/blog">Blog</EditorialLink>
          </p>
        </EditorialProse>
      </EditorialSection>
    </DocumentPage>
  );
}
