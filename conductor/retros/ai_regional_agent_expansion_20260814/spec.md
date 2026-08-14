---
type: specification
---

# Conductor Track Specification: AI Regional Intelligence Agent ML Expansion

## Track ID: `ai_regional_agent_expansion_20260814`

### Overview
This track expands the capability of PlantGeo's AI Regional Intelligence Agent (`RegionalIntelligencePanel.tsx`, `ai-prompt.ts`, `ai-conversations.ts`) to act as an expert interactive assistant for **Regenerative Agriculture, Agroforestry, Biochar Enhancement, Wildfire Prevention, and Drought Resilience**.

Instead of only returning static environmental metrics, the AI Agent dynamically retrieves active ML Strategy Recommendation predictions, interprets causal benefit estimates ($\hat{\tau}$), answers natural language questions about land remediation options, and generates structured land management reports with data provenance citations.

### Objectives
1. **ML Strategy Context Injection**: Update `src/lib/server/services/regional-context.ts` to query active ML Strategy Materialized Views (`geo.mv_strategy_recommendations_*`) for queried coordinates.
2. **Causal & Literature Evidence Prompting**: Update `ai-prompt.ts` system prompt to instruct Claude 3.5 on explaining practice recommendations (e.g. keyline plowing vs silvopasture vs biochar application rate), citing dataset sources (NASA FIRMS, USGS, SoilGrids, USDM).
3. **Structured Remediation Reports**: Add tool execution capabilities to `ai-prompt.ts` allowing the AI Agent to generate downloadable land remediation reports containing map screenshots, practice steps, and projected moisture/fire impact metrics.
4. **Interactive Q&A in `RegionalIntelligencePanel.tsx`**: Update the side panel chat UI with quick-action chips ("Explain Strategy Selection", "Calculate Biochar Benefit", "Wildfire Risk Reduction Plan").

### Key Deliverables
- **Context Builder Update (`src/lib/server/services/regional-context.ts`)**: Injects ML strategy predictions and active community proposals into agent prompt payload.
- **System Prompt Enhancement (`src/lib/server/services/ai-prompt.ts`)**: Adds domain rules for regenerative agriculture, biochar rates, and agroforestry shelterbelt placement.
- **UI Side Panel Upgrade (`src/components/panels/RegionalIntelligencePanel.tsx`)**: Displays strategy breakdown cards, evidence citations, and downloadable report triggers.
- **Verification Tests**: Vitest suite checking AI prompt context formatting and tool payload output.
