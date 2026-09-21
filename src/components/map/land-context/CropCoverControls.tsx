"use client";

import { useEffect } from "react";
import { useCropCover } from "@/hooks/useCropCover";
import { useLandContextStore } from "@/stores/land-context-store";

export function CropCoverControls() {
  const { enabled, day, availability, query } = useCropCover();
  const setEnabled = useLandContextStore((state) => state.setCropCoverEnabled);
  const setReleaseDay = useLandContextStore((state) => state.setCropCoverReleaseDay);
  const available = availability.data?.available === true && !availability.isError;
  useEffect(() => {
    if (enabled && (availability.isError || availability.data?.available === false)) setEnabled(false);
  }, [enabled, availability.isError, availability.data, setEnabled]);

  return (
    <div className="px-1 py-1 text-xs">
      {available ? (
        <button type="button" role="switch" aria-checked={enabled} onClick={() => setEnabled(!enabled)}
          className="flex min-h-8 w-full items-center justify-between text-left">
          <span>Estimated crop cover</span><span>{enabled ? "On" : "Off"}</span>
        </button>
      ) : <p className="text-[hsl(var(--muted-foreground))]">Estimated crop cover — {availability.isError
        ? "availability could not be checked" : availability.data?.reason ?? "checking availability…"}</p>}
      {enabled && (
        <div className="space-y-2 text-[10px] text-[hsl(var(--muted-foreground))]">
          <label className="flex flex-col gap-1">Source edition published
            <select value={day ?? ""} onChange={(event) => setReleaseDay(event.target.value)}
              className="rounded bg-[hsl(var(--background))] p-1">
              {availability.data?.releaseDays.map((release) => <option key={release} value={release}>{release}</option>)}
            </select>
          </label>
          <p>USDA CDL classified imagery. Color shows crop area share in each grid cell, from 0% to 100%; it is not a confidence score or an ownership boundary.</p>
          <p>{query.isError ? "Crop-cover data could not be read." : query.isFetching ? "Reading crop-cover cells…" : query.data?.message}</p>
          {query.data?.geojson?.features.slice(0, 5).map((feature) => {
            const properties = feature.properties;
            return properties ? <div key={String(feature.id)} className="border-t border-[hsl(var(--border))] pt-1">
              <p>{String(properties.observed_year)}: {String(properties.dominant_crop_name ?? "No cultivated crop classified")}</p>
              <p>{(Number(properties.crop_fraction) * 100).toFixed(1)}% crop; {(Number(properties.classified_fraction) * 100).toFixed(1)}% classified.</p>
              <p>{Number(properties.aggregation_cell_m).toLocaleString()} m cell; {String(properties.analysis_resolution_m)} m analysis from {String(properties.source_resolution_m)} m source.</p>
              <a href={String(properties.source_url)} target="_blank" rel="noreferrer" className="underline">USDA source and provenance</a>
            </div> : null;
          })}
          {(query.data?.geojson?.features.length ?? 0) > 5 && <p>Showing details for the first five cells in this view. Zoom in to inspect a smaller area.</p>}
        </div>
      )}
    </div>
  );
}
