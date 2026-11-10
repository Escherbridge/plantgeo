import Link from "next/link";
import { editorialFocusRing } from "@/components/ui/editorial";
import { SISTER_SITES } from "@/lib/sister-sites";
import { cn } from "@/lib/utils";

const FOOTER_LINKS = [
  { href: "/contact", label: "Contact" },
  { href: "/privacy", label: "Privacy" },
  { href: "/terms", label: "Terms" },
] as const;

function isCurrent(pathname: string, href: string): boolean {
  return pathname === href || pathname.startsWith(`${href}/`);
}

/**
 * Legal links plus one compact "An Escherbridge project" block. Height is
 * `--application-site-footer-height` so the shell can keep the map above it.
 * Sister anchors keep their descriptive casing and scroll in one row.
 */
export function SiteFooter({ pathname }: { pathname: string }) {
  return (
    <footer className="fixed inset-x-0 bottom-0 z-40 flex h-(--application-site-footer-height) flex-col rule-top-heavy border-t-rule bg-paper">
      <div className="flex h-11 min-w-0 items-center gap-comfortable overflow-x-auto px-page-inset">
        <p className="sticky left-0 shrink-0 bg-paper pr-snug font-editorial-label text-caption text-ink">
          An Escherbridge project
        </p>
        <nav aria-label="Sister sites" className="flex items-center gap-comfortable">
          {SISTER_SITES.map((site) => (
            <a
              key={site.href}
              href={site.href}
              className={cn(
                "shrink-0 font-editorial-label text-caption text-ink-muted hover:text-ink",
                editorialFocusRing
              )}
            >
              {site.label}
            </a>
          ))}
        </nav>
      </div>
      <nav aria-label="Footer" className="flex h-11 shrink-0 items-center justify-end px-page-inset">
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
