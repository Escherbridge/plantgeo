"use client";

import { useId } from "react";
import {
  DATA_INTERVENTION_LANES,
  type DataInterventionDetails,
} from "@/lib/environmental/data-intervention";

interface DataInterventionFieldsProps {
  type: string;
  value: DataInterventionDetails;
  onChange: (value: DataInterventionDetails) => void;
}

/** Shared collection and submission fields; see ../AGENTS.md. */
export function DataInterventionFields({
  type,
  value,
  onChange,
}: DataInterventionFieldsProps) {
  const id = useId();
  const submission = type === "data_submission";
  const inputClass = "min-h-11 w-full rounded-lg border border-[hsl(var(--border))] bg-[hsl(var(--card))] px-3 py-2 text-sm text-[hsl(var(--foreground))] focus:outline-none focus:ring-2 focus:ring-[hsl(var(--ring))]";

  return (
    <fieldset
      className="space-y-3 rounded-lg border border-[hsl(var(--border))] p-3"
      aria-describedby={`${id}-origin`}
    >
      <legend className="px-1 text-sm font-medium">
        {submission ? "Data submission" : "Data collection plan"}
      </legend>
      <p id={`${id}-origin`} className="text-xs text-[hsl(var(--muted-foreground))]">
        {submission ? "Community collected data." : "Community collection plan."}{" "}
        Review and publication keep this contribution separate from verified source data.
      </p>
      <div className="space-y-1">
        <label htmlFor={`${id}-lane`} className="block text-sm font-medium">
          Data lane <span aria-hidden="true">*</span>
        </label>
        <select
          id={`${id}-lane`}
          required
          value={value.lane}
          onChange={(event) => onChange({ ...value, lane: event.target.value })}
          className={inputClass}
        >
          <option value="">Select a data lane</option>
          {DATA_INTERVENTION_LANES.map((lane) => (
            <option key={lane.id} value={lane.id}>{lane.label}</option>
          ))}
        </select>
      </div>
      <div className="space-y-1">
        <label htmlFor={`${id}-method`} className="block text-sm font-medium">
          Collection method <span aria-hidden="true">*</span>
        </label>
        <textarea
          id={`${id}-method`}
          required
          rows={3}
          maxLength={2000}
          value={value.collectionMethod}
          onChange={(event) => onChange({ ...value, collectionMethod: event.target.value })}
          placeholder={submission
            ? "How, where, and with which instruments was the data collected?"
            : "How will volunteers collect and check the data?"}
          className={inputClass}
        />
      </div>
      {submission && (
        <>
          <div className="space-y-1">
            <label htmlFor={`${id}-day`} className="block text-sm font-medium">
              Observation day <span aria-hidden="true">*</span>
            </label>
            <input
              id={`${id}-day`}
              type="date"
              required
              value={value.observedOn ?? ""}
              onChange={(event) => onChange({ ...value, observedOn: event.target.value || undefined })}
              className={inputClass}
            />
          </div>
          <div className="space-y-1">
            <label htmlFor={`${id}-url`} className="block text-sm font-medium">
              Dataset or evidence link <span aria-hidden="true">*</span>
            </label>
            <input
              id={`${id}-url`}
              type="url"
              required
              maxLength={2048}
              value={value.dataUrl ?? ""}
              onChange={(event) => onChange({ ...value, dataUrl: event.target.value || undefined })}
              aria-describedby={`${id}-url-help`}
              placeholder="https://example.org/observations"
              className={inputClass}
            />
            <p id={`${id}-url-help`} className="text-xs text-[hsl(var(--muted-foreground))]">
              Use an HTTP or HTTPS link that reviewers can open. Approved submissions make this link public.
            </p>
          </div>
        </>
      )}
    </fieldset>
  );
}
