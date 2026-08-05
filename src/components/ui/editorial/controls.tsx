import Link from "next/link";
import type { ComponentProps, ReactNode } from "react";
import { cn } from "@/lib/utils";

/**
 * Focus rings are drawn *inside* the control (negative offset) so full-bleed
 * chrome like the top bar can never clip them. That makes the ring's contrast a
 * function of the fill it lands on, which is why there are two of them --
 * a single accent ring fails WCAG 2.2 SC 1.4.11 on ink-filled controls
 * (2.16:1 dark, 2.96:1 light). See src/components/ui/editorial/AGENTS.md §focus.
 */
export const editorialFocusRing =
  "focus-visible:outline-2 focus-visible:outline-accent focus-visible:-outline-offset-2";

/** For ink- or accent-filled controls: paper clears 3:1 against both, in both themes. */
export const editorialFocusRingInverse =
  "focus-visible:outline-2 focus-visible:outline-paper focus-visible:-outline-offset-2";

/* `disabled:opacity-70` rather than 50: at 50% the label on a solid control
   drops to 3.51:1 against its own dimmed fill in the light theme, and a
   disabled button still has to be readable to explain why it is disabled. */
const blockActionClassName =
  "editorial-flat inline-flex items-center justify-center px-comfortable py-snug font-editorial-label text-label uppercase transition-colors disabled:opacity-70";

/* Hover shifts fill and ink rather than dimming the whole control: opacity
   hover lowers contrast at exactly the moment the user is aiming at the thing. */
const solidActionClassName = "bg-ink text-paper hover:bg-accent hover:text-accent-ink";

const outlineActionClassName =
  "rule-all-hairline border-rule bg-transparent text-ink hover:border-accent hover:text-accent";

/** Inline link inside running text: rule-thick underline, no colour change. */
export function EditorialLink({
  className,
  ...props
}: ComponentProps<typeof Link>) {
  return (
    <Link
      {...props}
      className={cn(
        "underline decoration-accent decoration-2 underline-offset-4 hover:decoration-4",
        editorialFocusRing,
        className
      )}
    />
  );
}

export function EditorialActionLink({
  tone = "solid",
  className,
  ...props
}: ComponentProps<typeof Link> & { tone?: "solid" | "outline" }) {
  return (
    <Link
      {...props}
      className={cn(
        blockActionClassName,
        tone === "solid" ? solidActionClassName : outlineActionClassName,
        tone === "solid" ? editorialFocusRingInverse : editorialFocusRing,
        className
      )}
    />
  );
}

export function EditorialButton({
  tone = "solid",
  className,
  type = "button",
  ...props
}: ComponentProps<"button"> & { tone?: "solid" | "outline" }) {
  return (
    <button
      {...props}
      type={type}
      className={cn(
        blockActionClassName,
        tone === "solid" ? solidActionClassName : outlineActionClassName,
        tone === "solid" ? editorialFocusRingInverse : editorialFocusRing,
        className
      )}
    />
  );
}

/**
 * Honest state block. `signal` is reserved for "this data is not available and
 * here is why" -- never for decoration.
 */
export function EditorialNotice({
  tone = "neutral",
  title,
  children,
  className,
  role,
}: {
  tone?: "neutral" | "signal";
  title?: ReactNode;
  children?: ReactNode;
  className?: string;
  role?: "status" | "alert";
}) {
  return (
    <div
      role={role}
      className={cn(
        "editorial-flat rule-left-heavy pl-comfortable",
        tone === "signal" ? "border-l-signal" : "border-l-rule-faint",
        className
      )}
    >
      {title && (
        <p
          className={cn(
            "font-editorial-label text-label uppercase",
            tone === "signal" ? "text-signal" : "text-ink-muted"
          )}
        >
          {title}
        </p>
      )}
      <div className="mt-tight max-w-measure font-editorial-text text-body text-ink">
        {children}
      </div>
    </div>
  );
}

export function EditorialTag({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <span
      className={cn(
        "editorial-flat rule-all-hairline inline-block border-rule-faint px-tight py-hairline font-editorial-label text-label text-ink-muted uppercase",
        className
      )}
    >
      {children}
    </span>
  );
}
