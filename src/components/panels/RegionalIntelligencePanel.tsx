"use client";

import { useEffect, useMemo, useRef, useState } from 'react';
import Link from 'next/link';
import { MessageFeedback } from './MessageFeedback';
import { X, MapPin, Send, Loader2 } from 'lucide-react';
import { useRegionalIntelligenceStore, type ChatMessage } from '@/stores/regional-intelligence-store';
import { useRegionalIntelligence } from '@/hooks/useRegionalIntelligence';
import { AI_GENERATED_LABEL } from '@/lib/regional-intelligence';
import { buildSourcesView } from '@/lib/regional-evidence-presentation';
import { RegionalIntelligenceReport, SourcesDisclosure } from './RegionalIntelligenceReport';
import { ReportErrorBoundary } from './ReportErrorBoundary';

// Re-exported so saved-conversation pages and transcripts keep one import site.
export { RegionalIntelligenceReport } from './RegionalIntelligenceReport';
export { reportToMarkdown } from '@/lib/regional-evidence-presentation';

function MessageBubble({ message, conversationId }: { message: ChatMessage; conversationId: string | null }) {
  if (message.role === 'user') {
    return (
      <div className="flex justify-end">
        <div className="max-w-[85%] break-words rounded-lg bg-blue-600 px-3 py-2 text-sm text-white">
          {message.content}
        </div>
      </div>
    );
  }

  if (message.parsedResponse) return <div>
    <RegionalIntelligenceReport response={message.parsedResponse} />
    {conversationId && message.savedMessageId && <MessageFeedback conversationId={conversationId} messageId={message.savedMessageId} />}
  </div>;

  return (
    <div
      role={message.isStreaming ? 'status' : undefined}
      className="whitespace-pre-wrap break-words rounded-lg bg-gray-50 px-3 py-2 text-sm dark:bg-gray-800"
    >
      {message.content || (message.isStreaming ? 'Reviewing this location…' : 'No analysis was completed.')}
      {message.isStreaming && <span aria-hidden="true" className="ml-1 animate-pulse">|</span>}
      {!message.isStreaming && conversationId && message.savedMessageId && <MessageFeedback conversationId={conversationId} messageId={message.savedMessageId} />}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main panel component
// ---------------------------------------------------------------------------

export interface RegionalIntelligencePanelProps {
  /**
   * Rendered inside `AiInterventionWorkspace`'s AI slot rather than as its own right-edge
   * overlay. Embedded, the shell owns the frame: the panel drops its absolute positioning, its
   * own header (the shell states the coordinate and holds the close button) and its Escape
   * binding, and it ignores `isVisible` -- that flag exists so the STANDALONE copy stands down
   * while the workspace holds the conversation, and honouring it here would blank the pane the
   * workspace is showing.
   */
  embedded?: boolean;
}

/** Stands in for the whole panel when it throws, so the map and its close control survive. */
function PanelFailure({ embedded, onRetry }: { embedded: boolean; onRetry: () => void }) {
  const closePanel = useRegionalIntelligenceStore((state) => state.closePanel);
  return (
    <aside
      role="alert"
      data-testid="regional-intelligence-panel"
      className={
        embedded
          ? 'flex h-full w-full flex-col gap-2 bg-white p-3 text-sm dark:bg-gray-900'
          : 'absolute right-0 top-0 z-50 flex h-full w-full flex-col gap-2 border-l bg-white p-3 text-sm shadow-xl sm:w-96 dark:border-gray-700 dark:bg-gray-900'
      }
    >
      <p>The analysis panel could not be displayed.</p>
      <div className="flex gap-3 text-xs">
        <button type="button" onClick={onRetry} className="min-h-11 underline">Try again</button>
        <button type="button" onClick={closePanel} className="min-h-11 underline">Close</button>
      </div>
    </aside>
  );
}

export default function RegionalIntelligencePanel({
  embedded = false,
}: RegionalIntelligencePanelProps = {}) {
  return (
    <ReportErrorBoundary fallback={(reset) => <PanelFailure embedded={embedded} onRetry={reset} />}>
      <RegionalIntelligencePanelBody embedded={embedded} />
    </ReportErrorBoundary>
  );
}

function RegionalIntelligencePanelBody({ embedded }: { embedded: boolean }) {
  const isOpen = useRegionalIntelligenceStore((state) => state.isOpen);
  const isVisible = useRegionalIntelligenceStore((state) => state.isVisible);
  const selectedLocation = useRegionalIntelligenceStore((state) => state.selectedLocation);
  const messages = useRegionalIntelligenceStore((state) => state.messages);
  const isLoading = useRegionalIntelligenceStore((state) => state.isLoading);
  const activity = useRegionalIntelligenceStore((state) => state.activity);
  const conversationId = useRegionalIntelligenceStore((state) => state.conversationId);
  const error = useRegionalIntelligenceStore((state) => state.error);
  const errorRetryable = useRegionalIntelligenceStore((state) => state.errorRetryable);
  const analysisCancelled = useRegionalIntelligenceStore((state) => state.analysisCancelled);
  const dataFreshness = useRegionalIntelligenceStore((state) => state.dataFreshness);
  const analysisEvidence = useRegionalIntelligenceStore((state) => state.analysisEvidence);
  const toolActivity = useRegionalIntelligenceStore((state) => state.toolActivity);
  const closePanel = useRegionalIntelligenceStore((state) => state.closePanel);
  const cancelAnalysis = useRegionalIntelligenceStore((state) => state.cancelAnalysis);
  const setError = useRegionalIntelligenceStore((state) => state.setError);
  const { sendFollowUp, retryLastRequest } = useRegionalIntelligence();

  const [input, setInput] = useState('');
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const panelRef = useRef<HTMLElement>(null);
  const previousFocusRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    const messagesEnd = messagesEndRef.current;
    if (messagesEnd && typeof messagesEnd.scrollIntoView === 'function') {
      messagesEnd.scrollIntoView({ behavior: 'smooth' });
    }
  }, [messages]);

  useEffect(() => {
    if (!isOpen || !isVisible || embedded) return;
    previousFocusRef.current =
      document.activeElement instanceof HTMLElement ? document.activeElement : null;
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
  }, [closePanel, embedded, isOpen, isVisible]);

  // While a turn streams, the same Sources disclosure the finished report uses.
  const liveSources = useMemo(
    () => (isLoading && analysisEvidence ? buildSourcesView({ evidence: analysisEvidence, freshness: dataFreshness }) : null),
    [isLoading, analysisEvidence, dataFreshness],
  );

  if (!isOpen || !selectedLocation) return null;
  // The standalone overlay stands down while the workspace embeds this same conversation.
  if (!embedded && !isVisible) return null;

  const coordinatePrecision = selectedLocation.precision === 'exact' ? 6 : 2;

  const handleSend = async () => {
    const question = input.trim();
    if (!question || isLoading) return;
    setInput('');
    await sendFollowUp(question);
  };

  return (
    <aside
      ref={panelRef}
      role={embedded ? undefined : 'dialog'}
      tabIndex={-1}
      aria-labelledby={embedded ? undefined : 'regional-intelligence-title'}
      aria-label={embedded ? 'Regional intelligence analysis' : undefined}
      aria-busy={isLoading}
      data-testid="regional-intelligence-panel"
      className={
        embedded
          ? 'flex h-full w-full flex-col bg-white dark:bg-gray-900'
          : 'absolute right-0 top-0 z-50 flex h-full w-full flex-col border-l bg-white shadow-xl sm:w-96 dark:border-gray-700 dark:bg-gray-900'
      }
    >
      <div role="status" aria-live="polite" aria-atomic="true" className="sr-only">
        {isLoading
          ? toolActivity ?? 'Regional analysis in progress.'
          : messages.some((message) => message.parsedResponse)
            ? 'Regional analysis complete.'
            : ''}
      </div>

      {/* Header. Embedded, the workspace shell already states the coordinate and owns the one
          close control, so a second header here would be a second X with different semantics. */}
      {!embedded && (
      <div className="flex items-center justify-between border-b p-3 dark:border-gray-700">
        <div className="flex min-w-0 items-center gap-2">
          <MapPin aria-hidden="true" className="h-4 w-4 shrink-0 text-blue-500" />
          <div className="min-w-0">
            <h2 id="regional-intelligence-title" className="truncate text-sm font-medium">
              {selectedLocation.lat.toFixed(coordinatePrecision)}°,{' '}
              {selectedLocation.lon.toFixed(coordinatePrecision)}°
            </h2>
            <p className="text-[11px] text-gray-500">{AI_GENERATED_LABEL} advisor</p>
          </div>
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
      )}

      {/* Messages */}
      <div className="flex flex-wrap gap-3 border-b px-3 py-2 text-xs">
        <button type="button" disabled={isLoading} className="text-blue-600 hover:underline disabled:opacity-50" onClick={() => {
          useRegionalIntelligenceStore.getState().openPanel(selectedLocation.lat, selectedLocation.lon, selectedLocation.precision);
          setInput('');
        }}>New chat</button>
        <Link href="/dashboard/conversations" className="text-blue-600 hover:underline">Chat history</Link>
        {conversationId && <Link href={`/dashboard/conversations/${conversationId}`} className="text-blue-600 hover:underline">Saved conversation</Link>}
        <span className="text-gray-500">Private to your account</span>
      </div>
      <div className="flex-1 space-y-3 overflow-y-auto p-3">
        {activity.length > 0 && <details className="rounded border p-2 text-xs">
          <summary className="cursor-pointer font-medium">Activity in this session ({activity.length})</summary>
          <ol className="mt-2 space-y-1">{activity.map(event => <li key={event.id}><time dateTime={event.at}>{new Date(event.at).toLocaleTimeString()}</time> — {event.label}</li>)}</ol>
        </details>}
        {messages.length === 0 && !isLoading && (
          <div className="flex h-full items-center justify-center p-4 text-center text-sm text-gray-500">
            Ask about this location to get AI-generated remediation suggestions.
          </div>
        )}
        {messages.map((message) => (
          <MessageBubble key={message.id} message={message} conversationId={conversationId} />
        ))}
        {toolActivity && isLoading && (
          <p className="flex items-center gap-2 text-xs text-gray-500">
            <Loader2 aria-hidden="true" className="h-3 w-3 animate-spin" />
            {toolActivity}
          </p>
        )}
        {liveSources && <SourcesDisclosure sources={liveSources} heading="Sources so far" />}
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

      {/* Input */}
      <div className="border-t p-3 dark:border-gray-700">
        {isLoading && (
          <div className="mb-2 flex justify-end">
            <button
              type="button"
              onClick={cancelAnalysis}
              className="flex min-h-11 min-w-11 shrink-0 items-center justify-center rounded px-3 text-xs font-medium text-blue-700 underline hover:text-blue-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 dark:text-blue-400"
            >
              Cancel
            </button>
          </div>
        )}
        <div className="flex gap-2">
          <label htmlFor="regional-intelligence-question" className="sr-only">
            Ask a follow-up question about this location
          </label>
          <input
            id="regional-intelligence-question"
            ref={inputRef}
            type="text"
            value={input}
            onChange={(event) => setInput(event.target.value)}
            onKeyDown={(event) => event.key === 'Enter' && void handleSend()}
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
