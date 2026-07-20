import { z } from "zod";
import type { Supplier } from "@/lib/strategy-contracts";

export type { Supplier } from "@/lib/strategy-contracts";

export const PARTNER_SUPPLIER_DIRECTORY_STATE = "inactive" as const;

const httpsUrlSchema = z
  .string()
  .url()
  .max(2_048)
  .refine((value) => new URL(value).protocol === "https:");

const supplierSchema = z
  .object({
    id: z.string().min(1).max(160),
    name: z.string().min(1).max(200),
    strategyTypes: z.array(z.string().min(1).max(100)).max(25),
    region: z.string().min(1).max(200),
    rating: z.number().finite().min(0).max(5),
    productsAvailable: z.array(z.string().min(1).max(200)).max(100),
    url: httpsUrlSchema.optional(),
  })
  .strict();

const supplierDirectorySchema = z
  .object({ suppliers: z.array(supplierSchema).max(100) })
  .strict();

export class SupplierDirectoryInactiveError extends Error {
  constructor() {
    super(
      "Partner supplier matching is inactive until an approved directory, entitlement, and outbound-location consent contract exist"
    );
    this.name = "SupplierDirectoryInactiveError";
  }
}

export function parseSupplierDirectoryPayload(value: unknown): Supplier[] | null {
  const parsed = supplierDirectorySchema.safeParse(value);
  return parsed.success ? parsed.data.suppliers : null;
}

export function encodeGeohashKey(lat: number, lon: number): string {
  return `${lat.toFixed(2)}:${lon.toFixed(2)}`;
}

/** Never forwards a selected location to an external supplier directory. */
export async function getStrategySuppliers(
  _strategyId: string,
  _lat: number,
  _lon: number
): Promise<Supplier[]> {
  throw new SupplierDirectoryInactiveError();
}
