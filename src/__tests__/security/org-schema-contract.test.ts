import { describe, expect, it } from "vitest";
import { getTableColumns, getTableName } from "drizzle-orm";
import { getTableConfig } from "drizzle-orm/pg-core";
import {
  emailVerificationTokens,
  passwordResetTokens,
  teamInvitations,
  teamJoinLinks,
  users,
} from "@/lib/server/db/schema";

describe("organization credential and invitation schema contract", () => {
  it("exports the four new tables with the expected DB names", () => {
    expect(getTableName(emailVerificationTokens)).toBe(
      "email_verification_tokens"
    );
    expect(getTableName(passwordResetTokens)).toBe("password_reset_tokens");
    expect(getTableName(teamInvitations)).toBe("team_invitations");
    expect(getTableName(teamJoinLinks)).toBe("team_join_links");
  });

  it("maps emailVerificationTokens columns to expected snake_case names", () => {
    const columns = getTableColumns(emailVerificationTokens);
    expect(columns.id.name).toBe("id");
    expect(columns.userId.name).toBe("user_id");
    expect(columns.tokenHash.name).toBe("token_hash");
    expect(columns.expiresAt.name).toBe("expires_at");
    expect(columns.usedAt.name).toBe("used_at");
    expect(columns.createdAt.name).toBe("created_at");
  });

  it("maps passwordResetTokens columns to expected snake_case names", () => {
    const columns = getTableColumns(passwordResetTokens);
    expect(columns.id.name).toBe("id");
    expect(columns.userId.name).toBe("user_id");
    expect(columns.tokenHash.name).toBe("token_hash");
    expect(columns.expiresAt.name).toBe("expires_at");
    expect(columns.usedAt.name).toBe("used_at");
    expect(columns.createdAt.name).toBe("created_at");
  });

  it("maps teamInvitations columns to expected snake_case names", () => {
    const columns = getTableColumns(teamInvitations);
    expect(columns.id.name).toBe("id");
    expect(columns.teamId.name).toBe("team_id");
    expect(columns.email.name).toBe("email");
    expect(columns.teamRole.name).toBe("team_role");
    expect(columns.tokenHash.name).toBe("token_hash");
    expect(columns.invitedBy.name).toBe("invited_by");
    expect(columns.expiresAt.name).toBe("expires_at");
    expect(columns.acceptedAt.name).toBe("accepted_at");
    expect(columns.acceptedBy.name).toBe("accepted_by");
    expect(columns.revokedAt.name).toBe("revoked_at");
    expect(columns.createdAt.name).toBe("created_at");
  });

  it("maps teamJoinLinks columns to expected snake_case names", () => {
    const columns = getTableColumns(teamJoinLinks);
    expect(columns.id.name).toBe("id");
    expect(columns.teamId.name).toBe("team_id");
    expect(columns.codeHash.name).toBe("code_hash");
    expect(columns.teamRole.name).toBe("team_role");
    expect(columns.allowedEmailDomain.name).toBe("allowed_email_domain");
    expect(columns.maxUses.name).toBe("max_uses");
    expect(columns.useCount.name).toBe("use_count");
    expect(columns.expiresAt.name).toBe("expires_at");
    expect(columns.revokedAt.name).toBe("revoked_at");
    expect(columns.createdBy.name).toBe("created_by");
    expect(columns.createdAt.name).toBe("created_at");
  });

  it("cascades teamInvitations and teamJoinLinks on team delete", () => {
    const invitationTeamFk = getTableConfig(teamInvitations).foreignKeys.find(
      (fk) => fk.reference().columns.some((c) => c.name === "team_id")
    );
    expect(invitationTeamFk?.onDelete).toBe("cascade");
    expect(getTableName(invitationTeamFk!.reference().foreignTable)).toBe(
      "teams"
    );

    const joinLinkTeamFk = getTableConfig(teamJoinLinks).foreignKeys.find(
      (fk) => fk.reference().columns.some((c) => c.name === "team_id")
    );
    expect(joinLinkTeamFk?.onDelete).toBe("cascade");
    expect(getTableName(joinLinkTeamFk!.reference().foreignTable)).toBe(
      "teams"
    );
  });

  it("adds a nullable activeTeamId column to users", () => {
    const columns = getTableColumns(users);
    expect(columns.activeTeamId).toBeDefined();
    expect(columns.activeTeamId.name).toBe("active_team_id");
    expect(columns.activeTeamId.notNull).toBe(false);
  });
});
