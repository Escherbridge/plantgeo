import { create } from "zustand";
import { devtools } from "zustand/middleware";

interface AuthStoreState {
  activeTeamId: string | null;
  setActiveTeam: (teamId: string | null) => void;
}

export const useAuthStore = create<AuthStoreState>()(
  devtools(
    (set) => ({
      activeTeamId: null,
      setActiveTeam: (teamId) => set({ activeTeamId: teamId }),
    }),
    { name: "auth-store" }
  )
);
