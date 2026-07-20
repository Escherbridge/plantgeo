export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

import { NextRequest } from 'next/server';
import { getServerSession } from '@/lib/server/auth';
import { getRedis } from '@/lib/server/redis';
import { parseBoundedJson } from '@/lib/server/security/ingress';
import { assembleRegionalContext } from '@/lib/server/services/regional-context';
import {
  streamRegionalIntelligence,
  type ConversationTurn,
} from '@/lib/server/services/ai-prompt';
import { z } from 'zod';
import { randomUUID } from 'crypto';
import {
  INTERVENTION_STRATEGIES,
  REGIONAL_EVIDENCE_SOURCES,
  regionalEvidenceFreshnessState,
  type RegionalEvidenceSource,
} from '@/lib/regional-intelligence';
import {
  REGIONAL_INTELLIGENCE_INACTIVE_MESSAGE,
  REGIONAL_INTELLIGENCE_SERVING_STATE,
  reserveRegionalIntelligenceUsage,
} from '@/lib/server/security/regional-intelligence-access';

const requestSchema = z.object({
  lat: z.number().min(-90).max(90),
  lon: z.number().min(-180).max(180),
  question: z.string().max(1000).optional(),
  locationConsent: z
    .object({
      precision: z.enum(['approximate', 'exact']),
      confirmed: z.literal(true),
    })
    .strict(),
  history: z
    .array(
      z.object({
        role: z.enum(['user', 'assistant']),
        content: z.string().max(1000),
      })
    )
    .max(6)
    .optional(),
});

const MAX_REGIONAL_INTELLIGENCE_BODY_BYTES = 16 * 1024;
let activeRegionalIntelligenceRequests = 0;

function maximumConcurrentRequests(): number {
  const configured = Number(
    process.env.REGIONAL_INTELLIGENCE_MAX_CONCURRENT_PER_REPLICA
  );
  return Number.isInteger(configured) && configured > 0
    ? Math.min(configured, 16)
    : 2;
}

/** Reserves bounded per-replica capacity before regional context assembly. */
export function acquireRegionalIntelligenceCapacity(): boolean {
  if (activeRegionalIntelligenceRequests >= maximumConcurrentRequests()) {
    return false;
  }
  activeRegionalIntelligenceRequests += 1;
  return true;
}

/** Releases one regional-intelligence capacity reservation. */
export function releaseRegionalIntelligenceCapacity(): void {
  activeRegionalIntelligenceRequests = Math.max(
    0,
    activeRegionalIntelligenceRequests - 1
  );
}

export const regionalResponseSchema = z.object({
  riskSummary: z.object({
    level: z.enum(['low', 'moderate', 'high', 'critical']),
    headline: z.string().trim().min(1).max(300),
    factors: z.array(z.string().trim().min(1).max(240)).min(1).max(8),
    evidenceSources: z.array(z.enum(REGIONAL_EVIDENCE_SOURCES)).min(1).max(6),
  }).strict(),
  historicalEvents: z.array(
    z.object({
      date: z.string().regex(/^\d{4}-\d{2}-\d{2}$/),
      type: z.literal('wildfire'),
      description: z.string().trim().min(1).max(500),
      severity: z.enum(['low', 'moderate', 'high', 'critical']),
      evidenceSource: z.literal('mtbsPerimeters'),
    }).strict()
  ).max(10),
  actionableItems: z.array(
    z.object({
      priority: z.enum(['immediate', 'short_term', 'long_term']),
      action: z.string().trim().min(1).max(300),
      rationale: z.string().trim().min(1).max(500),
      strategy: z.enum(INTERVENTION_STRATEGIES).optional(),
      evidenceSource: z.enum(REGIONAL_EVIDENCE_SOURCES),
    }).strict()
  ).max(10),
  interventionRecommendations: z.array(
    z.object({
      strategy: z.enum(INTERVENTION_STRATEGIES),
      score: z.number().min(0).max(100),
      whyHere: z.string().trim().min(1).max(500),
      evidenceSource: z.enum(['strategyRecommendations', 'carbonPotential']),
    }).strict()
  ).max(6),
}).strict();

type RegionalResponse = z.infer<typeof regionalResponseSchema>;

function sourceIsAvailable(
  source: RegionalEvidenceSource,
  dataFreshness: Record<string, string>
): boolean {
  return regionalEvidenceFreshnessState(source, dataFreshness[source]) === 'available';
}

/** Rejects structurally valid output that cites evidence absent from context. */
export function responseUsesAvailableEvidence(
  response: RegionalResponse,
  dataFreshness: Record<string, string>
): boolean {
  const sources = [
    ...response.riskSummary.evidenceSources,
    ...response.actionableItems.map((item) => item.evidenceSource),
    ...response.historicalEvents.map((event) => event.evidenceSource),
    ...response.interventionRecommendations.map(
      (recommendation) => recommendation.evidenceSource
    ),
  ];
  return sources.every((source) => sourceIsAvailable(source, dataFreshness));
}

async function checkRateLimit(
  userId: string
): Promise<{ allowed: boolean; retryAfter?: number }> {
  const redis = getRedis();
  const key = `ai-ratelimit:${userId}`;
  const now = Date.now();
  const windowMs = 3_600_000; // 1 hour
  const limit = 20;

  try {
    const result = (await redis.eval(
      `
        redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', ARGV[2])
        local count = redis.call('ZCARD', KEYS[1])
        if count >= tonumber(ARGV[3]) then
          local oldest = redis.call('ZRANGE', KEYS[1], 0, 0, 'WITHSCORES')
          return { 0, oldest[2] or '0' }
        end
        redis.call('ZADD', KEYS[1], ARGV[1], ARGV[4])
        redis.call('EXPIRE', KEYS[1], ARGV[5])
        return { 1, '0' }
      `,
      1,
      key,
      now,
      now - windowMs,
      limit,
      `${now}:${randomUUID()}`,
      Math.ceil(windowMs / 1000)
    )) as [number, string];

    if (Number(result[0]) === 1) return { allowed: true };
    return {
      allowed: false,
      retryAfter: Math.max(1, Math.ceil((Number(result[1]) + windowMs - now) / 1000)),
    };
  } catch {
    // A production cost-control boundary must fail closed when its limiter fails.
    return {
      allowed: process.env.NODE_ENV !== 'production',
      retryAfter: 60,
    };
  }
}

export async function POST(request: NextRequest) {
  if (REGIONAL_INTELLIGENCE_SERVING_STATE === 'inactive') {
    return new Response(
      JSON.stringify({
        error: REGIONAL_INTELLIGENCE_INACTIVE_MESSAGE,
        retryable: false,
      }),
      {
        status: 503,
        headers: {
          'Cache-Control': 'no-store',
          'Content-Type': 'application/json',
          'X-Content-Type-Options': 'nosniff',
        },
      }
    );
  }

  // Auth check
  const session = await getServerSession();
  if (!session?.user) {
    return new Response(JSON.stringify({ error: 'Authentication required', retryable: false }), {
      status: 401,
      headers: { 'Content-Type': 'application/json' },
    });
  }

  const userId = (session.user as { id?: string }).id;
  if (!userId) {
    return new Response(JSON.stringify({ error: 'Authentication required', retryable: false }), {
      status: 401,
      headers: { 'Content-Type': 'application/json' },
    });
  }

  const entitlement = await reserveRegionalIntelligenceUsage(userId);
  if (!entitlement.allowed) {
    return new Response(
      JSON.stringify({ error: entitlement.reason, retryable: false }),
      {
        status: 403,
        headers: {
          'Cache-Control': 'no-store',
          'Content-Type': 'application/json',
          'X-Content-Type-Options': 'nosniff',
        },
      }
    );
  }

  if (!process.env.ANTHROPIC_API_KEY) {
    return new Response(
        JSON.stringify({ error: 'Regional intelligence is not configured', retryable: false }),
      { status: 503, headers: { 'Content-Type': 'application/json' } }
    );
  }

  const bodyResult = await parseBoundedJson(
    request,
    MAX_REGIONAL_INTELLIGENCE_BODY_BYTES
  );
  if (!bodyResult.ok) {
    return new Response(JSON.stringify({ error: bodyResult.error, retryable: false }), {
      status: bodyResult.status,
      headers: { 'Content-Type': 'application/json' },
    });
  }
  const parsedBody = requestSchema.safeParse(bodyResult.data);
  if (!parsedBody.success) {
    return new Response(JSON.stringify({ error: 'Invalid request', retryable: false }), {
      status: 400,
      headers: { 'Content-Type': 'application/json' },
    });
  }
  const body: z.infer<typeof requestSchema> = parsedBody.data;
  const requestedLat =
    body.locationConsent.precision === 'approximate'
      ? Number(body.lat.toFixed(2))
      : body.lat;
  const requestedLon =
    body.locationConsent.precision === 'approximate'
      ? Number(body.lon.toFixed(2))
      : body.lon;

  // Rate limit
  const { allowed, retryAfter } = await checkRateLimit(userId);
  if (!allowed) {
    return new Response(JSON.stringify({ error: 'Rate limit exceeded', retryable: true }), {
      status: 429,
      headers: {
        'Content-Type': 'application/json',
        'Retry-After': String(retryAfter),
      },
    });
  }

  if (!acquireRegionalIntelligenceCapacity()) {
    return new Response(
        JSON.stringify({ error: 'Regional intelligence is at capacity', retryable: true }),
      {
        status: 503,
        headers: {
          'Content-Type': 'application/json',
          'Retry-After': '5',
        },
      }
    );
  }

  let capacityHandedToStream = false;
  try {
    let contextResult: Awaited<ReturnType<typeof assembleRegionalContext>>;
    try {
      contextResult = await assembleRegionalContext(requestedLat, requestedLon);
    } catch {
      return new Response(
        JSON.stringify({ error: 'All data sources unavailable', retryable: false }),
        {
          status: 503,
          headers: { 'Content-Type': 'application/json' },
        }
      );
    }

    const { payload, dataFreshness, cacheHit } = contextResult;
    console.info(`[AI] regional analysis requested cacheHit=${cacheHit}`);

    const encoder = new TextEncoder();
    const abortController = new AbortController();
    request.signal.addEventListener('abort', () => abortController.abort());

    // Client-provided assistant turns are untrusted. Keep prior user questions only.
    const history: ConversationTurn[] = (body.history ?? []).filter(
      (turn) => turn.role === 'user'
    );

    const stream = new ReadableStream({
      async start(controller) {
        try {
          controller.enqueue(
            encoder.encode(
              `event: context\ndata: ${JSON.stringify({ cacheHit, dataFreshness })}\n\n`
          )
        );

          const generator = streamRegionalIntelligence(
            payload,
            dataFreshness,
            history,
            body.question,
            abortController.signal
          );

          let toolResult = '';

          for await (const chunk of generator) {
            if (abortController.signal.aborted) break;

            if (chunk.data.startsWith('__COMPLETE__')) {
              toolResult = chunk.data.slice('__COMPLETE__'.length);
            }
          }

          if (toolResult) {
            try {
              const parsed = regionalResponseSchema.parse(JSON.parse(toolResult));
              if (!responseUsesAvailableEvidence(parsed, dataFreshness)) {
                throw new Error('Response cited unavailable evidence');
              }
              controller.enqueue(
                encoder.encode(
                  `event: done\ndata: ${JSON.stringify({ ...parsed, dataFreshness })}\n\n`
                )
              );
            } catch {
              controller.enqueue(
                encoder.encode(
                  `event: error\ndata: ${JSON.stringify({ message: 'Response parse failed', retryable: false })}\n\n`
                )
              );
            }
          } else {
            controller.enqueue(
              encoder.encode(
                `event: error\ndata: ${JSON.stringify({ message: 'Structured response unavailable', retryable: true })}\n\n`
              )
            );
          }
        } catch (err) {
          if (!abortController.signal.aborted) {
            const internalMsg =
              err instanceof Error ? err.message : 'Unknown error';
            console.error('[AI Stream Error]', internalMsg);
            controller.enqueue(
              encoder.encode(
                `event: error\ndata: ${JSON.stringify({ message: 'An error occurred processing your request.', retryable: true })}\n\n`
              )
            );
          }
        } finally {
          releaseRegionalIntelligenceCapacity();
          controller.close();
        }
      },
    });

    capacityHandedToStream = true;
    return new Response(stream, {
      headers: {
        'Content-Type': 'text/event-stream',
        'Cache-Control': 'no-store, no-transform',
        Connection: 'keep-alive',
        Vary: 'Cookie',
        'X-Accel-Buffering': 'no',
      },
    });
  } finally {
    if (!capacityHandedToStream) releaseRegionalIntelligenceCapacity();
  }
}
