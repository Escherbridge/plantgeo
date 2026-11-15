"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { editorialFocusRing } from "@/components/ui/editorial";
import { SISTER_SITES } from "@/lib/sister-sites";
import { cn } from "@/lib/utils";

const FOOTER_LINKS = [
  { href: "/blog", label: "Blog" },
  { href: "/news", label: "News" },
  { href: "/site-types", label: "Site types" },
  { href: "/guides", label: "Guides" },
  { href: "/contact", label: "Contact" },
  { href: "/privacy", label: "Privacy" },
  { href: "/terms", label: "Terms" },
] as const;

function isCurrent(pathname: string, href: string): boolean {
  return pathname === href || pathname.startsWith(`${href}/`);
}

function SisterLink({
  href,
  label,
  hidden,
}: {
  href: string;
  label: string;
  hidden?: boolean;
}) {
  return (
    <a
      href={href}
      aria-hidden={hidden ? true : undefined}
      tabIndex={hidden ? -1 : undefined}
      className={cn(
        "shrink-0 font-editorial-label text-caption text-ink-muted hover:text-ink",
        editorialFocusRing,
      )}
    >
      {label}
    </a>
  );
}

/**
 * Sister-site row. It moves on its own, so it can be paused, and it stays
 * still when the reader asked for reduced motion. The track is clipped so the
 * page does not grow a horizontal scrollbar.
 */
function SisterTicker() {
  const [paused, setPaused] = useState(false);

  useEffect(() => {
    if (typeof window.matchMedia !== "function") return;
    const media = window.matchMedia("(prefers-reduced-motion: reduce)");
    const apply = () => {
      if (media.matches) setPaused(true);
    };
    apply();
    media.addEventListener("change", apply);
    return () => media.removeEventListener("change", apply);
  }, []);

  return (
    <div className="flex h-11 min-w-0 items-center gap-comfortable overflow-hidden px-page-inset">
      <button
        type="button"
        aria-pressed={paused}
        onClick={() => setPaused((value) => !value)}
        className={cn(
          "shrink-0 border border-rule bg-paper px-snug font-editorial-label text-label text-ink uppercase",
          editorialFocusRing,
        )}
      >
        {paused ? "Play" : "Pause"}
      </button>
      <p className="shrink-0 font-editorial-label text-caption text-ink">An Escherbridge project</p>
      <div className="min-w-0 flex-1 overflow-hidden">
        <nav
          aria-label="Sister sites"
          className={cn(
            "flex w-max items-center gap-comfortable",
            !paused && "sister-ticker-track",
          )}
        >
          {SISTER_SITES.map((site) => (
            <SisterLink key={site.href} href={site.href} label={site.label} />
          ))}
          {paused
            ? null
            : SISTER_SITES.map((site) => (
                <SisterLink key={`loop-${site.href}`} href={site.href} label={site.label} hidden />
              ))}
        </nav>
      </div>
    </div>
  );
}

/**
 * Legal links plus one compact "An Escherbridge project" block. Height is
 * `--application-site-footer-height` so the shell can keep the map above it.
 * Sister anchors keep their descriptive casing and scroll in one row.
 */
export function SiteFooter({ pathname }: { pathname: string }) {
  return (
    <footer className="fixed inset-x-0 bottom-0 z-40 flex h-(--application-site-footer-height) flex-col overflow-x-hidden rule-top-heavy border-t-rule bg-paper">
      <SisterTicker />
      <nav aria-label="Footer" className="flex h-11 min-w-0 shrink-0 items-center justify-end overflow-x-auto scrollbar-hidden px-page-inset">
        {FOOTER_LINKS.map((item) => {
          const current = isCurrent(pathname, item.href);
          return (
            <Link
              key={item.href}
              href={item.href}
              aria-current={current ? "page" : undefined}
              className={cn(
                "flex h-full items-center px-snug font-editorial-label text-nav uppercase",
                current
                  ? "text-ink underline decoration-accent decoration-2 underline-offset-4"
                  : "text-ink-muted hover:text-ink",
                editorialFocusRing
              )}
            >
              {item.label}
            </Link>
          );
        })}
      </nav>
    </footer>
  );
}
