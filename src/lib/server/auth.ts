import { getServerSession as nextAuthGetServerSession } from "next-auth";
import { authOptions } from "@/lib/server/auth-options";

export async function getServerSession() {
  return nextAuthGetServerSession(authOptions);
}

export { hashPassword, verifyPassword } from "@/lib/server/password";
