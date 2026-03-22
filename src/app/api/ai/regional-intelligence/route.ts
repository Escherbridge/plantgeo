export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

import { NextRequest } from 'next/server';
import { getServerSession } from '@/lib/server/auth';
import { getRedis } from '@/lib/server/redis';
import { assembleRegionalContext } from '@/lib/server/services/regional-context';
import {
  streamRegionalIntelligence,
  type ConversationTurn,
} from '@/lib/server/services/ai-prompt';
import { z } from 'zod';

const requestSchema = z.object({
  lat: z.number().min(-90).max(90),
  lon: z.number().min(-180).max(180),
  question: z.string().max(1000).optional(),
  history: z
    .array(
      z.object({
        role: z.enum(['user', 'assistant']),
        content: z.string(),
      })
    )
    .max(10)
    .optional(),
});

async function checkRateLimit(
  userId: string
): Promise<{ allowed: boolean; retryAfter?: number }> {
  const redis = getRedis();
  const key = `ai-ratelimit:${userId}`;
  const now = Date.now();
  const windowMs = 3_600_000; // 1 hour
  const limit = 20;

  try {
    // Remove old entries outside the window
    await redis.zremrangebyscore(key, 0, now - windowMs);
    const count = await redis.zcard(key);

    if (count >= limit) {
      const oldest = await redis.zrange(key, 0, 0, 'WITHSCORES');
      const retryAfter =
        oldest.length >= 2
          ? Math.ceil((Number(oldest[1]) + windowMs - now) / 1000)
          : 3600;
      return { allowed: false, retryAfter };
    }

    await redis.zadd(key, now, `${now}`);
    await redis.expire(key, 3600);
    return { allowed: true };
  } catch {
    // fail open if Redis is unavailable
    return { allowed: true };
  }
}

export async function POST(request: NextRequest) {
  // Auth check
  const session = await getServerSession();
  if (!session?.user) {
    return new Response(JSON.stringify({ error: 'Authentication required' }), {
      status: 401,
      headers: { 'Content-Type': 'application/json' },
    });
  }

  const userId = (session.user as { id?: string }).id;
  if (!userId) {
    return new Response(JSON.stringify({ error: 'Authentication required' }), {
      status: 401,
      headers: { 'Content-Type': 'application/json' },
    });
  }

  // Parse body
  let body: z.infer<typeof requestSchema>;
  try {
    body = requestSchema.parse(await request.json());
  } catch {
    return new Response(JSON.stringify({ error: 'Invalid request' }), {
      status: 400,
      headers: { 'Content-Type': 'application/json' },
    });
  }

  // Rate limit
  const { allowed, retryAfter } = await checkRateLimit(userId);
  if (!allowed) {
    return new Response(JSON.stringify({ error: 'Rate limit exceeded' }), {
      status: 429,
      headers: {
        'Content-Type': 'application/json',
        'Retry-After': String(retryAfter),
      },
    });
  }

  // Assemble regional context
  let contextResult: Awaited<ReturnType<typeof assembleRegionalContext>>;
  try {
    contextResult = await assembleRegionalContext(body.lat, body.lon);
  } catch {
    return new Response(
      JSON.stringify({ error: 'All data sources unavailable' }),
      {
        status: 503,
        headers: { 'Content-Type': 'application/json' },
      }
    );
  }

  const { payload, dataFreshness, cacheHit } = contextResult;

  // Log for cost monitoring
  console.log(
    `[AI] userId=${userId} lat=${body.lat} lon=${body.lon} cacheHit=${cacheHit}`
  );

  const encoder = new TextEncoder();
  const abortController = new AbortController();

  // Wire client disconnect to abort
  request.signal.addEventListener('abort', () => abortController.abort());

  const history: ConversationTurn[] = body.history ?? [];

  const stream = new ReadableStream({
    async start(controller) {
      try {
        // Send context event immediately
        controller.enqueue(
          encoder.encode(
            `event: context\ndata: ${JSON.stringify({ cacheHit, dataFreshness })}\n\n`
          )
        );

        // Stream AI response
        const generator = streamRegionalIntelligence(
          payload,
          dataFreshness,
          history,
          body.question,
          abortController.signal
        );

        let fullText = '';
        let toolResult = '';

        for await (const chunk of generator) {
          if (abortController.signal.aborted) break;

          if (chunk.data.startsWith('__COMPLETE__')) {
            toolResult = chunk.data.slice('__COMPLETE__'.length);
          } else {
            fullText += chunk.data;
            controller.enqueue(
              encoder.encode(
                `event: delta\ndata: ${JSON.stringify({ text: chunk.data, type: chunk.type })}\n\n`
              )
            );
          }
        }

        // Send done event with parsed structured response
        if (toolResult) {
          try {
            const parsed = JSON.parse(toolResult) as Record<string, unknown>;
            parsed.dataFreshness = dataFreshness;
            controller.enqueue(
              encoder.encode(
                `event: done\ndata: ${JSON.stringify(parsed)}\n\n`
              )
            );
          } catch {
            controller.enqueue(
              encoder.encode(
                `event: error\ndata: ${JSON.stringify({ message: 'Response parse failed' })}\n\n`
              )
            );
          }
        } else if (fullText) {
          controller.enqueue(
            encoder.encode(
              `event: done\ndata: ${JSON.stringify({ text: fullText })}\n\n`
            )
          );
        }
      } catch (err) {
        if (!abortController.signal.aborted) {
          const message =
            err instanceof Error ? err.message : 'Stream error';
          controller.enqueue(
            encoder.encode(
              `event: error\ndata: ${JSON.stringify({ message })}\n\n`
            )
          );
        }
      } finally {
        controller.close();
      }
    },
  });

  return new Response(stream, {
    headers: {
      'Content-Type': 'text/event-stream',
      'Cache-Control': 'no-cache, no-transform',
      Connection: 'keep-alive',
      'X-Accel-Buffering': 'no',
    },
  });
}
