import Link from "next/link";
import type { ComponentProps, ReactNode } from "react";
import { cn } from "@/lib/utils";

export const editorialFocusRing =
  "focus-visible:outline-2 focus-visible:outline-accent focus-visible:-outline-offset-2";

const blockActionClassName =
  "editorial-flat inline-flex items-center justify-center px-comfortable py-snug font-editorial-label text-label uppercase transition-opacity hover:opacity-85 disabled:opacity-50";

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
        tone === "solid"
          ? "bg-ink text-paper"
          : "rule-all-hairline border-rule bg-transparent text-ink",
        editorialFocusRing,
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
        tone === "solid"
          ? "bg-ink text-paper"
          : "rule-all-hairline border-rule bg-transparent text-ink",
        editorialFocusRing,
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
