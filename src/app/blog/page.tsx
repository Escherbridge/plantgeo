import type { Metadata } from "next";
import Link from "next/link";
import { JsonLd } from "@/components/content/JsonLd";
import { MapLegalNote, PlanFunnelGate } from "@/components/content/PlanFunnelGate";
import {
  EditorialContainer,
  EditorialDisplay,
  EditorialEyebrow,
  EditorialPage,
  EditorialProse,
} from "@/components/ui/editorial";
import { RSS_ALTERNATE } from "@/lib/content/rss";
import { OG_IMAGE } from "@/lib/public-metadata";
import { collectionStructuredData } from "@/lib/content/structured-data";
import { ESSAY_AUTHOR } from "@/lib/writing/essay-metadata";
import { blogPath, ESSAYS, essayDisplayDate } from "@/lib/writing/essays";

const TITLE = "Field Blog — PlantGeo";
const DESCRIPTION =
  "Practical field walks and site-planning stories on reading sun, slope, drainage swales, frost pockets, wetlands, and soils before planting or building.";

export const metadata: Metadata = {
  title: TITLE,
  description: DESCRIPTION,
  alternates: { canonical: "/blog", types: RSS_ALTERNATE },
  openGraph: {
    type: "website",
    url: "/blog",
    title: TITLE,
    description: DESCRIPTION,
    images: [OG_IMAGE],
  },
  twitter: {
    card: "summary_large_image",
    title: TITLE,
    description: DESCRIPTION,
    images: [OG_IMAGE.url],
  },
};

export default function BlogIndexPage() {
  const posts = [...ESSAYS].sort(
    (a, b) => b.date.localeCompare(a.date) || a.slug.localeCompare(b.slug),
  );

  return (
    <EditorialPage>
      <JsonLd
        data={collectionStructuredData({
          name: "Field Blog",
          description: DESCRIPTION,
          path: "/blog",
          items: posts.map((essay) => ({ name: essay.title, path: blogPath(essay) })),
        })}
      />
      <EditorialContainer>
        <header className="pt-section pb-roomy">
          <EditorialEyebrow>Field Blog</EditorialEyebrow>
          <EditorialDisplay className="mt-comfortable">
            Notes from the ground
          </EditorialDisplay>
          <EditorialProse className="mt-roomy">
            <p>
              Practical guides and field walks for growers, homesteaders, master gardeners, and
              site designers learning to read sun, slope, wet spots, frost pockets, and soil breaks
              before staking beds or buying tools. Follow via{" "}
              <a
                href="/feed.xml"
                className="underline decoration-accent decoration-2 underline-offset-4"
              >
                RSS
              </a>
              .
            </p>
          </EditorialProse>
          <PlanFunnelGate
            eyebrow="Start on the live map"
            heading="Explore the Ada County sample parcel first"
            body="See how bare-earth contours, low drainage swales, flood zones, and soil map units look together on real ground—or open a blank private plan for your own site."
          />
        </header>
        <ul className="rule-top-heavy border-t-rule">
          {posts.map((essay) => (
            <li key={essay.slug} className="rule-bottom-hairline border-b-rule-faint py-comfortable">
              <p className="font-editorial-label text-label text-ink-muted uppercase">
                {essayDisplayDate(essay.date)} — {ESSAY_AUTHOR} — {essay.tags.join(" · ")}
              </p>
              <h2 className="mt-tight font-editorial-display text-heading font-semibold text-ink">
                <Link
                  href={blogPath(essay)}
                  className="underline decoration-accent decoration-2 underline-offset-4"
                >
                  {essay.title}
                </Link>
              </h2>
              <EditorialProse className="mt-tight">
                <p>{essay.summary}</p>
              </EditorialProse>
            </li>
          ))}
        </ul>
        <div className="pb-chapter">
          <MapLegalNote includeCatalogueNote />
        </div>
      </EditorialContainer>
    </EditorialPage>
  );
}
