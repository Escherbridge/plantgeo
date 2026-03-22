import { getServerSession as nextAuthGetServerSession } from "next-auth";
import bcrypt from "bcryptjs";

export async function getServerSession() {
  return nextAuthGetServerSession();
}

export async function hashPassword(plain: string): Promise<string> {
  return bcrypt.hash(plain, 12);
}

export async function verifyPassword(plain: string, hash: string): Promise<boolean> {
  return bcrypt.compare(plain, hash);
}
