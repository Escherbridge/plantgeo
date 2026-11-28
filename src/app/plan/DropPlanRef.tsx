"use client";

import { useEffect } from "react";
import { forgetPlanRef } from "@/app/plan/actions";

/** Clears a stored invite code. Renders nothing. */
export function DropPlanRef() {
  useEffect(() => {
    void forgetPlanRef();
  }, []);
  return null;
}
