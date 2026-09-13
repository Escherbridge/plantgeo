"use client";

import { useBotanicalOccurrenceStore } from "@/stores/botanical-occurrence-store";
import { BOTANICAL_DETAIL_MIN_ZOOM } from "@/lib/botanical-occurrences";
import { BOTANICAL_EFFORT_MEASURE_LABELS, type BotanicalEffortMeasure } from "@/components/map/layers/BotanicalCollectionEffortLayer";

/** Shown in place of the fields when no release has been pinned yet. Fetching must not start. */
export const BOTANICAL_NO_RELEASE_PINNED_MESSAGE =
  "No release pinned. Enter a release set id to start querying botanical occurrences.";

function LabeledInput({
  label,
  value,
  onChange,
  placeholder,
  type = "text",
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  type?: string;
}) {
  return (
    <label className="flex flex-col gap-1 text-xs text-[hsl(var(--foreground))]">
      {label}
      <input
        type={type}
        className="rounded-md border border-[hsl(var(--border))] bg-[hsl(var(--card))] px-2 py-1 text-sm"
        value={value}
        placeholder={placeholder}
        onChange={(event) => onChange(event.target.value)}
      />
    </label>
  );
}

/**
 * Filter controls for the botanical occurrence experience. `release_set_id` gates everything
 * else: until it is set, this panel renders `BOTANICAL_NO_RELEASE_PINNED_MESSAGE` in place of
 * the rest of the form and the map layers must not fetch (the store's `release_set_id: null`
 * default is what layers should check, not this component's render state).
 */
export function BotanicalFilters() {
  const filters = useBotanicalOccurrenceStore((state) => state.filters);
  const setFilters = useBotanicalOccurrenceStore((state) => state.setFilters);
  const setReleaseSetId = useBotanicalOccurrenceStore((state) => state.setReleaseSetId);

  return (
    <div className="flex flex-col gap-3">
      <LabeledInput
        label="Release set id (required)"
        value={filters.release_set_id ?? ""}
        onChange={setReleaseSetId}
        placeholder="e.g. release-2026-08"
      />

      {filters.release_set_id === null ? (
        <p role="status" className="text-xs text-[hsl(var(--muted-foreground))]">
          {BOTANICAL_NO_RELEASE_PINNED_MESSAGE}
        </p>
      ) : (
        <>
          <p className="text-xs text-[hsl(var(--muted-foreground))]">
            Source snapshot: <span className="font-mono">{filters.release_set_id}</span>
          </p>

          <LabeledInput
            label="Taxon concept id (exact match)"
            value={filters.taxon_concept_id}
            onChange={(value) => setFilters({ taxon_concept_id: value })}
            placeholder="e.g. WFO-0000123456"
          />
          <p className="text-xs text-[hsl(var(--muted-foreground))]">
            Exact taxon concept id only — there is no free-text name search.
          </p>

          <LabeledInput
            label="Family"
            value={filters.family}
            onChange={(value) => setFilters({ family: value })}
          />
          <LabeledInput
            label="Collection"
            value={filters.collection_key}
            onChange={(value) => setFilters({ collection_key: value })}
          />

          <div className="flex gap-2">
            <LabeledInput
              label="Collecting-event window: start"
              type="date"
              value={filters.event_start}
              onChange={(value) => setFilters({ event_start: value })}
            />
            <LabeledInput
              label="Collecting-event window: end"
              type="date"
              value={filters.event_end}
              onChange={(value) => setFilters({ event_end: value })}
            />
          </div>

          <label className="flex flex-col gap-1 text-xs text-[hsl(var(--foreground))]">
            Public spatial quality
            <select
              className="rounded-md border border-[hsl(var(--border))] bg-[hsl(var(--card))] px-2 py-1 text-sm"
              value={filters.spatial_quality}
              onChange={(event) =>
                setFilters({ spatial_quality: event.target.value as typeof filters.spatial_quality })
              }
            >
              <option value="confirmed">Confirmed only</option>
              <option value="possible">Include possible</option>
              <option value="all">All</option>
            </select>
          </label>

          <label className="flex flex-col gap-1 text-xs text-[hsl(var(--foreground))]">
            Collection-effort measure
            <select
              className="rounded-md border border-[hsl(var(--border))] bg-[hsl(var(--card))] px-2 py-1 text-sm"
              value={filters.effort_measure}
              onChange={(event) => setFilters({ effort_measure: event.target.value as BotanicalEffortMeasure })}
            >
              {Object.entries(BOTANICAL_EFFORT_MEASURE_LABELS).map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
          </label>

          <p className="text-xs text-[hsl(var(--muted-foreground))]">
            Zoom band: aggregate below z{BOTANICAL_DETAIL_MIN_ZOOM} / detail at z{BOTANICAL_DETAIL_MIN_ZOOM}+ (mutually exclusive)
          </p>
        </>
      )}
    </div>
  );
}
