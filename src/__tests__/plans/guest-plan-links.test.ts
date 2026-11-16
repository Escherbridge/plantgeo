import { beforeEach, describe, expect, it } from "vitest";
import {
  GUEST_PLAN_STORAGE_KEY,
  guestPlanEditPath,
  guestPlanEditUrl,
  guestPlanSnapshot,
  migrateStoredGuestPlans,
  readSavedGuestPlans,
  rememberGuestPlan,
  savedGuestPlansFrom,
} from "@/lib/plans/guest-plan-links";

const plan = {
  publicId: "plan_guest12345678901",
  token: "guest-token-value",
  title: "North field",
};

function memoryStorage(initial: string | null = null): Storage {
  const values = new Map<string, string>();
  if (initial !== null) values.set(GUEST_PLAN_STORAGE_KEY, initial);
  return {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => {
      values.set(key, value);
    },
    removeItem: (key: string) => {
      values.delete(key);
    },
    clear: () => values.clear(),
    key: (index: number) => [...values.keys()][index] ?? null,
    get length() {
      return values.size;
    },
  };
}

describe("guest plan links", () => {
  beforeEach(() => {
    localStorage.removeItem(GUEST_PLAN_STORAGE_KEY);
  });

  it("builds the private edit link and ignores a stored value that is not a plan", () => {
    expect(guestPlanEditPath(plan)).toBe("/plan/plan_guest12345678901?token=guest-token-value");
    expect(guestPlanEditUrl("https://plantgeo.aevani.com", plan)).toBe(
      "https://plantgeo.aevani.com/plan/plan_guest12345678901?token=guest-token-value"
    );
    expect(
      savedGuestPlansFrom(
        JSON.stringify([
          plan,
          { publicId: "plan_guest12345678901", token: "other-token-value", title: "duplicate" },
          { publicId: "not-a-plan", token: "guest-token-value", title: "nope" },
          { publicId: plan.publicId, token: "short", title: "nope" },
          { href: "https://evil.example/steal" },
        ])
      )
    ).toEqual([plan]);
    expect(savedGuestPlansFrom("not json")).toEqual([]);
  });

  it("keeps the newest plan first and drops an invalid one", () => {
    const storage = memoryStorage();
    expect(rememberGuestPlan(storage, { ...plan, publicId: "nope", token: "guest-token-value", title: "x" })).toEqual(
      []
    );
    rememberGuestPlan(storage, plan);
    const newer = { publicId: "plan_newer12345678901", token: "newer-token-value", title: "  " };
    expect(rememberGuestPlan(storage, newer)[0]).toEqual({
      publicId: newer.publicId,
      token: newer.token,
      title: "Site plan",
    });
    expect(readSavedGuestPlans(storage).map((entry) => entry.publicId)).toEqual([
      newer.publicId,
      plan.publicId,
    ]);
    expect(rememberGuestPlan(storage, { ...plan, title: "Renamed" })[0]?.title).toBe("Renamed");
    const third = {
      publicId: "plan_third12345678901",
      token: "third-token-value",
      title: "South bed",
    };
    expect(rememberGuestPlan(storage, third).map((entry) => entry.publicId)).toEqual([
      third.publicId,
      plan.publicId,
      newer.publicId,
    ]);
  });

  it("keeps a single stored plan object and rewrites it as the list", () => {
    const storage = memoryStorage(JSON.stringify(plan));
    expect(readSavedGuestPlans(storage)).toEqual([plan]);
    expect(migrateStoredGuestPlans(storage)).toEqual([plan]);
    expect(JSON.parse(storage.getItem(GUEST_PLAN_STORAGE_KEY) ?? "null")).toEqual([plan]);
    const newer = { publicId: "plan_newer12345678901", token: "newer-token-value", title: "Creek" };
    expect(rememberGuestPlan(storage, newer).map((entry) => entry.publicId)).toEqual([
      newer.publicId,
      plan.publicId,
    ]);
  });

  it("reads the browser snapshot after a plan is remembered", () => {
    expect(guestPlanSnapshot()).toEqual([]);
    rememberGuestPlan(localStorage, plan);
    expect(guestPlanSnapshot()).toEqual([plan]);
    expect(guestPlanSnapshot()).toBe(guestPlanSnapshot());
  });
});
