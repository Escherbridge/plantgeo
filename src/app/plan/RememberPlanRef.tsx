"use client";

import { useEffect } from "react";
import { rememberPlanRef } from "@/app/plan/actions";

/** Stores the invite code from the sample link. Renders nothing. */
export function RememberPlanRef({ refCode }: { refCode: string }) {
  useEffect(() => {
    void rememberPlanRef(refCode);
  }, [refCode]);
  return null;
}
