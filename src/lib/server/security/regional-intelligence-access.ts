/** Fail-closed access contract; see docs/data-ingestion-and-serving-contract.md. */
export const REGIONAL_INTELLIGENCE_SERVING_STATE = "inactive" as const;

export const REGIONAL_INTELLIGENCE_INACTIVE_MESSAGE =
  "Regional intelligence is paused until warehouse-backed evidence and its provenance contract are published";

export const REGIONAL_INTELLIGENCE_ENTITLEMENT_MESSAGE =
  "Regional intelligence requires an active paid account or partner entitlement";

export interface RegionalIntelligenceEntitlement {
  allowed: boolean;
  reason: string;
}

/**
 * This deliberately denies every caller until an audited billing/partner ledger
 * can atomically reserve durable usage before any model or context work starts.
 */
export async function reserveRegionalIntelligenceUsage(
  _userId: string
): Promise<RegionalIntelligenceEntitlement> {
  return {
    allowed: false,
    reason: REGIONAL_INTELLIGENCE_ENTITLEMENT_MESSAGE,
  };
}
