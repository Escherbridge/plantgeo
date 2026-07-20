"use client";

import { useEffect, useRef, useState } from 'react';
import { X, MapPin, Send, AlertTriangle, Loader2, ChevronDown } from 'lucide-react';
import { useRegionalIntelligenceStore, type ChatMessage } from '@/stores/regional-intelligence-store';
import { useRegionalIntelligence } from '@/hooks/useRegionalIntelligence';
import {
  isRegionalEvidenceSource,
  regionalEvidenceFreshnessState,
  type RegionalIntelligenceResponse,
} from '@/lib/regional-intelligence';

// ---------------------------------------------------------------------------
// Sub-components for structured response sections
// ---------------------------------------------------------------------------

function RiskSummaryCard({ data }: { data: RegionalIntelligenceResponse['riskSummary'] }) {
  const colors: Record<string, string> = {
    low: 'bg-green-100 text-green-800',
    moderate: 'bg-yellow-100 text-yellow-800',
    high: 'bg-orange-100 text-orange-800',
    critical: 'bg-red-100 text-red-800',
  };
  return (
    <div className={`rounded-lg p-4 ${colors[data.level] ?? 'bg-gray-100 text-gray-800'}`}>
      <div className="flex items-center gap-2 font-semibold">
        <AlertTriangle aria-hidden="true" className="h-4 w-4" />
        Risk: {data.level.toUpperCase()}
      </div>
      <p className="mt-1 text-sm">{data.headline}</p>
      <div className="mt-2 flex flex-wrap gap-1">
        {data.factors.map((f, i) => (
          <span key={i} className="rounded-full bg-white/50 px-2 py-0.5 text-xs">{f}</span>
        ))}
      </div>
    </div>
  );
}

function HistoricalEventsList({ events }: { events: RegionalIntelligenceResponse['historicalEvents'] }) {
  if (!events.length) return null;
  return (
    <div className="space-y-2">
      <h4 className="text-sm font-semibold uppercase text-gray-500">Historical Events</h4>
      {events.map((e, i) => (
        <div key={i} className="flex items-start gap-2 rounded bg-gray-50 p-2 text-sm dark:bg-gray-800">
          <span className="font-mono text-xs text-gray-400">{e.date}</span>
          <div>
            <span className="font-medium">{e.type}</span>
            <span className="ml-1 text-gray-500">— {e.description}</span>
          </div>
        </div>
      ))}
    </div>
  );
}

function ActionableItemsList({ items }: { items: RegionalIntelligenceResponse['actionableItems'] }) {
  const priorityColors: Record<string, string> = {
    immediate: 'border-red-400',
    short_term: 'border-yellow-400',
    long_term: 'border-blue-400',
  };
  const priorityLabels: Record<string, string> = {
    immediate: 'Immediate',
    short_term: 'Short-term',
    long_term: 'Long-term',
  };

  return (
    <div className="space-y-2">
      <h4 className="text-sm font-semibold uppercase text-gray-500">Actionable Items</h4>
      {items.map((item, i) => (
        <div
          key={i}
          className={`border-l-4 ${priorityColors[item.priority] ?? 'border-gray-400'} rounded bg-white p-3 dark:bg-gray-800`}
        >
          <div className="flex items-center gap-2">
            <span className="rounded bg-gray-100 px-1.5 py-0.5 text-xs font-medium dark:bg-gray-700">
              {priorityLabels[item.priority] ?? item.priority}
            </span>
            {item.strategy && (
              <span className="text-xs text-blue-600">{item.strategy}</span>
            )}
          </div>
          <p className="mt-1 text-sm font-medium">{item.action}</p>
          <p className="mt-0.5 text-xs text-gray-500">{item.rationale}</p>
        </div>
      ))}
    </div>
  );
}

function InterventionCard({
  rec,
}: {
  rec: RegionalIntelligenceResponse['interventionRecommendations'][0];
}) {
  const score = Math.min(100, Math.max(0, rec.score));

  return (
    <div className="rounded-lg border p-3 dark:border-gray-700">
      <div className="flex items-center justify-between">
        <span className="text-sm font-medium">{rec.strategy.replace('_', ' ')}</span>
        <span
          className="text-sm font-bold"
          aria-label={`Modeled suitability ${score} out of 100`}
        >
          {score}/100
        </span>
      </div>
      <div className="mt-1 h-1.5 rounded bg-gray-200 dark:bg-gray-700">
        <div className="h-1.5 rounded bg-green-500" style={{ width: `${score}%` }} />
      </div>
      <p className="mt-2 text-xs text-gray-600 dark:text-gray-400">{rec.whyHere}</p>
    </div>
  );
}

function DataFreshnessFooter({ freshness }: { freshness: Record<string, string> }) {
  const [open, setOpen] = useState(false);
  const entries = Object.entries(freshness);
  if (!entries.length) return null;

  return (
    <div className="border-t pt-2 dark:border-gray-700">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        aria-controls="regional-intelligence-freshness"
        className="flex min-h-11 items-center gap-1 rounded text-xs text-gray-500 hover:text-gray-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500"
      >
        <ChevronDown
          aria-hidden="true"
          className={`h-3 w-3 transition-transform ${open ? 'rotate-180' : ''}`}
        />
        Data freshness ({entries.length} sources)
      </button>
      {open && (
        <div id="regional-intelligence-freshness" className="mt-1 space-y-0.5">
          {entries.map(([source, value]) => {
            const freshness = isRegionalEvidenceSource(source)
              ? regionalEvidenceFreshnessState(source, value)
              : 'unavailable';
            const timestamp = Date.parse(value);
            const observedAt = Number.isFinite(timestamp)
              ? new Date(timestamp).toLocaleString()
              : null;
            const state =
              freshness === 'available'
                ? {
                    label: observedAt ?? 'Available',
                    color: 'text-green-600 dark:text-green-400',
                    dot: 'bg-green-400',
                  }
                : freshness === 'pending'
                  ? {
                      label: 'Awaiting validated publication',
                      color: 'text-amber-600 dark:text-amber-400',
                      dot: 'bg-amber-400',
                    }
                  : freshness === 'stale'
                    ? {
                        label: `Stale${observedAt ? ` (${observedAt})` : ''}`,
                        color: 'text-amber-600 dark:text-amber-400',
                        dot: 'bg-amber-400',
                      }
                    : {
                        label: 'Unavailable',
                        color: 'text-red-600 dark:text-red-400',
                        dot: 'bg-red-400',
                      };
            return (
              <div key={source} className="flex items-center gap-2 text-xs">
                <span aria-hidden="true" className={`h-1.5 w-1.5 rounded-full ${state.dot}`} />
                <span className="text-gray-500">{source}</span>
                <span className={state.color}>{state.label}</span>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

function MessageBubble({ message }: { message: ChatMessage }) {
  if (message.role === 'user') {
    return (
      <div className="flex justify-end">
        <div className="max-w-[85%] rounded-lg bg-blue-600 px-3 py-2 text-sm text-white">
          {message.content}
        </div>
      </div>
    );
  }

  // Assistant message with structured response
  if (message.parsedResponse) {
    const r = message.parsedResponse;
    return (
      <div className="space-y-3">
        <RiskSummaryCard data={r.riskSummary} />
        <HistoricalEventsList events={r.historicalEvents} />
        <ActionableItemsList items={r.actionableItems} />
        {r.actionableItems.length === 0 &&
          r.interventionRecommendations.length === 0 && (
            <div className="rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900 dark:border-amber-800 dark:bg-amber-950/30 dark:text-amber-200">
              No evidence-backed actions are available for this location. This
              result is informational and is not an action recommendation.
            </div>
          )}
        {r.interventionRecommendations.length > 0 && (
          <div className="space-y-2">
            <h4 className="text-sm font-semibold uppercase text-gray-500">
              Intervention Recommendations
            </h4>
            {r.interventionRecommendations.map((rec, i) => (
              <InterventionCard key={i} rec={rec} />
            ))}
          </div>
        )}
      </div>
    );
  }

  // Streaming or plain text response
  return (
    <div
      role={message.isStreaming ? 'status' : undefined}
      className="rounded-lg bg-gray-50 px-3 py-2 text-sm dark:bg-gray-800"
    >
      {message.content || 'Loading evidence-backed analysis…'}
      {message.isStreaming && (
        <span className="ml-1 animate-pulse">|</span>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main panel component
// ---------------------------------------------------------------------------

export default function RegionalIntelligencePanel() {
  const isOpen = useRegionalIntelligenceStore((s) => s.isOpen);
  const selectedLocation = useRegionalIntelligenceStore((s) => s.selectedLocation);
  const messages = useRegionalIntelligenceStore((s) => s.messages);
  const isLoading = useRegionalIntelligenceStore((s) => s.isLoading);
  const error = useRegionalIntelligenceStore((s) => s.error);
  const errorRetryable = useRegionalIntelligenceStore((s) => s.errorRetryable);
  const analysisCancelled = useRegionalIntelligenceStore((s) => s.analysisCancelled);
  const dataFreshness = useRegionalIntelligenceStore((s) => s.dataFreshness);
  const closePanel = useRegionalIntelligenceStore((s) => s.closePanel);
  const cancelAnalysis = useRegionalIntelligenceStore((s) => s.cancelAnalysis);
  const setError = useRegionalIntelligenceStore((s) => s.setError);
  const { sendFollowUp, retryLastRequest } = useRegionalIntelligence();

  const [input, setInput] = useState('');
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const panelRef = useRef<HTMLElement>(null);
  const previousFocusRef = useRef<HTMLElement | null>(null);

  // Auto-scroll on new messages
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  useEffect(() => {
    if (!isOpen) return;
    previousFocusRef.current =
      document.activeElement instanceof HTMLElement
        ? document.activeElement
        : null;
    const focusFrame = window.requestAnimationFrame(() => {
      (inputRef.current ?? panelRef.current)?.focus();
    });
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') closePanel();
    };
    document.addEventListener('keydown', handleKeyDown);
    return () => {
      window.cancelAnimationFrame(focusFrame);
      document.removeEventListener('keydown', handleKeyDown);
      previousFocusRef.current?.focus();
      previousFocusRef.current = null;
    };
  }, [closePanel, isOpen]);

  if (!isOpen || !selectedLocation) return null;

  const coordinatePrecision = selectedLocation.precision === 'exact' ? 6 : 2;

  const handleSend = async () => {
    const q = input.trim();
    if (!q || isLoading) return;
    setInput('');
    await sendFollowUp(q);
  };

  return (
    <aside
      ref={panelRef}
      role="dialog"
      tabIndex={-1}
      aria-labelledby="regional-intelligence-title"
      aria-busy={isLoading}
      className="absolute right-0 top-0 z-50 flex h-full w-full flex-col border-l bg-white shadow-xl sm:w-96 dark:border-gray-700 dark:bg-gray-900"
    >
      <div role="status" aria-live="polite" aria-atomic="true" className="sr-only">
        {isLoading
          ? 'Regional analysis in progress.'
          : messages.some((message) => message.parsedResponse)
            ? 'Regional analysis complete.'
            : ''}
      </div>
      {/* Header */}
      <div className="flex items-center justify-between border-b p-3 dark:border-gray-700">
        <div className="flex items-center gap-2">
          <MapPin aria-hidden="true" className="h-4 w-4 text-blue-500" />
          <h2 id="regional-intelligence-title" className="text-sm font-medium">
            {selectedLocation.lat.toFixed(coordinatePrecision)}°,{' '}
            {selectedLocation.lon.toFixed(coordinatePrecision)}°
          </h2>
        </div>
        <button
          type="button"
          onClick={closePanel}
          aria-label="Close regional intelligence analysis"
          className="flex min-h-11 min-w-11 items-center justify-center rounded hover:bg-gray-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 dark:hover:bg-gray-800"
        >
          <X aria-hidden="true" className="h-4 w-4" />
        </button>
      </div>

      {/* Messages */}
      <div className="flex-1 space-y-3 overflow-y-auto p-3">
        {messages.length === 0 && !isLoading && (
          <div className="flex h-full items-center justify-center p-4 text-center text-sm text-gray-500">
            No validated regional evidence has produced an analysis. No action
            is recommended from this state.
          </div>
        )}
        {messages.map((msg) => (
          <MessageBubble key={msg.id} message={msg} />
        ))}
        {analysisCancelled && !isLoading && (
          <div role="status" className="rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900 dark:border-amber-800 dark:bg-amber-950/30 dark:text-amber-200">
            <p>Analysis was canceled. No analysis was completed.</p>
            <button
              type="button"
              onClick={() => void retryLastRequest()}
              className="mt-1 flex min-h-11 items-center rounded px-2 text-xs underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-500"
            >
              Resume analysis
            </button>
          </div>
        )}
        {error && (
          <div role="alert" className="rounded-lg bg-red-50 p-3 text-sm text-red-600 dark:bg-red-900/20">
            <p>{error}</p>
            <div className="mt-1 flex gap-2">
              {errorRetryable && (
                <button
                  type="button"
                  onClick={() => void retryLastRequest()}
                  disabled={isLoading}
                  className="flex min-h-11 items-center rounded px-2 text-xs underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-500 disabled:opacity-50"
                >
                  Retry
                </button>
              )}
              <button
                type="button"
                onClick={() => setError(null)}
                className="flex min-h-11 items-center rounded px-2 text-xs underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-500"
              >
                Dismiss
              </button>
            </div>
          </div>
        )}
        <div ref={messagesEndRef} />
      </div>

      {/* Data freshness */}
      <div className="px-3 pb-1">
        <DataFreshnessFooter freshness={dataFreshness} />
      </div>

      {/* Input */}
      <div className="border-t p-3 dark:border-gray-700">
        <div className="mb-2 flex items-center justify-between gap-3">
          <p className="text-xs text-gray-500 dark:text-gray-400">
            AI-assisted, privacy-reduced guidance. Verify any information before acting.
          </p>
          {isLoading && (
            <button
              type="button"
              onClick={cancelAnalysis}
              className="flex min-h-11 min-w-11 shrink-0 items-center justify-center rounded px-3 text-xs font-medium text-blue-600 underline hover:text-blue-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 dark:text-blue-400"
            >
              Cancel
            </button>
          )}
        </div>
        <div className="flex gap-2">
          <label htmlFor="regional-intelligence-question" className="sr-only">
            Ask a follow-up question about this location
          </label>
          <input
            id="regional-intelligence-question"
            ref={inputRef}
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && void handleSend()}
            placeholder="Ask a follow-up question..."
            maxLength={1000}
            disabled={isLoading}
            className="min-h-11 flex-1 rounded-lg border bg-gray-50 px-3 py-2 text-sm outline-none focus-visible:border-blue-500 focus-visible:ring-2 focus-visible:ring-blue-500 disabled:opacity-50 dark:border-gray-600 dark:bg-gray-800"
          />
          <button
            type="button"
            aria-label="Send follow-up question"
            onClick={() => void handleSend()}
            disabled={isLoading || !input.trim()}
            className="flex min-h-11 min-w-11 items-center justify-center rounded-lg bg-blue-600 px-3 py-2 text-white hover:bg-blue-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2 disabled:opacity-50"
          >
            {isLoading ? (
              <Loader2 aria-hidden="true" className="h-4 w-4 animate-spin" />
            ) : (
              <Send aria-hidden="true" className="h-4 w-4" />
            )}
          </button>
        </div>
      </div>
    </aside>
  );
}
