/** Legacy outbound priority-zone publication remains disabled by design. */
export const PRIORITY_ZONE_WEBHOOK_STATE = "inactive" as const;

export interface PriorityZoneWebhookPayload {
  event: "priority_zones_updated";
  zones: Array<{
    zoneId: string;
    strategyType: string;
    requestCount: number;
    totalVotes: number;
    bbox: [number, number, number, number] | null;
    centroidLat: number | null;
    centroidLon: number | null;
    locationName: string | null;
  }>;
  timestamp: string;
}

/** Never sends raw-derived opportunity coordinates to an arbitrary webhook. */
export async function dispatchPriorityZoneWebhook(): Promise<void> {
  return;
}
