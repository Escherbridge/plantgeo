import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

type RuleWeight = "hairline" | "medium" | "heavy" | "massive";
type RuleTone = "rule" | "faint" | "accent" | "signal";

const ruleWeightClass: Record<RuleWeight, string> = {
  hairline: "rule-top-hairline",
  medium: "rule-top-medium",
  heavy: "rule-top-heavy",
  massive: "rule-top-massive",
};

const ruleToneClass: Record<RuleTone, string> = {
  rule: "border-t-rule",
  faint: "border-t-rule-faint",
  accent: "border-t-accent",
  signal: "border-t-signal",
};

interface BlockProps {
  children?: ReactNode;
  className?: string;
}

/**
 * Scroll surface for a document route. The global bar is a block in normal
 * flow, so the page owns the remaining viewport and scrolls inside it rather
 * than fighting the map shell's `body { overflow: hidden }`.
 */
export function EditorialPage({ children, className }: BlockProps) {
  return (
    <div
      className={cn(
        "viewport-below-top-bar overflow-y-auto bg-paper font-editorial-text text-body text-ink",
        className
      )}
    >
      {children}
    </div>
  );
}

export function EditorialContainer({ children, className }: BlockProps) {
  return (
    <div className={cn("mx-auto w-full max-w-page px-page-inset", className)}>
      {children}
    </div>
  );
}

/** Four columns on small screens, twelve above `md`. Nothing in between. */
export function EditorialGrid({ children, className }: BlockProps) {
  return (
    <div className={cn("grid grid-cols-4 gap-x-gutter md:grid-cols-12", className)}>
      {children}
    </div>
  );
}

export function EditorialRule({
  weight = "hairline",
  tone = "rule",
  className,
}: {
  weight?: RuleWeight;
  tone?: RuleTone;
  className?: string;
}) {
  return (
    <hr
      className={cn(
        "w-full",
        ruleWeightClass[weight],
        ruleToneClass[tone],
        className
      )}
    />
  );
}

/**
 * A numbered chapter: heavy rule, a sticky label column, and a content column
 * that never exceeds the reading measure unless the caller overrides it.
 */
export function EditorialSection({
  index,
  title,
  id,
  children,
  className,
}: BlockProps & {
  index?: string;
  title: string;
  id?: string;
}) {
  return (
    <section id={id} className={cn("pt-section pb-chapter", className)}>
      <EditorialRule weight="heavy" />
      <EditorialGrid className="pt-comfortable">
        <div className="col-span-4 md:col-span-3 md:self-start md:sticky md:top-roomy">
          {index && (
            <p className="font-editorial-label text-label text-accent uppercase">
              {index}
            </p>
          )}
          <h2 className="mt-tight font-editorial-display text-heading font-semibold text-ink">
            {title}
          </h2>
        </div>
        <div className="col-span-4 mt-comfortable md:col-span-8 md:col-start-5 md:mt-0">
          {children}
        </div>
      </EditorialGrid>
    </section>
  );
}

/** Flat bordered block. No radius, no shadow, no fill gradient -- by contract. */
export function EditorialPanel({ children, className }: BlockProps) {
  return (
    <div
      className={cn(
        "editorial-flat rule-all-hairline border-rule-faint bg-paper-recessed p-comfortable",
        className
      )}
    >
      {children}
    </div>
  );
}

export function EditorialColophon({ children, className }: BlockProps) {
  return (
    <footer
      className={cn(
        "rule-top-massive border-t-rule pt-comfortable pb-chapter",
        className
      )}
    >
      {children}
    </footer>
  );
}
