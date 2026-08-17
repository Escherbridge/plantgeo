"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";
import { SessionProvider } from "next-auth/react";
import { trpc, trpcLinks } from "@/lib/trpc/client";
import { createIndexedDbLayerQueryPersister } from "@/lib/cache/query-persister";
import { useHydrateSyncIndex } from "@/stores/sync-index-store";

export function Providers({ children }: { children: React.ReactNode }) {
  const [queryClient] = useState(() => {
    // The persister is handed `(queryFn, context, query)` and never a QueryClient, but it has
    // to publish a background-revalidated payload back into THIS client or the layer renders a
    // fetch behind for good. So the client is closed over here, where it is created, rather
    // than taken from the `Query` (a query-core internal) or from a module global (hidden
    // state that binds to whichever client registered last). The accessor is only ever called
    // long after this initializer has returned. See src/lib/cache/AGENTS.md "publishing a
    // revalidated value".
    let client: QueryClient | null = null;
    const created = new QueryClient({
      defaultOptions: {
        queries: {
          staleTime: 60 * 1000,
          refetchOnWindowFocus: false,
          // Allowlisted geospatial layer reads only; see src/lib/cache/AGENTS.md.
          persister: createIndexedDbLayerQueryPersister(() => client),
        },
      },
    });
    client = created;
    return created;
  });

  // One cursor walk of the query cache per app mount, so every layer's "synced days" track can
  // be answered from memory instead of scanning IndexedDB per render.
  useHydrateSyncIndex();

  const [trpcClient] = useState(() => trpc.createClient({ links: trpcLinks() }));

  return (
    <SessionProvider>
      <trpc.Provider client={trpcClient} queryClient={queryClient}>
        <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
      </trpc.Provider>
    </SessionProvider>
  );
}
