import Link from "next/link";
import { JsonLd } from "@/components/content/JsonLd";
import {
  EditorialContainer,
  EditorialHeadline,
  EditorialLead,
  EditorialLink,
  EditorialPage,
  EditorialProse,
  EditorialSubheading,
} from "@/components/ui/editorial";
import { essayStructuredData } from "@/lib/content/structured-data";
import { ESSAY_AUTHOR } from "@/lib/writing/essay-metadata";
import { EssayInline } from "@/components/writing/EssayInline";
import {
  blogPath,
  ESSAYS,
  essayDisplayDate,
  type Essay,
} from "@/lib/writing/essays";

export function EssayDocument({ essay }: { essay: Essay }) {
  const related = ESSAYS.filter((other) => other.slug !== essay.slug);

  return (
    <EditorialPage>
      <JsonLd data={essayStructuredData(essay)} />
      <EditorialContainer>
        <header className="pt-section pb-roomy">
          <p className="font-editorial-label text-label text-ink-muted uppercase">
            {essayDisplayDate(essay.date)} — {ESSAY_AUTHOR} — {essay.tags.join(" · ")}
          </p>
          <EditorialHeadline as="h1" className="mt-comfortable">
            {essay.title}
          </EditorialHeadline>
          <EditorialLead className="mt-roomy">{essay.summary}</EditorialLead>
        </header>
        <EditorialProse>
          {essay.paragraphs.map((paragraph) => (
            <p key={paragraph.slice(0, 48)}>
              <EssayInline text={paragraph} />
            </p>
          ))}
        </EditorialProse>
        <section className="pt-section">
          <EditorialSubheading as="h2">Sources</EditorialSubheading>
          <ul className="mt-comfortable max-w-measure font-editorial-text text-body text-ink">
            {essay.sources.map((source) => (
              <li key={source.href} className="mt-tight">
                <a
                  href={source.href}
                  className="underline decoration-accent decoration-2 underline-offset-4"
                >
                  {source.label}
                </a>
              </li>
            ))}
          </ul>
        </section>
        {related.length > 0 ? (
          <section className="pt-section">
            <EditorialSubheading as="h2">Related</EditorialSubheading>
            <ul className="mt-comfortable max-w-measure font-editorial-text text-body text-ink">
              {related.map((other) => (
                <li key={other.slug} className="mt-tight">
                  <Link
                    href={blogPath(other)}
                    className="underline decoration-accent decoration-2 underline-offset-4"
                  >
                    {other.title}
                  </Link>
                </li>
              ))}
            </ul>
          </section>
        ) : null}
        <p className="max-w-measure pt-section pb-chapter font-editorial-text text-body text-ink">
          <EditorialLink href="/blog">Blog</EditorialLink>
          {" · "}
          <EditorialLink href="/news">News</EditorialLink>
          {" · "}
          <EditorialLink href="/site-types">Site types</EditorialLink>
          {" · "}
          <EditorialLink href="/contact">Contact</EditorialLink>
          {" · "}
          <a
            href="/feed.xml"
            className="underline decoration-accent decoration-2 underline-offset-4"
          >
            RSS
          </a>
        </p>
      </EditorialContainer>
    </EditorialPage>
  );
}
