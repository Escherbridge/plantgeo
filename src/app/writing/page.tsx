import type { Metadata } from "next";
import Link from "next/link";
import {
  EditorialContainer,
  EditorialDisplay,
  EditorialEyebrow,
  EditorialPage,
  EditorialProse,
} from "@/components/ui/editorial";
import { ESSAYS, essayDisplayDate, essayPath } from "@/lib/writing/essays";

export const metadata: Metadata = {
  title: "Notes — PlantGeo",
  description: "Not forecast beyond today. Plan the land, then pick the tools.",
  alternates: { canonical: "/writing" },
};

export default function WritingIndexPage() {
  return (
    <EditorialPage>
      <EditorialContainer>
        <header className="pt-section pb-roomy">
          <EditorialEyebrow>Writing</EditorialEyebrow>
          <EditorialDisplay className="mt-comfortable">Notes</EditorialDisplay>
        </header>
        <ul className="rule-top-heavy border-t-rule">
          {ESSAYS.map((essay) => (
            <li key={essay.slug} className="rule-bottom-hairline border-b-rule-faint py-comfortable">
              <p className="font-editorial-label text-label text-ink-muted uppercase">
                {essayDisplayDate(essay.date)}
              </p>
              <h2 className="mt-tight font-editorial-display text-heading font-semibold text-ink">
                <Link
                  href={essayPath(essay)}
                  className="underline decoration-accent decoration-2 underline-offset-4"
                >
                  {essay.title}
                </Link>
              </h2>
              <EditorialProse className="mt-tight">
                <p>{essay.summary}</p>
              </EditorialProse>
            </li>
          ))}
        </ul>
      </EditorialContainer>
    </EditorialPage>
  );
}
