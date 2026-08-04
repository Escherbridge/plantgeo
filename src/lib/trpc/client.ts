"use client";

import { createTRPCClient, httpBatchLink, type TRPCLink } from "@trpc/client";
import { createTRPCReact } from "@trpc/react-query";
import superjson from "superjson";
import type { AppRouter } from "@/lib/server/trpc/router";

export const trpc = createTRPCReact<AppRouter>();
export const api = trpc;

/** The single link chain both clients use, so the two can never drift apart. */
export function trpcLinks(): TRPCLink<AppRouter>[] {
  return [httpBatchLink({ url: "/api/trpc", transformer: superjson })];
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
