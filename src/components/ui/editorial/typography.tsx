import type { ElementType, ReactNode } from "react";
import { cn } from "@/lib/utils";

interface TextProps {
  children?: ReactNode;
  className?: string;
  as?: ElementType;
}

function renderText(
  defaultElement: ElementType,
  baseClassName: string,
  { as, className, children }: TextProps
) {
  const Element = as ?? defaultElement;
  return <Element className={cn(baseClassName, className)}>{children}</Element>;
}

/** Mono, tracked, uppercase. The system's only decorative voice. */
export function EditorialEyebrow(props: TextProps) {
  return renderText(
    "p",
    "font-editorial-label text-label text-ink-muted uppercase",
    props
  );
}

export function EditorialDisplay(props: TextProps) {
  return renderText(
    "h1",
    "font-editorial-display text-display font-black text-balance text-ink",
    props
  );
}

export function EditorialHeadline(props: TextProps) {
  return renderText(
    "h2",
    "font-editorial-display text-headline font-extrabold text-balance text-ink",
    props
  );
}

export function EditorialTitle(props: TextProps) {
  return renderText(
    "h2",
    "font-editorial-display text-title font-bold text-balance text-ink",
    props
  );
}

export function EditorialHeading(props: TextProps) {
  return renderText(
    "h3",
    "font-editorial-display text-heading font-semibold text-ink",
    props
  );
}

export function EditorialSubheading(props: TextProps) {
  return renderText(
    "h4",
    "font-editorial-display text-subheading font-medium text-ink",
    props
  );
}

/** The deck under a headline. Serif, one step up from body, never justified. */
export function EditorialLead(props: TextProps) {
  return renderText(
    "p",
    "max-w-measure font-editorial-text text-lead text-ink",
    props
  );
}

export function EditorialCaption(props: TextProps) {
  return renderText(
    "p",
    "font-editorial-label text-caption text-ink-muted",
    props
  );
}

/** Long-form container: constrains the measure and sets vertical rhythm. */
export function EditorialProse(props: TextProps) {
  return renderText(
    "div",
    cn(
      "max-w-measure font-editorial-text text-body text-ink",
      "[&>*+*]:mt-comfortable",
      "[&_em]:italic [&_strong]:font-semibold",
      "[&_a]:underline [&_a]:decoration-accent [&_a]:decoration-2 [&_a]:underline-offset-4"
    ),
    props
  );
}

export function EditorialPullQuote(props: TextProps) {
  return renderText(
    "blockquote",
    "rule-left-heavy border-l-accent pl-comfortable font-editorial-text text-lead italic text-ink",
    props
  );
}

/**
 * Ruled definition rows -- the workhorse for stack tables, parity tables and
 * metadata blocks. Rows collapse to stacked pairs below `md`.
 */
export function EditorialDefinitionList({
  items,
  className,
}: {
  items: { term: ReactNode; description: ReactNode; note?: ReactNode }[];
  className?: string;
}) {
  return (
    <dl className={cn("rule-top-medium border-t-rule", className)}>
      {items.map((item, position) => (
        <div
          key={position}
          className="rule-bottom-hairline grid grid-cols-4 gap-x-gutter border-b-rule-faint py-snug md:grid-cols-12"
        >
          <dt className="col-span-4 font-editorial-label text-label text-ink uppercase md:col-span-4 md:pt-hairline">
            {item.term}
          </dt>
          <dd className="col-span-4 mt-tight font-editorial-text text-caption text-ink-muted md:col-span-6 md:mt-0">
            {item.description}
          </dd>
          {item.note !== undefined && (
            <div className="col-span-4 mt-tight font-editorial-label text-label text-accent uppercase md:col-span-2 md:mt-0 md:text-right">
              {item.note}
            </div>
          )}
        </div>
      ))}
    </dl>
  );
}
