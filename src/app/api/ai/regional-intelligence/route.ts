export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

import { NextRequest } from 'next/server';
import { z } from 'zod';
import { getServerSession } from '@/lib/server/auth';
import { parseBoundedJson } from '@/lib/server/security/ingress';
import { assembleRegionalContext } from '@/lib/server/services/regional-context';
import { streamRegionalIntelligence } from '@/lib/server/services/ai-prompt';
import {
  openConversation,
  recordExchange,
} from '@/lib/server/services/ai-conversations';
import {
  EVIDENCE_ORIGINS,
  INTERVENTION_STRATEGIES,
  PROFESSIONAL_DISCIPLINES,
  REGIONAL_EVIDENCE_SOURCES,
} from '@/lib/regional-intelligence';
import {
  REGIONAL_INTELLIGENCE_INACTIVE_MESSAGE,
  REGIONAL_INTELLIGENCE_SERVING_STATE,
  reserveRegionalIntelligenceUsage,
} from '@/lib/server/security/regional-intelligence-access';

const MAX_BODY_BYTES = 16 * 1024;
const DEFAULT_QUESTION = 'Analyze this location';

/**
 * The most layer rows one request may report a day for.
 *
 * The layer registry holds 20 toggles and the map cannot draw a row that is not one of them,
 * so an honest client never reaches this. 24 leaves headroom for a registry that grows before
 * both halves redeploy, and still keeps the array far under MAX_BODY_BYTES: 24 entries at
 * roughly 70 bytes of JSON each is about 1.7 KB of a 16 KB budget, which leaves `question`
 * its full 1000 characters. The bound is here rather than left to the byte cap alone because
 * a body that fits in 16 KB can still carry thousands of tiny entries, and every one of them
 * would become a line of prompt.
 */
export const MAX_VIEWED_LAYERS = 24;

const CALENDAR_DATE_PATTERN = /^\d{4}-\d{2}-\d{2}$/;

/** One layer row's own day. `hasDataOnDate` is the client's claim, cross-checked server-side. */
const viewedLayerSchema = z
  .object({
    layer: z.string().trim().min(1).max(64),
    date: z.string().regex(CALENDAR_DATE_PATTERN),
    hasDataOnDate: z.boolean(),
  })
  .strict();

export const requestSchema = z.object({
  lat: z.number().min(-90).max(90),
  lon: z.number().min(-180).max(180),
  question: z.string().max(1000).optional(),
  conversationId: z.string().uuid().optional(),
  // Optional on purpose: a client built before per-layer dates, and a map with every layer
  // switched off, both send nothing, and both must still get an answer.
  viewedLayers: z.array(viewedLayerSchema).max(MAX_VIEWED_LAYERS).optional(),
  locationConsent: z
    .object({
      precision: z.enum(['approximate', 'exact']),
      confirmed: z.literal(true),
    })
    .strict(),
});

/** Mirrors the remediation_report tool schema; the model's output is untrusted. */
export const remediationReportSchema = z
  .object({
    riskSummary: z
      .object({
        level: z.enum(['low', 'moderate', 'high', 'critical']),
        headline: z.string().trim().min(1).max(300),
        factors: z.array(z.string().trim().min(1).max(240)).max(8),
        evidenceOrigin: z.enum(EVIDENCE_ORIGINS),
        evidenceSources: z.array(z.enum(REGIONAL_EVIDENCE_SOURCES)).max(9),
      })
      .strict(),
    observations: z
      .array(
        z
          .object({
            statement: z.string().trim().min(1).max(500),
            evidenceOrigin: z.enum(EVIDENCE_ORIGINS),
            evidenceSource: z.enum(REGIONAL_EVIDENCE_SOURCES).optional(),
          })
          .strict()
      )
      .max(12),
    remediation: z
      .array(
        z
          .object({
            strategy: z.enum(INTERVENTION_STRATEGIES),
            title: z.string().trim().min(1).max(160),
            rationale: z.string().trim().min(1).max(900),
            timeframe: z.enum(['immediate', 'short_term', 'long_term']),
            confidence: z.enum(['low', 'moderate', 'high']),
            consultProfessionals: z
              .array(z.enum(PROFESSIONAL_DISCIPLINES))
              .max(5),
            evidenceOrigin: z.enum(EVIDENCE_ORIGINS),
            evidenceSource: z.enum(REGIONAL_EVIDENCE_SOURCES).optional(),
          })
          .strict()
      )
      .max(8),
    professionalConsultation: z.string().trim().min(1).max(600),
  })
  .strict();

let activeRequests = 0;

function maximumConcurrentRequests(): number {
  const configured = Number(
    process.env.REGIONAL_INTELLIGENCE_MAX_CONCURRENT_PER_REPLICA
  );
  return Number.isInteger(configured) && configured > 0
    ? Math.min(configured, 16)
    : 4;
}

/** Reserves bounded per-replica capacity before regional context assembly. */
export function acquireRegionalIntelligenceCapacity(): boolean {
  if (activeRequests >= maximumConcurrentRequests()) return false;
  activeRequests += 1;
  return true;
}

/** Releases one regional-intelligence capacity reservation. */
export function releaseRegionalIntelligenceCapacity(): void {
  activeRequests = Math.max(0, activeRequests - 1);
}

function jsonResponse(
  body: Record<string, unknown>,
  status: number,
  extraHeaders: Record<string, string> = {}
): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      'Cache-Control': 'no-store',
      'Content-Type': 'application/json',
      'X-Content-Type-Options': 'nosniff',
      ...extraHeaders,
    },
  });
}

function sse(event: string, data: unknown): string {
  return `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
}

export async function POST(request: NextRequest) {
  if (REGIONAL_INTELLIGENCE_SERVING_STATE !== 'active') {
    return jsonResponse(
      { error: REGIONAL_INTELLIGENCE_INACTIVE_MESSAGE, retryable: false },
      503
    );
  }

  const session = await getServerSession();
  const userId = (session?.user as { id?: string } | undefined)?.id;
  if (!userId) {
    return jsonResponse(
      { error: 'Authentication required', retryable: false },
      401
    );
  }

  if (!process.env.ANTHROPIC_API_KEY?.trim()) {
    return jsonResponse(
      { error: 'Regional intelligence is not configured', retryable: false },
      503
    );
  }

  const bodyResult = await parseBoundedJson(request, MAX_BODY_BYTES);
  if (!bodyResult.ok) {
    return jsonResponse({ error: bodyResult.error, retryable: false }, bodyResult.status);
  }
  const parsedBody = requestSchema.safeParse(bodyResult.data);
  if (!parsedBody.success) {
    return jsonResponse({ error: 'Invalid request', retryable: false }, 400);
  }
  const body = parsedBody.data;

  // Reserve quota before any context assembly or model work, so a rejected
  // caller never costs an upstream query or a token.
  const entitlement = await reserveRegionalIntelligenceUsage(userId);
  if (!entitlement.allowed) {
    return jsonResponse(
      { error: entitlement.reason, retryable: entitlement.retryAfterSeconds !== null },
      entitlement.retryAfterSeconds === null ? 403 : 429,
      entitlement.retryAfterSeconds === null
        ? {}
        : { 'Retry-After': String(entitlement.retryAfterSeconds) }
    );
  }

  const approximate = body.locationConsent.precision === 'approximate';
  const lat = approximate ? Number(body.lat.toFixed(2)) : body.lat;
  const lon = approximate ? Number(body.lon.toFixed(2)) : body.lon;

  if (!acquireRegionalIntelligenceCapacity()) {
    return jsonResponse(
      { error: 'Regional intelligence is at capacity', retryable: true },
      503,
      { 'Retry-After': '5' }
    );
  }

  let capacityHandedToStream = false;
  try {
    let context: Awaited<ReturnType<typeof assembleRegionalContext>>;
    try {
      context = await assembleRegionalContext(lat, lon, body.viewedLayers ?? []);
    } catch (error) {
      console.error('[AI] context assembly failed', error);
      return jsonResponse(
        { error: 'Location context is unavailable', retryable: true },
        503
      );
    }

    const { payload, dataFreshness, contextIsEmpty, temporalContext } = context;
    const question = body.question?.trim() || DEFAULT_QUESTION;

    let conversation: Awaited<ReturnType<typeof openConversation>>;
    try {
      conversation = await openConversation({
        userId,
        conversationId: body.conversationId,
        lat,
        lon,
        geohash: payload.location.geohash,
        question: body.question,
      });
    } catch (error) {
      console.error('[AI] conversation open failed', error);
      return jsonResponse(
        { error: 'Could not start the conversation', retryable: true },
        503
      );
    }

    const abortController = new AbortController();
    request.signal.addEventListener('abort', () => abortController.abort());
    const encoder = new TextEncoder();

    const stream = new ReadableStream({
      async start(controller) {
        const send = (event: string, data: unknown) =>
          controller.enqueue(encoder.encode(sse(event, data)));

        try {
          send('context', {
            conversationId: conversation.id,
            dataFreshness,
            contextIsEmpty,
            quotaRemaining: entitlement.remaining,
          });

          let narration = '';
          let report: unknown;
          let webSources: { title: string; url: string }[] = [];
          let refused = false;

          for await (const event of streamRegionalIntelligence(
            payload,
            dataFreshness,
            contextIsEmpty,
            temporalContext,
            conversation.history,
            question,
            abortController.signal
          )) {
            if (abortController.signal.aborted) break;

            switch (event.type) {
              case 'text':
                narration += event.text;
                send('delta', { text: event.text });
                break;
              case 'search':
                send('search', {
                  query: event.query,
                  resultCount: event.resultCount,
                });
                break;
              case 'sources':
                webSources = event.sources;
                break;
              case 'report':
                report = event.report;
                break;
              case 'refusal':
                refused = true;
                break;
            }
          }

          if (abortController.signal.aborted) return;

          if (refused) {
            send('error', {
              message:
                'The assistant declined to answer this request. Try rephrasing it.',
              retryable: false,
            });
            return;
          }

          const parsed = remediationReportSchema.safeParse(report);
          if (!parsed.success) {
            console.error('[AI] report validation failed', parsed.error?.issues);
            send('error', {
              message: 'The analysis could not be completed. Please try again.',
              retryable: true,
            });
            return;
          }

          const answer = {
            aiGenerated: true as const,
            ...parsed.data,
            webSources,
            dataFreshness,
          };

          try {
            await recordExchange({
              conversationId: conversation.id,
              question,
              answer: narration.trim() || parsed.data.riskSummary.headline,
              structuredResponse: answer,
            });
          } catch (error) {
            // A persistence failure must not cost the user their answer.
            console.error('[AI] conversation persistence failed', error);
          }

          send('done', answer);
        } catch (error) {
          if (abortController.signal.aborted) return;
          console.error(
            '[AI] stream error',
            error instanceof Error ? error.message : error
          );
          send('error', {
            message: 'An error occurred processing your request.',
            retryable: true,
          });
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
