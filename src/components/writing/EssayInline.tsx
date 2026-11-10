import type { ReactNode } from "react";

const INLINE = /\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)|`([^`]+)`/g;

/** Renders the draft's links and code spans. Links stay follow links. */
export function EssayInline({ text }: { text: string }) {
  const nodes: ReactNode[] = [];
  let last = 0;
  let index = 0;
  for (const match of text.matchAll(INLINE)) {
    const start = match.index ?? 0;
    if (start > last) nodes.push(text.slice(last, start));
    if (match[1] && match[2]) {
      nodes.push(
        <a key={index} href={match[2]}>
          {match[1]}
        </a>
      );
    } else if (match[3]) {
      nodes.push(
        <code key={index} className="font-editorial-label text-caption">
          {match[3]}
        </code>
      );
    }
    last = start + match[0].length;
    index += 1;
  }
  if (last < text.length) nodes.push(text.slice(last));
  return <>{nodes}</>;
}
