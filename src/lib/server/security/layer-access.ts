import { and, eq, or, sql, type SQL } from "drizzle-orm";
import { features, layers, teamMembers } from "@/lib/server/db/schema";
import {
  isPlatformAdmin,
  type AuthIdentity,
} from "@/lib/server/security/access-control";

function teamAccessCondition(identity: AuthIdentity): SQL | undefined {
  const conditions: SQL[] = [];
  if (identity.teamId) {
    conditions.push(eq(layers.teamId, identity.teamId));
  } else if (identity.userId) {
    conditions.push(sql<boolean>`EXISTS (
      SELECT 1
      FROM ${teamMembers}
      WHERE ${teamMembers.teamId} = ${layers.teamId}
        AND ${teamMembers.userId} = ${identity.userId}
    )`);
  }
  return conditions.length > 0 ? or(...conditions) : undefined;
}

/** Limits layer metadata to public layers or teams visible to the identity. */
export function layerVisibilityCondition(identity: AuthIdentity): SQL {
  if (isPlatformAdmin(identity)) return sql<boolean>`true`;
  const teamAccess = teamAccessCondition(identity);
  return teamAccess
    ? (or(eq(layers.isPublic, true), teamAccess) as SQL)
    : eq(layers.isPublic, true);
}

/** Public callers see only published features; team identities see team drafts. */
export function featureVisibilityCondition(identity: AuthIdentity): SQL {
  if (isPlatformAdmin(identity)) return sql<boolean>`true`;
  const publicPublished = and(
    eq(layers.isPublic, true),
    eq(features.status, "published")
  );
  const teamAccess = teamAccessCondition(identity);
  return teamAccess
    ? (or(publicPublished, teamAccess) as SQL)
    : (publicPublished as SQL);
}
