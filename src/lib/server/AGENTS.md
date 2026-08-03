# `src/lib/server` — module notes

Rationale and constraints that the code's one-line doc comments deliberately omit.
Add a section per module as it grows; sections are independent.

## §regional-intelligence

The AI advisor that answers "what should be done about this place?" for a map
point. Files: `services/ai-prompt.ts` (agent loop), `services/regional-context.ts`
(evidence assembly), `services/web-evidence.ts` (search), `services/ai-conversations.ts`
(multi-turn persistence), `security/regional-intelligence-access.ts` (quota),
`app/api/ai/regional-intelligence/route.ts` (SSE transport),
`lib/regional-intelligence.ts` (shared client/server contract).

### The feature recommends; it does not certify

The agent's primary job is to suggest remediation strategies. That output is
**AI-generated advisory content, not a validated model release.** Three things
enforce that, and none of them are cosmetic:

- Every report carries a literal `aiGenerated: true`. A renderer cannot
  accidentally present it as a warehouse product.
- Every claim carries an `evidenceOrigin` of `warehouse`, `web`, or
  `model_inference`. Most remediation reasoning is legitimately inference; the
  contract makes the model say so instead of dressing inference up as data.
- `professionalConsultation` and per-item `consultProfessionals` are **required**
  by the tool schema. The model cannot return a plan without naming who should
  vet it.

This replaces an earlier design in which the route rejected any response citing
a source that was not freshly published. That gate made the feature
unshippable — the strategy and carbon evidence planes it depended on are
permanent `unavailable` stubs, so no recommendation could ever pass. The gate
was removed deliberately (2026-08-02). The cost is real and should be understood:
**model-asserted facts now render alongside warehouse-sourced ones**, so the
data-freshness footer describes what was *observed*, not what the model *said*.
Origin badges, not the footer, are what tell a reader where a claim came from.

### Evidence assembly never blocks the answer

`assembleRegionalContext` used to throw when nothing was published, which
turned a thin data day into a 503. It now returns whatever resolved plus
`contextIsEmpty`, and the prompt instructs the model to state what it could not
see. A source marked `unavailable` means *unmeasured*, not *absent* — the
prompt says this explicitly because the distinction is easy for a model to blur
and consequential for a land manager.

`REGIONAL_EVIDENCE_SOURCES` is the set of layers the platform can date-stamp,
not a ceiling on what the agent may discuss. Fire detections, perimeters, and
weather were added to it because they are the layers actually populated; soil,
MTBS, strategy scores, and carbon remain declared-but-unpublished so the
contract is ready when those planes land.

### Quota is the cost boundary, and it fails closed

`reserveRegionalIntelligenceUsage` reserves a slot in a Redis ZSET **before**
context assembly or any model call, so a rejected caller costs neither an
upstream query nor a token. The reservation is one Lua script because a
read-then-write pair lets two replicas both pass at the limit.

In production an unreachable Redis denies the request. An unmetered agent is an
uncapped bill, and that is the worse failure. Development allows it through so a
local run without Redis is not blocked.

`resolveEntitlementPolicy` is the billing seam: it currently maps every
signed-in account to the `signed_in` tier. Replacing its body with a real
subscription/partner lookup is the entire integration — the reservation logic
below it does not change.

### Conversation history is server-side on purpose

Multi-turn state lives in `ai_conversations` / `ai_messages`, keyed by
`conversationId`. The client sends only that id. An earlier version accepted
history in the request body and had to discard assistant turns as untrusted,
which meant the model could never see its own prior answers — follow-up
questions were not really follow-ups. Reading history from the database fixes
that and removes the forgery surface at the same time.

### The agent loop is bounded in two dimensions

`MAX_TOOL_ROUNDS` bounds wall-clock and token spend; `MAX_SEARCHES_PER_REQUEST`
bounds vendor cost separately, because one runaway round could otherwise burn
the whole search budget. The final round forces `tool_choice` to the report
tool, so a model that keeps wanting to search still returns something usable.
Search failures come back as `is_error` tool results rather than aborting the
turn — degraded advice beats no advice.

Prompt-injection surface: the user's question is fenced in `<user_question>`
tags and the system prompt states that content inside them is untrusted input.
Web search results are model-visible text from arbitrary pages and are treated
the same way — they inform the report, they do not redirect the agent.

### Search provider is swappable by design

`WebEvidenceProvider` exists so Jina can be replaced with Brave, Tavily, or Exa
by editing one file. Jina is configured because search and clean markdown
extraction come from one vendor. Search requests send
`X-Respond-With: no-content` so a query returns snippets; a full page body is
fetched only when the agent asks to read one. Snippets are hard-truncated —
token cost, not result quality, is the binding constraint on this call.

No `JINA_API_KEY` is a supported state, not a broken one: the provider resolves
to `null` and the system prompt tells the model it is working offline.
