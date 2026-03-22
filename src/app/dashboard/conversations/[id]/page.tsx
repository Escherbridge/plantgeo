import { getServerSession } from '@/lib/server/auth';
import { db } from '@/lib/server/db';
import { aiConversations, aiMessages } from '@/lib/server/db/schema';
import { eq, and, asc } from 'drizzle-orm';
import { redirect, notFound } from 'next/navigation';
import Link from 'next/link';
import { ArrowLeft, MapPin, ExternalLink } from 'lucide-react';

export default async function ConversationDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const session = await getServerSession();
  const userId = (session?.user as { id?: string } | undefined)?.id;
  if (!userId) redirect('/');

  const [conversation] = await db
    .select()
    .from(aiConversations)
    .where(
      and(
        eq(aiConversations.id, id),
        eq(aiConversations.userId, userId)
      )
    )
    .limit(1);

  if (!conversation) notFound();

  const messages = await db
    .select()
    .from(aiMessages)
    .where(eq(aiMessages.conversationId, id))
    .orderBy(asc(aiMessages.createdAt));

  return (
    <div className="mx-auto max-w-4xl p-6">
      <div className="mb-6 flex items-center gap-3">
        <Link
          href="/dashboard/conversations"
          className="rounded p-1 hover:bg-gray-100 dark:hover:bg-gray-800"
        >
          <ArrowLeft className="h-5 w-5" />
        </Link>
        <div>
          <h1 className="text-xl font-bold">{conversation.title}</h1>
          <div className="flex items-center gap-2 text-sm text-gray-500">
            <MapPin className="h-3 w-3" />
            {conversation.lat.toFixed(4)}°, {conversation.lon.toFixed(4)}°
            <Link
              href={`/?lat=${conversation.lat}&lon=${conversation.lon}&ai=open`}
              className="ml-2 flex items-center gap-1 text-blue-500 hover:underline"
            >
              <ExternalLink className="h-3 w-3" />
              Open on Map
            </Link>
          </div>
        </div>
      </div>

      <div className="space-y-4">
        {messages.map((msg) => (
          <div
            key={msg.id}
            className={msg.role === 'user' ? 'flex justify-end' : ''}
          >
            <div
              className={`max-w-[85%] rounded-lg p-3 text-sm ${
                msg.role === 'user'
                  ? 'bg-blue-600 text-white'
                  : 'bg-gray-50 dark:bg-gray-800'
              }`}
            >
              {msg.structuredResponse ? (
                <pre className="whitespace-pre-wrap text-xs">
                  {JSON.stringify(msg.structuredResponse, null, 2)}
                </pre>
              ) : (
                <p className="whitespace-pre-wrap">{msg.content}</p>
              )}
              <div className="mt-1 text-xs opacity-60">
                {new Date(msg.createdAt).toLocaleString()}
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
