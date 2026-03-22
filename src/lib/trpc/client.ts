"use client";

import { createTRPCReact } from "@trpc/react-query";
import type { AppRouter } from "@/lib/server/trpc/router";

export const trpc = createTRPCReact<AppRouter>();
export const api = trpc;
