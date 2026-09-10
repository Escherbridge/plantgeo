'use client';

import { useRouter } from 'next/navigation';
import { useRegionalIntelligenceStore, type ChatMessage } from '@/stores/regional-intelligence-store';
import { CopyShareText } from '@/components/panels/CopyShareText';
import { reportToMarkdown } from '@/components/panels/RegionalIntelligencePanel';

export function ResumeConversation({ id, lat, lon, messages, mapHref }: {
  id: string; lat: number; lon: number; messages: ChatMessage[]; mapHref: string;
}) {
  const router = useRouter();
  const transcript = [`Saved PlantGeo conversation at ${lat}, ${lon}`, 'Historical reports are shared as recorded.', ...messages.map(message => `${message.role === 'assistant' ? 'Assistant' : 'You'}${message.createdAt ? ` — ${message.createdAt}` : ''}\n${message.parsedResponse ? reportToMarkdown(message.parsedResponse) : message.content}`)].join('\n\n');
  return <div className="my-4 flex flex-wrap items-start gap-3"><button type="button" className="rounded bg-blue-600 px-3 py-2 text-sm font-medium text-white hover:bg-blue-500" onClick={() => {
    useRegionalIntelligenceStore.getState().resumeConversation({ id, lat, lon, messages });
    router.push(mapHref);
  }}>Resume on map</button><CopyShareText text={transcript} title="Saved PlantGeo conversation" /></div>;
}
