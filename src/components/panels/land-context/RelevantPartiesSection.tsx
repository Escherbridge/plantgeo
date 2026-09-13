import { OfficeCard } from "./OfficeCard";
import { dedupeOfficeCards } from "./land-context-utils";
import type { OfficeCardData } from "./types";

interface RelevantPartiesSectionProps {
  officeCards: OfficeCardData[];
}

/**
 * Panel item 2: relevant parties. Dedupes on office id via `dedupeOfficeCards` before render --
 * one card per office, every relationship still listed inside it (spec "Deduplicate a shared
 * office card ... while retaining every relevant feature/relationship").
 */
export function RelevantPartiesSection({ officeCards }: RelevantPartiesSectionProps) {
  const deduped = dedupeOfficeCards(officeCards);

  return (
    <section aria-labelledby="land-context-parties-heading">
      <h3 id="land-context-parties-heading" className="text-sm font-semibold text-zinc-100">
        Relevant parties
      </h3>

      {deduped.length === 0 ? (
        <p className="mt-2 text-xs text-zinc-500">No published office/program route resolved for this selection.</p>
      ) : (
        <div className="mt-2 space-y-2">
          {deduped.map((card) => (
            <OfficeCard key={card.office.id} card={card} />
          ))}
        </div>
      )}
    </section>
  );
}
