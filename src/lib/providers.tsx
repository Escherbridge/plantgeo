"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";
import { SessionProvider } from "next-auth/react";
import { trpc, trpcLinks } from "@/lib/trpc/client";
import { indexedDbLayerQueryPersister } from "@/lib/cache/query-persister";

export function Providers({ children }: { children: React.ReactNode }) {
  const [queryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 60 * 1000,
            refetchOnWindowFocus: false,
            // Allowlisted geospatial layer reads only; see src/lib/cache/AGENTS.md.
            persister: indexedDbLayerQueryPersister,
          },
        },
      })
  );

  const [trpcClient] = useState(() => trpc.createClient({ links: trpcLinks() }));

  return (
    <SessionProvider>
      <trpc.Provider client={trpcClient} queryClient={queryClient}>
        <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
      </trpc.Provider>
    </SessionProvider>
  );
}
