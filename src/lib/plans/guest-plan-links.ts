/**
 * Guest edit links kept in this browser. The server stores only a SHA-256 hash,
 * so the raw token has to live here if the guest is to reopen the plan.
 */

export const GUEST_PLAN_STORAGE_KEY = "plantgeo-guest-plans";

/** Same-tab listeners. The storage event only fires in other tabs. */
export const GUEST_PLAN_CHANGE_EVENT = "plantgeo-guest-plans";

const PUBLIC_ID = /^plan_[A-Za-z0-9_-]{16,64}$/;
const GUEST_TOKEN = /^[A-Za-z0-9_-]{16,512}$/;
const MAX_SAVED = 20;

export type SavedGuestPlan = {
  publicId: string;
  token: string;
  title: string;
};

const EMPTY_GUEST_PLANS: SavedGuestPlan[] = [];

let cachedRaw: string | null | undefined;
let cachedPlans: SavedGuestPlan[] = EMPTY_GUEST_PLANS;

export function guestPlanEditPath(plan: Pick<SavedGuestPlan, "publicId" | "token">): string {
  const params = new URLSearchParams();
  params.set("token", plan.token);
  return `/plan/${plan.publicId}?${params.toString()}`;
}

export function guestPlanEditUrl(
  origin: string,
  plan: Pick<SavedGuestPlan, "publicId" | "token">
): string {
  return new URL(guestPlanEditPath(plan), origin).toString();
}

function asSaved(value: unknown): SavedGuestPlan | null {
  if (!value || typeof value !== "object") return null;
  const record = value as Record<string, unknown>;
  if (typeof record.publicId !== "string" || !PUBLIC_ID.test(record.publicId)) return null;
  if (typeof record.token !== "string" || !GUEST_TOKEN.test(record.token)) return null;
  const title = typeof record.title === "string" ? record.title.trim().slice(0, 200) : "";
  return {
    publicId: record.publicId,
    token: record.token,
    title: title.length > 0 ? title : "Site plan",
  };
}

/** Drops malformed entries. A stored value is never used as a URL except through `guestPlanEditPath`. */
export function savedGuestPlansFrom(raw: string | null): SavedGuestPlan[] {
  if (!raw) return EMPTY_GUEST_PLANS;
  try {
    const parsed = JSON.parse(raw) as unknown;
    if (!Array.isArray(parsed)) return EMPTY_GUEST_PLANS;
    const seen = new Set<string>();
    const plans: SavedGuestPlan[] = [];
    for (const entry of parsed) {
      const plan = asSaved(entry);
      if (!plan || seen.has(plan.publicId)) continue;
      seen.add(plan.publicId);
      plans.push(plan);
      if (plans.length === MAX_SAVED) break;
    }
    return plans;
  } catch {
    return EMPTY_GUEST_PLANS;
  }
}

export function readSavedGuestPlans(storage: Pick<Storage, "getItem">): SavedGuestPlan[] {
  try {
    return savedGuestPlansFrom(storage.getItem(GUEST_PLAN_STORAGE_KEY));
  } catch {
    return EMPTY_GUEST_PLANS;
  }
}

/** Stable snapshot for `useSyncExternalStore`. Same raw string returns the same array. */
export function guestPlanSnapshot(): SavedGuestPlan[] {
  if (typeof window === "undefined") return EMPTY_GUEST_PLANS;
  let raw: string | null;
  try {
    raw = window.localStorage.getItem(GUEST_PLAN_STORAGE_KEY);
  } catch {
    return EMPTY_GUEST_PLANS;
  }
  if (raw === cachedRaw) return cachedPlans;
  cachedRaw = raw;
  cachedPlans = raw ? savedGuestPlansFrom(raw) : EMPTY_GUEST_PLANS;
  return cachedPlans;
}

export function subscribeSavedGuestPlans(onChange: () => void): () => void {
  if (typeof window === "undefined") return () => {};
  window.addEventListener(GUEST_PLAN_CHANGE_EVENT, onChange);
  window.addEventListener("storage", onChange);
  return () => {
    window.removeEventListener(GUEST_PLAN_CHANGE_EVENT, onChange);
    window.removeEventListener("storage", onChange);
  };
}

export function rememberGuestPlan(
  storage: Pick<Storage, "getItem" | "setItem">,
  plan: SavedGuestPlan
): SavedGuestPlan[] {
  const saved = asSaved(plan);
  if (!saved) return readSavedGuestPlans(storage);
  const next = [
    saved,
    ...readSavedGuestPlans(storage).filter((entry) => entry.publicId !== saved.publicId),
  ].slice(0, MAX_SAVED);
  const raw = JSON.stringify(next);
  storage.setItem(GUEST_PLAN_STORAGE_KEY, raw);
  if (typeof window !== "undefined" && storage === window.localStorage) {
    cachedRaw = raw;
    cachedPlans = next;
    window.dispatchEvent(new Event(GUEST_PLAN_CHANGE_EVENT));
  }
  return next;
}
