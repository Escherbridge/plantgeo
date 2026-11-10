import Link from "next/link";
import { editorialFocusRing } from "@/components/ui/editorial";
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
 * Legal links for every route that mounts the global bar. Height is
 * `--application-site-footer-height` so the shell can keep the map above it.
 */
export function SiteFooter({ pathname }: { pathname: string }) {
  return (
    <footer className="fixed inset-x-0 bottom-0 z-40 flex h-(--application-site-footer-height) items-stretch justify-end rule-top-heavy border-t-rule bg-paper px-page-inset">
      <nav aria-label="Footer" className="flex h-full min-w-0">
        {FOOTER_LINKS.map((item) => {
          const current = isCurrent(pathname, item.href);
          return (
            <Link
              key={item.href}
              href={item.href}
              aria-current={current ? "page" : undefined}
              className={cn(
                "flex items-center px-snug font-editorial-label text-nav uppercase",
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
