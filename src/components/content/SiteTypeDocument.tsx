import type { ReactNode } from "react";
import { JsonLd } from "@/components/content/JsonLd";
import {
  EditorialContainer,
  EditorialEyebrow,
  EditorialHeading,
  EditorialHeadline,
  EditorialLink,
  EditorialPage,
  EditorialProse,
} from "@/components/ui/editorial";
import type { SiteTypeArticle, SiteTypeBlock, SiteTypeCitation } from "@/lib/content/site-types";
import { siteTypeStructuredData } from "@/lib/content/structured-data";

const TOKEN_SOURCE =
  "\\*\\*(.+?)\\*\\*|\\*(.+?)\\*|\\[([^\\]]+)\\]\\(([^)\\s]+)\\)|(https?:\\/\\/[^\\s<>\"']+)";

/** Draft inline marks: bold, italic, markdown links, and bare source URLs. */
export function RichText({ text }: { text: string }) {
  const nodes: ReactNode[] = [];
  let last = 0;
  let index = 0;
  for (const match of text.matchAll(new RegExp(TOKEN_SOURCE, "g"))) {
    const start = match.index ?? 0;
    if (start > last) nodes.push(text.slice(last, start));
    if (match[1] !== undefined) {
      nodes.push(
        <strong key={index}>
          <RichText text={match[1]} />
        </strong>,
      );
    } else if (match[2] !== undefined) {
      nodes.push(
        <em key={index}>
          <RichText text={match[2]} />
        </em>,
      );
    } else if (match[3] !== undefined && match[4] !== undefined) {
      nodes.push(<TextLink key={index} href={match[4]} label={match[3]} />);
    } else if (match[5] !== undefined) {
      const { href, trailing } = splitTrailingPunctuation(match[5]);
      nodes.push(<TextLink key={index} href={href} label={href} />);
      if (trailing) nodes.push(trailing);
    }
    last = start + match[0].length;
    index += 1;
  }
  if (last < text.length) nodes.push(text.slice(last));
  return <>{nodes}</>;
}

function splitTrailingPunctuation(url: string): { href: string; trailing: string } {
  const match = /^(.*?)([.,;:]+)$/.exec(url);
  if (!match?.[1]) return { href: url, trailing: "" };
  return { href: match[1], trailing: match[2] ?? "" };
}

function TextLink({ href, label }: { href: string; label: string }) {
  if (href.startsWith("/")) {
    return <EditorialLink href={href}>{label}</EditorialLink>;
  }
  if (href.startsWith("https://") || href.startsWith("http://")) {
    return <a href={href}>{label}</a>;
  }
  return label;
}

export function SiteTypeBlocks({
  blocks,
  sources = [],
}: {
  blocks: readonly SiteTypeBlock[];
  sources?: readonly SiteTypeCitation[];
}) {
  return (
    <>
      {blocks.map((block, index) => {
        if (block.type === "heading") {
          return (
            <EditorialHeading as="h2" key={`${block.type}-${index}`}>
              {block.text}
            </EditorialHeading>
          );
        }
        if (block.type === "list") {
          return (
            <ul key={`${block.type}-${index}`} className="list-disc pl-comfortable">
              {block.items.map((item) => (
                <li key={item} className="mt-tight">
                  <RichText text={item} />
                </li>
              ))}
            </ul>
          );
        }
        if (block.type === "sources") {
          return (
            <ol key={`${block.type}-${index}`} className="list-decimal pl-comfortable">
              {sources.map((source) => (
                <li key={source.text} className="mt-tight">
                  <RichText text={source.text} />
                </li>
              ))}
            </ol>
          );
        }
        return (
          <p key={`${block.type}-${index}`}>
            <RichText text={block.text} />
          </p>
        );
      })}
    </>
  );
}

export function SiteTypeDocument({ page }: { page: SiteTypeArticle }) {
  return (
    <EditorialPage>
      <JsonLd data={siteTypeStructuredData(page)} />
      <EditorialContainer>
        <header className="pt-section pb-roomy">
          <EditorialEyebrow>{page.eyebrow}</EditorialEyebrow>
          <EditorialHeadline as="h1" className="mt-comfortable">
            {page.h1}
          </EditorialHeadline>
        </header>
        <EditorialProse className="pb-chapter">
          <SiteTypeBlocks blocks={page.blocks} sources={page.sources} />
        </EditorialProse>
      </EditorialContainer>
    </EditorialPage>
  );
}
