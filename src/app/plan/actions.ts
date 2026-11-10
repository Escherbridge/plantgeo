"use server";

import { cookies } from "next/headers";
import {
  normalizeRefCode,
  PLAN_REF_COOKIE,
  PLAN_REF_MAX_AGE_SECONDS,
} from "@/lib/plans/ref-code";

/** Remembers a valid invite code so a later plan keeps it. Invalid codes are ignored. */
export async function rememberPlanRef(refCode: string): Promise<void> {
  const normalized = normalizeRefCode(refCode);
  if (!normalized) return;
  const jar = await cookies();
  jar.set(PLAN_REF_COOKIE, normalized, {
    httpOnly: true,
    sameSite: "lax",
    secure: process.env.NODE_ENV === "production",
    path: "/",
    maxAge: PLAN_REF_MAX_AGE_SECONDS,
  });
}
