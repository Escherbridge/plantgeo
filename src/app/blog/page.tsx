import type { Metadata } from "next";
import Link from "next/link";
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
import { ESSAY_AUTHOR } from "@/lib/writing/essay-metadata";
import { blogPath, ESSAYS, essayDisplayDate } from "@/lib/writing/essays";

const TITLE = "Blog — PlantGeo";
const DESCRIPTION =
  "Two notes from Escherbridge: what PlantGeo will not forecast, and how a site is planned before anything is bought for it.";

export const metadata: Metadata = {
  title: TITLE,
  description: DESCRIPTION,
  alternates: { canonical: "/blog", types: RSS_ALTERNATE },
  openGraph: { type: "website", url: "/blog", title: TITLE, description: DESCRIPTION },
};

export default function BlogIndexPage() {
  const posts = [...ESSAYS].sort(
    (a, b) => b.date.localeCompare(a.date) || a.slug.localeCompare(b.slug),
  );

  return (
    <EditorialPage>
      <JsonLd
        data={collectionStructuredData({
          name: "Blog",
          description: DESCRIPTION,
          path: "/blog",
          items: posts.map((essay) => ({ name: essay.title, path: blogPath(essay) })),
        })}
      />
      <EditorialContainer>
        <header className="pt-section pb-roomy">
          <EditorialEyebrow>Blog</EditorialEyebrow>
          <EditorialDisplay className="mt-comfortable">Notes</EditorialDisplay>
          <EditorialProse className="mt-roomy">
            <p>
              Dated notes from {ESSAY_AUTHOR}.{" "}
              <a
                href="/feed.xml"
                className="underline decoration-accent decoration-2 underline-offset-4"
              >
                RSS
              </a>
              .
            </p>
          </EditorialProse>
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
      </EditorialContainer>
    </EditorialPage>
  );
}
