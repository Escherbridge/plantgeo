/** Legacy aggregates remain disabled; see docs/data-ingestion-and-serving-contract.md. */
export const PRIORITY_ZONE_RECOMPUTATION_STATE = "inactive" as const;

/**
 * Keeps legacy workers from deriving or refreshing raw-community aggregates.
 * A reviewed workspace-scoped publication will replace this boundary.
 */
export async function recomputePriorityZones(): Promise<void> {
  return;
}
