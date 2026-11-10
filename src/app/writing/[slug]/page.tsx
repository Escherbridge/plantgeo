import type { Metadata } from "next";
import { notFound } from "next/navigation";
import {
  EditorialContainer,
  EditorialEyebrow,
  EditorialHeadline,
  EditorialLead,
  EditorialPage,
  EditorialProse,
  EditorialSubheading,
} from "@/components/ui/editorial";
import { EssayInline } from "@/components/writing/EssayInline";
import { ESSAYS, essayBySlug, essayDisplayDate, essayPath } from "@/lib/writing/essays";

export function generateStaticParams() {
  return ESSAYS.map((essay) => ({ slug: essay.slug }));
}

export const dynamicParams = false;

export async function generateMetadata({
  params,
}: {
  params: Promise<{ slug: string }>;
}): Promise<Metadata> {
  const { slug } = await params;
  const essay = essayBySlug(slug);
  if (!essay) return {};
  return {
    title: `${essay.title} — PlantGeo`,
    description: essay.summary,
    alternates: { canonical: essayPath(essay) },
  };
}

export default async function EssayPage({
  params,
}: {
  params: Promise<{ slug: string }>;
}) {
  const { slug } = await params;
  const essay = essayBySlug(slug);
  if (!essay) notFound();

  return (
    <EditorialPage>
      <EditorialContainer>
        <header className="pt-section pb-roomy">
          <EditorialEyebrow>
            {essayDisplayDate(essay.date)} — {essay.tags.join(" · ")}
          </EditorialEyebrow>
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
        <section className="pt-section pb-chapter">
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
      </EditorialContainer>
    </EditorialPage>
  );
}
