import { create } from "zustand";
import { devtools } from "zustand/middleware";

export type ConnectionState = "connecting" | "open" | "closed";

interface RealtimeStoreState {
  connections: Map<string, ConnectionState>;
  activeStreams: string[];

  addConnection: (id: string, state: ConnectionState) => void;
  updateConnection: (id: string, state: ConnectionState) => void;
  removeConnection: (id: string) => void;
}

export const useRealtimeStore = create<RealtimeStoreState>()(
  devtools((set) => ({
    connections: new Map(),
    activeStreams: [],

    addConnection: (id, state) =>
      set((s) => {
        const next = new Map(s.connections);
        next.set(id, state);
        return {
          connections: next,
          activeStreams: next.has(id)
            ? s.activeStreams
            : [...s.activeStreams, id],
        };
      }),

    updateConnection: (id, state) =>
      set((s) => {
        if (!s.connections.has(id)) return s;
        const next = new Map(s.connections);
        next.set(id, state);
        return { connections: next };
      }),

    removeConnection: (id) =>
      set((s) => {
        const next = new Map(s.connections);
        next.delete(id);
        return {
          connections: next,
          activeStreams: s.activeStreams.filter((sid) => sid !== id),
        };
      }),
  }))
);
