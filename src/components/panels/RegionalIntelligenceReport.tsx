"use client";

import { useId, useMemo, useState } from 'react';
import { ChevronDown } from 'lucide-react';
import { AI_GENERATED_DISCLAIMER, type RegionalIntelligenceResponse } from '@/lib/regional-intelligence';
import {
  KEY_ITEM_LIMIT,
  NO_RECOMMENDATION_TEXT,
  NOT_GROUNDED_LABEL,
  PARTIAL_NOTE,
  buildReportView,
  citationSummary,
  recommendationLine,
  reportViewToMarkdown,
  sourceRowSummary,
  type ReportView,
  type SourceRow,
  type SourceStatus,
  type SourcesView,
} from '@/lib/regional-evidence-presentation';
import { ReportErrorBoundary } from './ReportErrorBoundary';

// Minimal report: risk + headline, ≤3 findings, ≤3 recommendations, one consult line, one
// collapsed Sources disclosure, one footer with a single actions menu. See `./AGENTS.md`
// §"Regional report view-model" for what was removed and why.

const RISK_TONES: Record<string, { rule: string; text: string }> = {
  low: { rule: 'border-emerald-600', text: 'text-emerald-800 dark:text-emerald-300' },
  moderate: { rule: 'border-amber-500', text: 'text-amber-800 dark:text-amber-300' },
  high: { rule: 'border-orange-600', text: 'text-orange-800 dark:text-orange-300' },
  critical: { rule: 'border-red-600', text: 'text-red-800 dark:text-red-300' },
};
const UNKNOWN_RISK_TONE = { rule: 'border-gray-400', text: 'text-gray-700 dark:text-gray-300' };

const STATUS_DOTS: Record<SourceStatus, string> = {
  Found: 'bg-emerald-500',
  'Nearest day': 'bg-sky-500',
  'Nearest cell': 'bg-sky-500',
  'Static layer': 'bg-gray-400',
  'Not published': 'bg-amber-500',
  Error: 'bg-red-500',
};

const MUTED = 'text-gray-600 dark:text-gray-400';
const SECTION_HEADING = `font-editorial-label text-[11px] font-medium uppercase tracking-[0.12em] ${MUTED}`;
const DISCLOSURE_BUTTON =
  'rounded focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500';

function DisclosureChevron({ open }: { open: boolean }) {
  return <ChevronDown aria-hidden="true" className={`h-3.5 w-3.5 shrink-0 transition-transform ${open ? 'rotate-180' : ''}`} />;
}

/** Shows the first KEY_ITEM_LIMIT items and one "Show all" toggle; the export always carries every item. */
function useCollapsedList<T>(items: T[]) {
  const [expanded, setExpanded] = useState(false);
  const hidden = Math.max(0, items.length - KEY_ITEM_LIMIT);
  return { visible: expanded ? items : items.slice(0, KEY_ITEM_LIMIT), hidden, expanded, setExpanded };
}

function ShowAllButton({ hidden, expanded, onToggle, controls }: {
  hidden: number; expanded: boolean; onToggle: () => void; controls: string;
}) {
  if (hidden === 0) return null;
  return (
    <button type="button" aria-expanded={expanded} aria-controls={controls} onClick={onToggle}
      className={`${DISCLOSURE_BUTTON} min-h-8 text-xs text-blue-700 underline-offset-2 hover:underline dark:text-blue-400`}>
      {expanded ? 'Show fewer' : `Show ${hidden} more`}
    </button>
  );
}

function SourceRowItem({ row }: { row: SourceRow }) {
  const [open, setOpen] = useState(false);
  const detailsId = useId();
  const summary = sourceRowSummary(row);
  const hasDetails = row.details.length > 0 || row.citations.length > 0;
  const dot = <span aria-hidden="true" className={`mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full ${STATUS_DOTS[row.status]}`} />;
  if (!hasDetails) {
    return <div className="flex min-h-8 items-start gap-2 py-1">{dot}<span className="min-w-0 break-words">{summary}</span></div>;
  }
  return (
    <div>
      <button type="button" aria-expanded={open} aria-controls={detailsId} onClick={() => setOpen(!open)}
        className={`${DISCLOSURE_BUTTON} flex min-h-8 w-full items-start gap-2 py-1 text-left hover:bg-gray-50 dark:hover:bg-gray-800`}>
        {dot}
        <span className="min-w-0 flex-1 break-words">{summary}</span>
        <DisclosureChevron open={open} />
      </button>
      {open && (
        <ul id={detailsId} className={`mb-1 ml-3.5 space-y-0.5 border-l pl-2 dark:border-gray-700 ${MUTED}`}>
          {row.details.map((detail) => <li key={detail} className="break-words">{detail}</li>)}
          {row.citations.map((citation) => (
            <li key={citation.recordId} className="break-words">
              {citationSummary(citation)}
              {citation.url && (
                <>
                  {' · '}
                  <a href={citation.url} target="_blank" rel="noopener noreferrer nofollow"
                    className="text-blue-700 underline dark:text-blue-400">
                    Source <span className="sr-only">for {citation.title} (opens in a new tab)</span>
                  </a>
                </>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** One collapsed "Sources" disclosure: one row per lane, then gaps, caveats and web sources inside it. */
export function SourcesDisclosure({ sources, heading = 'Sources' }: { sources: SourcesView; heading?: string }) {
  const [open, setOpen] = useState(false);
  const panelId = useId();
  const { rows, gaps, caveats, webSources } = sources;
  if (!rows.length && !gaps.length && !caveats.length && !webSources.length) return null;
  return (
    <section className="border-t pt-1 text-xs dark:border-gray-700">
      <h4>
        <button type="button" aria-expanded={open} aria-controls={panelId} onClick={() => setOpen(!open)}
          className={`${DISCLOSURE_BUTTON} flex min-h-11 w-full items-center justify-between gap-2 font-semibold`}>
          <span>{heading} ({rows.length})</span>
          <DisclosureChevron open={open} />
        </button>
      </h4>
      {open && (
        <div id={panelId} className="space-y-2 pb-2">
          {rows.length > 0 && (
            <ul aria-label="Source lanes" className="font-editorial-label text-[11px]">
              {rows.map((row) => <li key={row.laneId}><SourceRowItem row={row} /></li>)}
            </ul>
          )}
          {gaps.length > 0 && (
            <div>
              <h5 className="font-semibold">Gaps</h5>
              <ul className={`mt-0.5 space-y-0.5 ${MUTED}`}>
                {gaps.map((gap) => <li key={gap.laneId} className="break-words">{gap.label}: {gap.text}</li>)}
              </ul>
            </div>
          )}
          {caveats.length > 0 && (
            <div>
              <h5 className="font-semibold">Caveats</h5>
              <ul className={`mt-0.5 space-y-0.5 ${MUTED}`}>
                {caveats.map((caveat) => <li key={caveat} className="break-words">{caveat}</li>)}
              </ul>
            </div>
          )}
          {webSources.length > 0 && (
            <div>
              <h5 className="font-semibold">Web sources</h5>
              <ul className="mt-0.5 space-y-0.5">
                {webSources.map((source) => (
                  <li key={source.url} className="break-words">
                    <a href={source.url} target="_blank" rel="noopener noreferrer nofollow"
                      className="text-blue-700 underline dark:text-blue-400">{source.title}</a>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </section>
  );
}

function downloadFile(content: string, mimeType: string, extension: string) {
  const url = URL.createObjectURL(new Blob([content], { type: mimeType }));
  const link = document.createElement('a');
  link.href = url;
  link.download = `regional-analysis-${Date.now()}.${extension}`;
  link.click();
  URL.revokeObjectURL(url);
}

/** The single per-report actions menu (copy + both exports), replacing three repeated toolbars. */
function ReportActions({ response, view }: { response: RegionalIntelligenceResponse; view: ReportView }) {
  const [open, setOpen] = useState(false);
  const [status, setStatus] = useState('');
  const menuId = useId();
  const run = (action: () => void | Promise<void>) => async () => {
    setOpen(false);
    await action();
  };
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(reportViewToMarkdown(view));
      setStatus('Copied.');
    } catch {
      setStatus('Copy is unavailable here. Download Markdown instead.');
    }
  };
  const item = `${DISCLOSURE_BUTTON} block min-h-9 w-full px-3 text-left text-xs hover:bg-gray-100 dark:hover:bg-gray-800`;
  return (
    <div className="relative shrink-0" onKeyDown={(event) => { if (event.key === 'Escape') setOpen(false); }}>
      <button type="button" aria-expanded={open} aria-controls={menuId} onClick={() => setOpen(!open)}
        className={`${DISCLOSURE_BUTTON} flex min-h-9 items-center gap-1 rounded border px-2.5 text-xs font-medium hover:bg-gray-50 dark:border-gray-600 dark:hover:bg-gray-800`}>
        Export <DisclosureChevron open={open} />
      </button>
      {open && (
        <ul id={menuId} className="absolute bottom-full right-0 z-10 mb-1 w-44 rounded border bg-white py-1 shadow-lg dark:border-gray-700 dark:bg-gray-900">
          <li><button type="button" className={item} onClick={() => void run(copy)()}>Copy as text</button></li>
          <li><button type="button" className={item} onClick={() => void run(() => downloadFile(reportViewToMarkdown(view), 'text/markdown', 'md'))()}>Download Markdown</button></li>
          <li><button type="button" className={item} onClick={() => void run(() => downloadFile(JSON.stringify(response, null, 2), 'application/json', 'json'))()}>Download JSON</button></li>
        </ul>
      )}
      <span role="status" className="sr-only">{status}</span>
    </div>
  );
}

function ReportBody({ response }: { response: RegionalIntelligenceResponse }) {
  const view = useMemo(() => buildReportView(response), [response]);
  const headingId = useId();
  const findingsId = useId();
  const recommendationsId = useId();
  const findings = useCollapsedList(view.findings);
  const recommendations = useCollapsedList(view.recommendations);
  const tone = RISK_TONES[view.risk.level] ?? UNKNOWN_RISK_TONE;

  return (
    <article aria-labelledby={headingId} className="min-w-0 space-y-3 text-sm">
      <header className={`border-l-4 pl-3 ${tone.rule}`}>
        <h3 id={headingId} className={`font-editorial-label text-[11px] font-semibold uppercase tracking-[0.14em] ${tone.text}`}>
          Risk · {view.risk.label}
        </h3>
        <p className="mt-0.5 break-words font-editorial-text text-base leading-snug">{view.risk.headline}</p>
      </header>

      {view.sources.isPartial && (
        <p role="note" className="rounded bg-amber-50 px-2 py-1 text-xs text-amber-900 dark:bg-amber-950/40 dark:text-amber-200">
          {PARTIAL_NOTE}
        </p>
      )}

      {view.findings.length > 0 && (
        <section aria-labelledby={`${findingsId}-heading`} className="space-y-1.5">
          <h4 id={`${findingsId}-heading`} className={SECTION_HEADING}>Key findings</h4>
          <ul id={findingsId} className="space-y-2">
            {findings.visible.map((finding, index) => (
              <li key={index} className="break-words">
                <p>{finding.text}</p>
                {finding.meta && <p className={`text-xs ${MUTED}`}>{finding.meta}</p>}
                {finding.groundingNote && <p className={`text-xs ${MUTED}`}>{NOT_GROUNDED_LABEL}</p>}
              </li>
            ))}
          </ul>
          <ShowAllButton hidden={findings.hidden} expanded={findings.expanded}
            onToggle={() => findings.setExpanded(!findings.expanded)} controls={findingsId} />
        </section>
      )}

      <section aria-labelledby={`${recommendationsId}-heading`} className="space-y-1.5">
        <h4 id={`${recommendationsId}-heading`} className={SECTION_HEADING}>Recommendations</h4>
        {view.recommendations.length > 0 ? (
          <>
            <ol id={recommendationsId} className="space-y-2">
              {recommendations.visible.map((item, index) => (
                <li key={index} className="break-words">
                  <p className="font-medium">{recommendationLine(item)}</p>
                  {item.rationale && <p className="text-gray-700 dark:text-gray-300">{item.rationale}</p>}
                  {item.groundingNote && <p className={`text-xs ${MUTED}`}>{NOT_GROUNDED_LABEL}</p>}
                </li>
              ))}
            </ol>
            <ShowAllButton hidden={recommendations.hidden} expanded={recommendations.expanded}
              onToggle={() => recommendations.setExpanded(!recommendations.expanded)} controls={recommendationsId} />
          </>
        ) : (
          <p className={MUTED}>{NO_RECOMMENDATION_TEXT}</p>
        )}
      </section>

      {view.consult && <p className="break-words">{view.consult}</p>}

      <SourcesDisclosure sources={view.sources} />

      {/* The one footer block: AI_GENERATED_DISCLAIMER verbatim, small and muted (gray-600/400 meet WCAG AA). */}
      <footer className="flex items-start justify-between gap-2 border-t pt-2 dark:border-gray-700">
        <p className={`min-w-0 flex-1 text-[11px] leading-snug ${MUTED}`}>{AI_GENERATED_DISCLAIMER}</p>
        <ReportActions response={response} view={view} />
      </footer>
    </article>
  );
}

/** Shared presentation for live and saved analyses; a malformed payload fails inside its own boundary. */
export function RegionalIntelligenceReport({ response }: { response: RegionalIntelligenceResponse }) {
  return (
    <ReportErrorBoundary resetKey={response} fallback={() => (
      <p role="alert" className="rounded border border-red-300 bg-red-50 p-3 text-sm text-red-800 dark:border-red-800 dark:bg-red-950/40 dark:text-red-200">
        This report could not be displayed. Its saved data is incomplete or in an unexpected format.
      </p>
    )}>
      <ReportBody response={response} />
    </ReportErrorBoundary>
  );
}
