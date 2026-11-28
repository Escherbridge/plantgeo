import { randomBytes } from "crypto";
import { and, asc, count, eq } from "drizzle-orm";
import {
  affiliateApplicationStatus,
  type AffiliateApplicationListItem,
} from "@/lib/affiliate/application";
import {
  affiliateInviteUrl,
  buildAffiliateCode,
  inviteLineForAffiliate,
} from "@/lib/affiliate/invite-copy";
import { normalizeRefCode } from "@/lib/plans/ref-code";
import { affiliateApplications, affiliates, sitePlans, users } from "@/lib/server/db/schema";
import { isUniqueConstraintViolation, normalizeAuthEmail } from "@/lib/server/security/registration";
import type { AppDatabase } from "@/lib/server/affiliate/database";

export type AffiliateReviewReason =
  | "not_found"
  | "not_pending"
  | "email_has_code"
  | "code_unavailable";

export class AffiliateReviewError extends Error {
  readonly reason: AffiliateReviewReason;

  constructor(reason: AffiliateReviewReason) {
    super(reason);
    this.name = "AffiliateReviewError";
    this.reason = reason;
  }
}

export type ActiveAffiliate = {
  code: string;
  name: string;
  organization: string | null;
  status: string;
};

const CODE_ATTEMPTS = 5;

function randomAffiliateSuffix(): string {
  return randomBytes(3).toString("hex");
}

function constraintName(error: unknown): string | null {
  if (typeof error !== "object" || error === null) return null;
  if ("constraint_name" in error && typeof error.constraint_name === "string") {
    return error.constraint_name;
  }
  if ("constraint" in error && typeof error.constraint === "string") {
    return error.constraint;
  }
  return null;
}

function errorChain(error: unknown): unknown[] {
  const chain: unknown[] = [];
  let current: unknown = error;
  for (let depth = 0; depth < 5 && typeof current === "object" && current !== null; depth += 1) {
    chain.push(current);
    current = "cause" in current ? (current as { cause: unknown }).cause : null;
  }
  return chain;
}

function uniqueViolation(error: unknown): { constraint: string | null } | null {
  for (const candidate of errorChain(error)) {
    if (!isUniqueConstraintViolation(candidate)) continue;
    return { constraint: constraintName(candidate) };
  }
  return null;
}

async function insertIssuedCode(
  database: AppDatabase,
  values: {
    applicationId: string;
    email: string;
    name: string;
    organization: string | null;
    approvedBy: string;
  },
  suffix: () => string
): Promise<string> {
  for (let attempt = 0; attempt < CODE_ATTEMPTS; attempt += 1) {
    const code = buildAffiliateCode(values.name, values.organization, suffix());
    try {
      // Savepoint: a unique violation aborts a Postgres transaction, so the
      // retry needs the failed insert rolled back on its own.
      await database.transaction(async (savepoint) => {
        await savepoint.insert(affiliates).values({
          applicationId: values.applicationId,
          email: values.email,
          name: values.name,
          organization: values.organization,
          code,
          status: "active",
          approvedBy: values.approvedBy,
        });
      });
      return code;
    } catch (error) {
      const violation = uniqueViolation(error);
      if (!violation) throw error;
      if (violation.constraint === "affiliates_code_unique" && attempt < CODE_ATTEMPTS - 1) {
        continue;
      }
      if (violation.constraint === "affiliates_code_unique") {
        throw new AffiliateReviewError("code_unavailable");
      }
      throw new AffiliateReviewError("email_has_code");
    }
  }
  throw new AffiliateReviewError("code_unavailable");
}

export async function listAffiliateApplications(
  database: AppDatabase
): Promise<AffiliateApplicationListItem[]> {
  const rows = await database
    .select({
      id: affiliateApplications.id,
      name: affiliateApplications.name,
      email: affiliateApplications.email,
      organization: affiliateApplications.organization,
      role: affiliateApplications.role,
      website: affiliateApplications.website,
      message: affiliateApplications.message,
      status: affiliateApplications.status,
      code: affiliates.code,
    })
    .from(affiliateApplications)
    .leftJoin(affiliates, eq(affiliates.applicationId, affiliateApplications.id))
    .orderBy(asc(affiliateApplications.createdAt));

  const listed: AffiliateApplicationListItem[] = [];
  for (const row of rows) {
    const status = affiliateApplicationStatus(row.status);
    if (!status) continue;
    listed.push({
      id: row.id,
      name: row.name,
      email: row.email,
      organization: row.organization,
      role: row.role,
      website: row.website,
      message: row.message,
      status,
      code: row.code,
    });
  }
  return listed;
}

export async function approveAffiliateApplication(
  database: AppDatabase,
  input: { applicationId: string; reviewerId: string; suffix?: () => string }
): Promise<{ code: string }> {
  const suffix = input.suffix ?? randomAffiliateSuffix;
  return database.transaction(async (tx) => {
    const [application] = await tx
      .select({
        id: affiliateApplications.id,
        name: affiliateApplications.name,
        email: affiliateApplications.email,
        organization: affiliateApplications.organization,
        status: affiliateApplications.status,
      })
      .from(affiliateApplications)
      .where(eq(affiliateApplications.id, input.applicationId))
      .limit(1);
    if (!application) throw new AffiliateReviewError("not_found");
    if (application.status !== "pending") throw new AffiliateReviewError("not_pending");

    const email = normalizeAuthEmail(application.email);
    const [existing] = await tx
      .select({ id: affiliates.id })
      .from(affiliates)
      .where(and(eq(affiliates.email, email), eq(affiliates.status, "active")))
      .limit(1);
    if (existing) throw new AffiliateReviewError("email_has_code");

    const [updated] = await tx
      .update(affiliateApplications)
      .set({ status: "approved" })
      .where(
        and(eq(affiliateApplications.id, application.id), eq(affiliateApplications.status, "pending"))
      )
      .returning({ id: affiliateApplications.id });
    if (!updated) throw new AffiliateReviewError("not_pending");

    const code = await insertIssuedCode(
      tx,
      {
        applicationId: application.id,
        email,
        name: application.name,
        organization: application.organization,
        approvedBy: input.reviewerId,
      },
      suffix
    );
    return { code };
  });
}

export async function declineAffiliateApplication(
  database: AppDatabase,
  applicationId: string
): Promise<void> {
  const [application] = await database
    .select({ id: affiliateApplications.id, status: affiliateApplications.status })
    .from(affiliateApplications)
    .where(eq(affiliateApplications.id, applicationId))
    .limit(1);
  if (!application) throw new AffiliateReviewError("not_found");
  if (application.status !== "pending") throw new AffiliateReviewError("not_pending");

  const [updated] = await database
    .update(affiliateApplications)
    .set({ status: "declined" })
    .where(
      and(eq(affiliateApplications.id, application.id), eq(affiliateApplications.status, "pending"))
    )
    .returning({ id: affiliateApplications.id });
  if (!updated) throw new AffiliateReviewError("not_pending");
}

export async function findActiveAffiliateByCode(
  database: AppDatabase,
  code: string
): Promise<ActiveAffiliate | null> {
  const [row] = await database
    .select({
      code: affiliates.code,
      name: affiliates.name,
      organization: affiliates.organization,
      status: affiliates.status,
    })
    .from(affiliates)
    .where(and(eq(affiliates.code, code), eq(affiliates.status, "active")))
    .limit(1);
  if (!row || row.status !== "active" || row.code !== code) return null;
  return row;
}

/** Keeps a ref only when it is an active affiliate code. Anything else is dropped. */
export async function activeRefCode(
  database: AppDatabase,
  requested: string | null
): Promise<string | null> {
  if (!requested) return null;
  const affiliate = await findActiveAffiliateByCode(database, requested);
  return affiliate?.code ?? null;
}

export type PlanInviteResolution = {
  line: string | null;
  refCode: string | null;
  dropStoredCode: boolean;
};

/**
 * `/plan/new?ref=` shows a line only for an active code with a recorded name
 * or organization. An unknown or inactive code shows nothing and is dropped.
 */
export async function resolvePlanInvite(
  database: AppDatabase,
  rawRef: unknown
): Promise<PlanInviteResolution> {
  const submitted = typeof rawRef === "string" && rawRef.trim().length > 0;
  const code = normalizeRefCode(rawRef);
  if (!code) {
    return { line: null, refCode: null, dropStoredCode: submitted };
  }
  const affiliate = await findActiveAffiliateByCode(database, code);
  if (!affiliate) {
    return { line: null, refCode: null, dropStoredCode: true };
  }
  return {
    line: inviteLineForAffiliate(affiliate),
    refCode: affiliate.code,
    dropStoredCode: false,
  };
}

export type AffiliateDashboard = {
  inviteUrl: string;
  plansStarted: number;
};

export async function loadAffiliateDashboard(
  database: AppDatabase,
  userId: string
): Promise<AffiliateDashboard | null> {
  const [user] = await database
    .select({ email: users.email, emailVerified: users.emailVerified })
    .from(users)
    .where(eq(users.id, userId))
    .limit(1);
  if (!user || typeof user.email !== "string" || !user.emailVerified) return null;

  const email = normalizeAuthEmail(user.email);
  if (email.length === 0) return null;

  const [affiliate] = await database
    .select({ code: affiliates.code, status: affiliates.status })
    .from(affiliates)
    .where(and(eq(affiliates.email, email), eq(affiliates.status, "active")))
    .limit(1);
  if (!affiliate || affiliate.status !== "active") return null;

  const [row] = await database
    .select({ plansStarted: count() })
    .from(sitePlans)
    .where(eq(sitePlans.refCode, affiliate.code));
  const plansStarted = Number(row?.plansStarted ?? 0);

  return {
    inviteUrl: affiliateInviteUrl(affiliate.code),
    plansStarted: Number.isFinite(plansStarted) ? plansStarted : 0,
  };
}
