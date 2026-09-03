"use client";

import { createTRPCClient, httpBatchLink, type TRPCLink } from "@trpc/client";
import { createTRPCReact } from "@trpc/react-query";
import superjson from "superjson";
import type { AppRouter } from "@/lib/server/trpc/router";
import { createBudgetedFetch } from "@/lib/net/request-budget";

/**
 * The react-query tRPC hooks. `abortOnUnmount` is what makes every `signal` the routers thread
 * server-side actually fire -- see src/lib/server/services/AGENTS.md §request-cancellation for
 * what a batch does with it.
 */
export const trpc = createTRPCReact<AppRouter>({ abortOnUnmount: true });
export const api = trpc;

/**
 * The single link chain both clients use, so the two can never drift apart. Routes every
 * tRPC-issued request through the shared client-side request budget -- see
 * src/lib/net/AGENTS.md "Both transports". This one edit is what makes the budget uniform
 * structurally: neither consumer below needs its own wiring, nor does any layer added to the
 * router in the future.
 */
export function trpcLinks(): TRPCLink<AppRouter>[] {
  return [
    httpBatchLink({ url: "/api/trpc", transformer: superjson, fetch: createBudgetedFetch("trpc") }),
  ];
}

let vanillaClient: ReturnType<typeof createTRPCClient<AppRouter>> | null = null;

/**
 * The provider-free tRPC client, for call sites that are plain functions rather than hooks --
 * a react-query `queryFn` and its `prefetchQuery` twin share one, so the prefetch path is the
 * same transport as the query path. Built on first use: the relative URL above is only
 * meaningful in the browser.
 */
export function getVanillaTrpcClient() {
  vanillaClient ??= createTRPCClient<AppRouter>({ links: trpcLinks() });
  return vanillaClient;
}
