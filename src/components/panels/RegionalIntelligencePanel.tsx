"use client";

import { useEffect, useRef, useState } from 'react';
import { X, MapPin, Send, AlertTriangle, Loader2, ChevronDown } from 'lucide-react';
import { useRegionalIntelligenceStore, type ChatMessage } from '@/stores/regional-intelligence-store';
import { useRegionalIntelligence } from '@/hooks/useRegionalIntelligence';
import type { RegionalIntelligenceResponse } from '@/lib/server/services/ai-prompt';

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
        <AlertTriangle className="h-4 w-4" />
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
  return (
    <div className="rounded-lg border p-3 dark:border-gray-700">
      <div className="flex items-center justify-between">
        <span className="text-sm font-medium">{rec.strategy.replace('_', ' ')}</span>
        <span className="text-sm font-bold">{rec.score}/100</span>
      </div>
      <div className="mt-1 h-1.5 rounded bg-gray-200 dark:bg-gray-700">
        <div className="h-1.5 rounded bg-green-500" style={{ width: `${rec.score}%` }} />
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
        onClick={() => setOpen(!open)}
        className="flex items-center gap-1 text-xs text-gray-400 hover:text-gray-600"
      >
        <ChevronDown
          className={`h-3 w-3 transition-transform ${open ? 'rotate-180' : ''}`}
        />
        Data freshness ({entries.length} sources)
      </button>
      {open && (
        <div className="mt-1 space-y-0.5">
          {entries.map(([source, ts]) => (
            <div key={source} className="flex items-center gap-2 text-xs">
              <span
                className={`h-1.5 w-1.5 rounded-full ${
                  ts === 'unavailable' ? 'bg-red-400' : 'bg-green-400'
                }`}
              />
              <span className="text-gray-500">{source}</span>
              <span
                className={ts === 'unavailable' ? 'text-red-400' : 'text-green-400'}
              >
                {ts === 'unavailable'
                  ? 'unavailable'
                  : new Date(ts).toLocaleTimeString()}
              </span>
            </div>
          ))}
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
        <div className="space-y-2">
          <h4 className="text-sm font-semibold uppercase text-gray-500">
            Intervention Recommendations
          </h4>
          {r.interventionRecommendations.map((rec, i) => (
            <InterventionCard key={i} rec={rec} />
          ))}
        </div>
      </div>
    );
  }

  // Streaming or plain text response
  return (
    <div className="rounded-lg bg-gray-50 px-3 py-2 text-sm dark:bg-gray-800">
      {message.content || '...'}
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
  const dataFreshness = useRegionalIntelligenceStore((s) => s.dataFreshness);
  const closePanel = useRegionalIntelligenceStore((s) => s.closePanel);
  const setError = useRegionalIntelligenceStore((s) => s.setError);

  const [input, setInput] = useState('');
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  // Auto-scroll on new messages
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  // Focus input when panel opens
  useEffect(() => {
    if (isOpen) inputRef.current?.focus();
  }, [isOpen]);

  if (!isOpen || !selectedLocation) return null;

  const { sendFollowUp } = useRegionalIntelligence();

  const handleSend = async () => {
    const q = input.trim();
    if (!q || isLoading) return;
    setInput('');
    await sendFollowUp(q);
  };

  return (
    <div className="absolute right-0 top-0 z-50 flex h-full w-96 flex-col border-l bg-white shadow-xl dark:border-gray-700 dark:bg-gray-900">
      {/* Header */}
      <div className="flex items-center justify-between border-b p-3 dark:border-gray-700">
        <div className="flex items-center gap-2">
          <MapPin className="h-4 w-4 text-blue-500" />
          <span className="text-sm font-medium">
            {selectedLocation.lat.toFixed(4)}°,{' '}
            {selectedLocation.lon.toFixed(4)}°
          </span>
        </div>
        <button
          onClick={closePanel}
          className="rounded p-1 hover:bg-gray-100 dark:hover:bg-gray-800"
        >
          <X className="h-4 w-4" />
        </button>
      </div>

      {/* Messages */}
      <div className="flex-1 space-y-3 overflow-y-auto p-3">
        {messages.length === 0 && !isLoading && (
          <div className="flex h-full items-center justify-center text-sm text-gray-400">
            Analyzing location...
          </div>
        )}
        {messages.map((msg) => (
          <MessageBubble key={msg.id} message={msg} />
        ))}
        {error && (
          <div className="rounded-lg bg-red-50 p-3 text-sm text-red-600 dark:bg-red-900/20">
            <p>{error}</p>
            <button
              onClick={() => setError(null)}
              className="mt-1 text-xs underline"
            >
              Dismiss
            </button>
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
        <div className="flex gap-2">
          <input
            ref={inputRef}
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && void handleSend()}
            placeholder="Ask a follow-up question..."
            disabled={isLoading}
            className="flex-1 rounded-lg border bg-gray-50 px-3 py-2 text-sm outline-none focus:border-blue-400 disabled:opacity-50 dark:border-gray-600 dark:bg-gray-800"
          />
          <button
            onClick={() => void handleSend()}
            disabled={isLoading || !input.trim()}
            className="rounded-lg bg-blue-600 px-3 py-2 text-white hover:bg-blue-700 disabled:opacity-50"
          >
            {isLoading ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Send className="h-4 w-4" />
            )}
          </button>
        </div>
      </div>
    </div>
  );
}
