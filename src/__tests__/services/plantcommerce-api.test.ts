import { describe, expect, it } from "vitest";
import { parseSupplierDirectoryPayload } from "@/lib/server/services/plantcommerce-api";

const supplier = {
  id: "supplier-1",
  name: "Example Nursery",
  strategyTypes: ["reforestation"],
  region: "Front Range",
  rating: 4.5,
  productsAvailable: ["native seedlings"],
  url: "https://example.org/supplier",
};

describe("Aevani supplier contract", () => {
  it("accepts a bounded HTTPS supplier record", () => {
    expect(parseSupplierDirectoryPayload({ suppliers: [supplier] })).toEqual([
      supplier,
    ]);
  });

  it("rejects unsafe links and oversized result sets", () => {
    expect(
      parseSupplierDirectoryPayload({
        suppliers: [{ ...supplier, url: "javascript:alert(1)" }],
      })
    ).toBeNull();
    expect(
      parseSupplierDirectoryPayload({
        suppliers: Array.from({ length: 101 }, (_, index) => ({
          ...supplier,
          id: `supplier-${index}`,
        })),
      })
    ).toBeNull();
  });
});
