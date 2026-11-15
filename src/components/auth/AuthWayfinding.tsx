import Link from "next/link";

/**
 * First tab stop on the auth, invite, and join shells. The arrow is visual;
 * the accessible name is the words.
 */
export function AuthWayfinding({ contentId }: { contentId: string }) {
  return (
    <div className="absolute left-4 top-4 z-20 flex flex-col items-start gap-2">
      <a
        href={`#${contentId}`}
        className="sr-only rounded-md bg-zinc-100 px-3 py-2 text-sm font-medium text-zinc-950 focus:not-sr-only focus:outline-none focus:ring-2 focus:ring-emerald-400"
      >
        Skip to the form
      </a>
      <Link
        href="/"
        className="rounded-md text-sm text-zinc-300 underline decoration-emerald-500/70 underline-offset-4 hover:text-zinc-50 focus:outline-none focus:ring-2 focus:ring-emerald-400"
      >
        <span aria-hidden="true">← </span>
        Back to the map
      </Link>
    </div>
  );
}
