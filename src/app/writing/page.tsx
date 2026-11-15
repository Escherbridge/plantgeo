import type { Metadata } from "next";
import Link from "next/link";
import { MapLegalNote, PlanFunnelGate } from "@/components/content/PlanFunnelGate";
import {
  EditorialContainer,
  EditorialDisplay,
  EditorialEyebrow,
  EditorialPage,
  EditorialProse,
} from "@/components/ui/editorial";
import { blogPath, ESSAYS, essayDisplayDate } from "@/lib/writing/essays";

export const metadata: Metadata = {
  title: "Field Blog & Notes — PlantGeo",
  description:
    "Field walks and planning notes on reading sun, slope, drainage, wet spots, soils, and microclimates before staking beds or buying equipment.",
  alternates: { canonical: "/writing" },
};

export default function WritingIndexPage() {
  const posts = [...ESSAYS].sort(
    (a, b) => b.date.localeCompare(a.date) || a.slug.localeCompare(b.slug),
  );

  return (
    <EditorialPage>
      <EditorialContainer>
        <header className="pt-section pb-roomy">
          <EditorialEyebrow>Writing &amp; Field Stories</EditorialEyebrow>
          <EditorialDisplay className="mt-comfortable">Notes from the ground</EditorialDisplay>
          <PlanFunnelGate
            eyebrow="Try the live map"
            heading="Open the Ada County sample plan"
            body="Toggle terrain contours, drainage threads, wetlands, and soil map units side by side before walking your own yard or acreage."
          />
        </header>
        <ul className="rule-top-heavy border-t-rule">
          {posts.map((essay) => (
            <li key={essay.slug} className="rule-bottom-hairline border-b-rule-faint py-comfortable">
              <p className="font-editorial-label text-label text-ink-muted uppercase">
                {essayDisplayDate(essay.date)}
              </p>
              <h2 className="mt-tight font-editorial-display text-heading font-semibold text-ink">
                <Link
                  href={blogPath(essay)}
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
        <div className="pb-chapter">
          <MapLegalNote />
        </div>
      </EditorialContainer>
    </EditorialPage>
  );
}
