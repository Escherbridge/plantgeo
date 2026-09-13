import { AdviserCard } from "./AdviserCard";
import type { AdviserCardData } from "./types";

interface RelatedAdvisersSectionProps {
  advisers: AdviserCardData[];
}

/** Panel item 6: related advice. Uses `AdviserCard`'s distinct styling, never `OfficeCard`'s. */
export function RelatedAdvisersSection({ advisers }: RelatedAdvisersSectionProps) {
  return (
    <section aria-labelledby="land-context-advisers-heading">
      <h3 id="land-context-advisers-heading" className="text-sm font-semibold text-zinc-100">
        Related advice
      </h3>

      {advisers.length === 0 ? (
        <p className="mt-2 text-xs text-zinc-500">No topic-matched adviser is published for this selection.</p>
      ) : (
        <div className="mt-2 space-y-2">
          {advisers.map((card) => (
            <AdviserCard key={card.adviser.id} card={card} />
          ))}
        </div>
      )}
    </section>
  );
}
