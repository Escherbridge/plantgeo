import Anthropic from '@anthropic-ai/sdk';
import type { RegionalContextPayload } from './regional-context';

export interface ConversationTurn {
  role: 'user' | 'assistant';
  content: string;
}

export interface RegionalIntelligenceResponse {
  riskSummary: {
    level: 'low' | 'moderate' | 'high' | 'critical';
    headline: string;
    factors: string[];
  };
  historicalEvents: {
    date: string;
    type: string;
    description: string;
    severity: string;
  }[];
  actionableItems: {
    priority: 'immediate' | 'short_term' | 'long_term';
    action: string;
    rationale: string;
    strategy?: string;
  }[];
  interventionRecommendations: {
    strategy: string;
    score: number;
    whyHere: string;
    suppliersAvailable: boolean;
  }[];
  dataFreshness: Record<string, string>;
}

const MAX_HISTORY_TURNS = 5;

// Define the tool schema for structured output
const REPORT_TOOL: Anthropic.Messages.Tool = {
  name: 'regional_intelligence_report',
  description:
    'Generate a structured regional intelligence report for the given location based on environmental data.',
  input_schema: {
    type: 'object' as const,
    properties: {
      riskSummary: {
        type: 'object',
        properties: {
          level: { type: 'string', enum: ['low', 'moderate', 'high', 'critical'] },
          headline: { type: 'string' },
          factors: { type: 'array', items: { type: 'string' } },
        },
        required: ['level', 'headline', 'factors'],
      },
      historicalEvents: {
        type: 'array',
        items: {
          type: 'object',
          properties: {
            date: { type: 'string' },
            type: { type: 'string' },
            description: { type: 'string' },
            severity: { type: 'string' },
          },
          required: ['date', 'type', 'description', 'severity'],
        },
      },
      actionableItems: {
        type: 'array',
        items: {
          type: 'object',
          properties: {
            priority: {
              type: 'string',
              enum: ['immediate', 'short_term', 'long_term'],
            },
            action: { type: 'string' },
            rationale: { type: 'string' },
            strategy: { type: 'string' },
          },
          required: ['priority', 'action', 'rationale'],
        },
      },
      interventionRecommendations: {
        type: 'array',
        items: {
          type: 'object',
          properties: {
            strategy: { type: 'string' },
            score: { type: 'number' },
            whyHere: { type: 'string' },
            suppliersAvailable: { type: 'boolean' },
          },
          required: ['strategy', 'score', 'whyHere', 'suppliersAvailable'],
        },
      },
      dataFreshness: {
        type: 'object',
        additionalProperties: { type: 'string' },
      },
    },
    required: [
      'riskSummary',
      'historicalEvents',
      'actionableItems',
      'interventionRecommendations',
      'dataFreshness',
    ],
  },
};

function buildSystemPrompt(): string {
  return `You are PlantGeo Regional Intelligence, an environmental analysis agent. You analyze location-specific environmental data and produce actionable intelligence reports.

Rules:
- Base ALL claims strictly on the provided context data. Never hallucinate data values.
- Quantify risk factors using the numeric scores provided (e.g., "fire risk score of 78/100").
- For actionable items, link each to a specific data signal (e.g., "Given D3 drought classification...").
- For intervention recommendations, use only these strategy types: keyline, silvopasture, reforestation, biochar, water_harvesting, cover_cropping.
- For historical events, use MTBS fire data from the context.
- Copy dataFreshness directly from the context — do not generate timestamps.
- Be concise and actionable. No jargon. Prioritize what matters most.
- When fire risk >= 60 or drought >= D2, include at least one "immediate" priority actionable item.`;
}

function buildUserMessage(
  payload: RegionalContextPayload,
  userQuestion?: string
): string {
  const question =
    userQuestion || 'Provide a full regional intelligence report for this location.';
  return `## Location: ${payload.location.lat.toFixed(4)}°N, ${payload.location.lon.toFixed(4)}°W

## Environmental Data Context
${JSON.stringify(payload, null, 2)}

## Question
${question}

Use the regional_intelligence_report tool to provide your structured response.`;
}

export async function* streamRegionalIntelligence(
  payload: RegionalContextPayload,
  dataFreshness: Record<string, string>,
  history: ConversationTurn[],
  userQuestion?: string,
  signal?: AbortSignal
): AsyncGenerator<{ type: 'text' | 'tool_use'; data: string }> {
  const client = new Anthropic();
  const model = process.env.ANTHROPIC_MODEL ?? 'claude-haiku-4-5-20251001';

  // Build messages array from history + new message
  const messages: Anthropic.Messages.MessageParam[] = [];

  // Add truncated history (last MAX_HISTORY_TURNS * 2 individual messages)
  const recentHistory = history.slice(-MAX_HISTORY_TURNS * 2);
  for (const turn of recentHistory) {
    messages.push({ role: turn.role, content: turn.content });
  }

  // Add new user message
  messages.push({
    role: 'user',
    content: buildUserMessage(payload, userQuestion),
  });

  const toolChoice: Anthropic.Messages.ToolChoice =
    history.length > 0
      ? { type: 'auto' }
      : { type: 'tool', name: 'regional_intelligence_report' };

  const stream = client.messages.stream(
    {
      model,
      max_tokens: 2048,
      system: buildSystemPrompt(),
      messages,
      tools: [REPORT_TOOL],
      tool_choice: toolChoice,
    },
    { signal }
  );

  let toolInput = '';

  for await (const event of stream) {
    if (event.type === 'content_block_delta') {
      const delta = event.delta;
      if (delta.type === 'text_delta') {
        yield { type: 'text', data: delta.text };
      } else if (delta.type === 'input_json_delta') {
        toolInput += delta.partial_json;
        yield { type: 'tool_use', data: delta.partial_json };
      }
    }
  }

  // Final yield with complete tool input so caller can parse it
  if (toolInput) {
    yield { type: 'tool_use', data: `__COMPLETE__${toolInput}` };
  }
}

export { buildSystemPrompt, buildUserMessage, REPORT_TOOL };
