import type { DefaultSession } from "next-auth";

/** Membership role inside the active organization; null when the user has none. */
export type ActiveTeamRole = "owner" | "member" | "viewer";

declare module "next-auth" {
  interface Session {
    user: {
      id: string;
      platformRole: string;
      activeTeamId: string | null;
      activeTeamRole: ActiveTeamRole | null;
    } & DefaultSession["user"];
  }

  interface User {
    platformRole?: string | null;
  }
}

declare module "next-auth/jwt" {
  interface JWT {
    id: string;
    platformRole: string;
    activeTeamId: string | null;
    activeTeamRole: ActiveTeamRole | null;
  }
}
