import type { ReactNode } from "react";
import { EditorialLink } from "@/components/ui/editorial";

const INLINE =
  /\*\*(.+?)\*\*|\*(.+?)\*|\[([^\]]+)\]\(([^)\s]+)\)|`([^`]+)`/g;

/** Renders markdown-lite inline formatting inside blog articles. External links stay follow links. */
export function EssayInline({ text }: { text: string }) {
  const nodes: ReactNode[] = [];
  let last = 0;
  let index = 0;
  for (const match of text.matchAll(INLINE)) {
    const start = match.index ?? 0;
    if (start > last) nodes.push(text.slice(last, start));
    if (match[1] !== undefined) {
      nodes.push(
        <strong key={index}>
          <EssayInline text={match[1]} />
        </strong>
      );
    } else if (match[2] !== undefined) {
      nodes.push(
        <em key={index}>
          <EssayInline text={match[2]} />
        </em>
      );
    } else if (match[3] !== undefined && match[4] !== undefined) {
      const label = match[3];
      const href = match[4];
      if (href.startsWith("/")) {
        nodes.push(
          <EditorialLink key={index} href={href}>
            {label}
          </EditorialLink>
        );
      } else if (href.startsWith("https://") || href.startsWith("http://")) {
        nodes.push(
          <a
            key={index}
            href={href}
            className="underline decoration-accent decoration-2 underline-offset-4"
          >
            {label}
          </a>
        );
      } else {
        nodes.push(label);
      }
    } else if (match[5] !== undefined) {
      nodes.push(
        <code key={index} className="font-editorial-label text-caption">
          {match[5]}
        </code>
      );
    }
    last = start + match[0].length;
    index += 1;
  }
  if (last < text.length) nodes.push(text.slice(last));
  return <>{nodes}</>;
}
