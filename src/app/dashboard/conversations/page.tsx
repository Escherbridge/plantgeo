import { getServerSession } from '@/lib/server/auth';
import { db } from '@/lib/server/db';
import { aiConversations } from '@/lib/server/db/schema';
import { eq, desc } from 'drizzle-orm';
import { redirect } from 'next/navigation';
import Link from 'next/link';
import { MapPin, MessageSquare, Clock } from 'lucide-react';

export default async function ConversationsPage() {
  const session = await getServerSession();
  const userId = (session?.user as { id?: string } | undefined)?.id;
  if (!userId) redirect('/');

  const conversations = await db
    .select()
    .from(aiConversations)
    .where(eq(aiConversations.userId, userId))
    .orderBy(desc(aiConversations.updatedAt))
    .limit(50);

  return (
    <div className="mx-auto max-w-4xl p-6">
      <h1 className="mb-6 text-2xl font-bold">AI Conversations</h1>

      {conversations.length === 0 ? (
        <div className="py-12 text-center text-gray-500">
          <MessageSquare className="mx-auto mb-4 h-12 w-12 opacity-50" />
          <p>No conversations yet. Click any point on the map to start.</p>
        </div>
      ) : (
        <div className="space-y-3">
          {conversations.map((conv) => (
            <Link
              key={conv.id}
              href={`/dashboard/conversations/${conv.id}`}
              className="flex items-start gap-4 rounded-lg border p-4 transition hover:bg-gray-50 dark:border-gray-700 dark:hover:bg-gray-800"
            >
              <MapPin className="mt-0.5 h-5 w-5 shrink-0 text-blue-500" />
              <div className="min-w-0 flex-1">
                <h3 className="truncate font-medium">{conv.title}</h3>
                <p className="mt-0.5 text-sm text-gray-500">
                  {conv.lat.toFixed(4)}°, {conv.lon.toFixed(4)}°
                </p>
                <div className="mt-1 flex items-center gap-3 text-xs text-gray-400">
                  <span className="flex items-center gap-1">
                    <MessageSquare className="h-3 w-3" />
                    {conv.messageCount} messages
                  </span>
                  <span className="flex items-center gap-1">
                    <Clock className="h-3 w-3" />
                    {new Date(conv.updatedAt).toLocaleDateString()}
                  </span>
                </div>
              </div>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
