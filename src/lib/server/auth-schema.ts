import {
  accounts,
  sessions,
  users,
  verificationTokens,
} from "@/lib/server/db/schema";

export const authAdapterTables = {
  usersTable: users,
  accountsTable: accounts,
  sessionsTable: sessions,
  verificationTokensTable: verificationTokens,
};
