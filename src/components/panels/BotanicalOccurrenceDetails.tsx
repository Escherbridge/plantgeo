"use client";

import type { BotanicalOccurrenceFeature } from "@/lib/botanical-occurrences";

/**
 * The one disclosure line every specimen-record view must carry, verbatim -- a governed
 * scientific-honesty requirement from the track spec, not editorial copy. The test suite
 * asserts this exact string appears; do not paraphrase it.
 */
export const BOTANICAL_SPECIMEN_DISCLOSURE =
  "A specimen record documents a collection event; it does not prove current occupancy, abundance or absence.";

/** Formats an event interval as e.g. "1987 (year precision)" or "1987-04 – 1987-06 (interval)". */
export function formatEventInterval(interval: BotanicalOccurrenceFeature["event_interval"]): string {
  const { start, end, precision } = interval;
  if (!start && !end) return `unknown date (${precision} precision)`;
  if (start && end && start !== end) return `${start} – ${end} (${precision} precision)`;
  return `${start ?? end} (${precision} precision)`;
}

interface DetailRowProps {
  label: string;
  value: string;
}

function DetailRow({ label, value }: DetailRowProps) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-[hsl(var(--muted-foreground))] text-xs">{label}</span>
      <span className="text-[hsl(var(--foreground))] text-sm">{value}</span>
    </div>
  );
}

interface BotanicalOccurrenceDetailsProps {
  feature: BotanicalOccurrenceFeature | null;
}

/**
 * Detail panel for a single selected specimen record, opened by
 * `BotanicalOccurrencesLayer`'s click handler. Follows the field-list layout other detail
 * panels in this directory use (see `SoilDetails.tsx`'s point-query card) rather than
 * inventing a new card shape.
 */
export function BotanicalOccurrenceDetails({ feature }: BotanicalOccurrenceDetailsProps) {
  if (!feature) {
    return (
      <p className="text-xs text-[hsl(var(--muted-foreground))]">
        Select a specimen point on the map to see its record.
      </p>
    );
  }

  return (
    <div className="flex flex-col gap-3">
      <div>
        <h3 className="text-sm font-semibold text-[hsl(var(--foreground))]">{feature.scientific_name}</h3>
        <p className="text-xs text-[hsl(var(--muted-foreground))]">
          {feature.family ?? "Family not recorded"} · resolution: {feature.resolution_state}
        </p>
      </div>

      <DetailRow label="Collecting-event date" value={formatEventInterval(feature.event_interval)} />
      <DetailRow label="Contributing collection" value={feature.collection_key} />
      <DetailRow label="Catalog number" value={feature.catalog_number ?? "not recorded"} />
      <DetailRow label="Recorded by" value={feature.recorded_by ?? "not recorded"} />
      <DetailRow
        label="Coordinate uncertainty"
        value={
          feature.coordinate_uncertainty_m != null
            ? `${feature.coordinate_uncertainty_m} m (${feature.spatial_class})`
            : `unknown (${feature.spatial_class})`
        }
      />
      <DetailRow label="Membership" value={feature.membership} />
      <DetailRow label="Basis of record" value={feature.basis_of_record ?? "not recorded"} />

      {feature.rights_uri && (
        <a
          href={feature.rights_uri}
          target="_blank"
          rel="noreferrer noopener"
          className="text-xs text-[hsl(var(--primary))] underline"
        >
          Rights & provenance
        </a>
      )}
      {feature.attribution_text && (
        <p className="text-xs text-[hsl(var(--muted-foreground))]">{feature.attribution_text}</p>
      )}

      <p role="note" className="text-xs italic text-[hsl(var(--muted-foreground))] border-t border-[hsl(var(--border))] pt-2">
        {BOTANICAL_SPECIMEN_DISCLOSURE}
      </p>
    </div>
  );
}
