import { EVIDENCE_STATUS_PRESENTATION } from "./land-context-utils";
import type { EvidenceStatus } from "./types";

interface StalenessIndicatorProps {
  status: EvidenceStatus;
  /** Optional extra context, e.g. "checked 2026-07-01" or the broken URL. */
  note?: string;
}

const STATUS_TEXT_COLOR: Record<EvidenceStatus, string> = {
  current: "text-emerald-300",
  stale: "text-amber-300",
  unverified: "text-zinc-400",
  broken_link: "text-red-400",
};

/**
 * Icon + text label together, never color alone -- spec accessibility section X4. The icon is
 * a plain glyph (not an svg-only icon font) so it survives to a screen reader/plain-text render
 * alongside the label; do not remove the text span to "clean up" the UI.
 */
export function StalenessIndicator({ status, note }: StalenessIndicatorProps) {
  const presentation = EVIDENCE_STATUS_PRESENTATION[status];

  return (
    <span className={`inline-flex items-center gap-1 text-xs ${STATUS_TEXT_COLOR[status]}`}>
      <span aria-hidden="true">{presentation.icon}</span>
      <span>{presentation.label}</span>
      {note ? <span className="text-zinc-500">({note})</span> : null}
    </span>
  );
}
