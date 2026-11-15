import Link from "next/link";
import { JsonLd } from "@/components/content/JsonLd";
import { MapLegalNote, PlanFunnelGate } from "@/components/content/PlanFunnelGate";
import {
  EditorialContainer,
  EditorialHeading,
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

type EssayBlock =
  | { type: "heading"; text: string }
  | { type: "list"; items: string[] }
  | { type: "paragraph"; text: string };

function groupParagraphs(paragraphs: readonly string[]): EssayBlock[] {
  const blocks: EssayBlock[] = [];
  for (const entry of paragraphs) {
    if (entry.startsWith("## ")) {
      blocks.push({ type: "heading", text: entry.slice(3).trim() });
    } else if (entry.startsWith("- ")) {
      const item = entry.slice(2).trim();
      const prev = blocks.at(-1);
      if (prev?.type === "list") {
        prev.items.push(item);
      } else {
        blocks.push({ type: "list", items: [item] });
      }
    } else {
      blocks.push({ type: "paragraph", text: entry });
    }
  }
  return blocks;
}

export function EssayDocument({ essay }: { essay: Essay }) {
  const related = ESSAYS.filter((other) => other.slug !== essay.slug);
  const blocks = groupParagraphs(essay.paragraphs);

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
          {blocks.map((block, index) => {
            if (block.type === "heading") {
              return (
                <EditorialHeading as="h2" key={`h-${index}`}>
                  {block.text}
                </EditorialHeading>
              );
            }
            if (block.type === "list") {
              return (
                <ul key={`l-${index}`} className="list-disc pl-comfortable">
                  {block.items.map((item) => (
                    <li key={item.slice(0, 48)} className="mt-tight">
                      <EssayInline text={item} />
                    </li>
                  ))}
                </ul>
              );
            }
            return (
              <p key={`p-${index}`}>
                <EssayInline text={block.text} />
              </p>
            );
          })}
        </EditorialProse>
        <PlanFunnelGate />
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
            <EditorialSubheading as="h2">More field stories</EditorialSubheading>
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
        <MapLegalNote author={ESSAY_AUTHOR} lastReviewed={essay.date} />
        <p className="max-w-measure pt-comfortable pb-chapter font-editorial-text text-body text-ink">
          <EditorialLink href="/blog">Blog</EditorialLink>
          {" · "}
          <EditorialLink href="/guides">Field guides</EditorialLink>
          {" · "}
          <EditorialLink href="/site-types">Site types</EditorialLink>
          {" · "}
          <EditorialLink href="/plan/sample-ada-county">Sample plan</EditorialLink>
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
