"use client";

import { useMemo, useState } from "react";
import { useSession } from "next-auth/react";
import { trpc } from "@/lib/trpc/client";
import {
  EditorialActionLink,
  EditorialButton,
  EditorialCaption,
  EditorialContainer,
  EditorialHeading,
  EditorialNotice,
  EditorialProse,
  EditorialRule,
  EditorialSection,
  EditorialTag,
} from "@/components/ui/editorial";
import { EditorialSelectField } from "@/components/ui/editorial/fields";

/** Mirrors the enum in src/lib/server/trpc/routers/community.ts. */
const STRATEGY_OPTIONS = [
  { value: "", label: "All strategies" },
  { value: "keyline", label: "Keyline design" },
  { value: "silvopasture", label: "Silvopasture" },
  { value: "reforestation", label: "Reforestation" },
  { value: "biochar", label: "Biochar" },
  { value: "water_harvesting", label: "Water harvesting" },
  { value: "cover_cropping", label: "Cover cropping" },
] as const;

type StrategyFilter = (typeof STRATEGY_OPTIONS)[number]["value"];

const PRIVATE_SCOPE = "";

function strategyLabel(strategyType: string): string {
  return (
    STRATEGY_OPTIONS.find((option) => option.value === strategyType)?.label ??
    strategyType
  );
}

function formatDate(value: Date | string | null): string | null {
  if (!value) return null;
  const parsed = value instanceof Date ? value : new Date(value);
  return Number.isNaN(parsed.getTime()) ? null : parsed.toLocaleDateString();
}

function LedgerControls({
  scope,
  onScopeChange,
  scopeOptions,
  strategy,
  onStrategyChange,
}: {
  scope: string;
  onScopeChange: (value: string) => void;
  scopeOptions: { value: string; label: string }[];
  strategy: StrategyFilter;
  onStrategyChange: (value: StrategyFilter) => void;
}) {
  return (
    <div className="grid grid-cols-4 gap-gutter md:grid-cols-8">
      <EditorialSelectField
        className="col-span-4"
        label="Scope"
        value={scope}
        onValueChange={onScopeChange}
        options={scopeOptions}
      />
      <EditorialSelectField
        className="col-span-4"
        label="Strategy"
        value={strategy}
        onValueChange={(value) => onStrategyChange(value as StrategyFilter)}
        options={STRATEGY_OPTIONS.map((option) => ({ ...option }))}
      />
    </div>
  );
}

function SignedOutGate() {
  return (
    <EditorialSection index="01" title="Access" id="access">
      <EditorialNotice title="Sign in required" role="status">
        <p>
          The ledger holds strategy requests that are private to an account or to
          a partner workspace. There is no public feed to show you, so this page
          shows nothing until it knows who you are.
        </p>
      </EditorialNotice>
      <div className="mt-comfortable flex flex-wrap gap-tight">
        <EditorialActionLink href="/login?callbackUrl=%2Fcommunity">
          Log in
        </EditorialActionLink>
        <EditorialActionLink href="/register" tone="outline">
          Create an account
        </EditorialActionLink>
      </div>
    </EditorialSection>
  );
}

function RequestRow({
  request,
  canVote,
  onVote,
  voting,
}: {
  request: {
    id: string;
    title: string;
    description: string | null;
    strategyType: string;
    voteCount: number | null;
    createdAt: Date | string | null;
  };
  canVote: boolean;
  onVote: (requestId: string) => void;
  voting: boolean;
}) {
  const submitted = formatDate(request.createdAt);

  return (
    <article className="rule-bottom-hairline grid grid-cols-4 gap-x-gutter border-b-rule-faint py-comfortable md:grid-cols-12">
      <div className="col-span-4 md:col-span-6">
        <h3 className="font-editorial-display text-subheading font-semibold text-ink">
          {request.title}
        </h3>
        {request.description && (
          <p className="mt-tight max-w-measure font-editorial-text text-caption text-ink-muted">
            {request.description}
          </p>
        )}
        <p className="mt-tight font-editorial-label text-label text-ink-muted uppercase">
          Location held privately
          {submitted && <> — submitted {submitted}</>}
        </p>
      </div>

      <div className="col-span-2 mt-snug md:col-span-3 md:mt-0">
        <EditorialTag>{strategyLabel(request.strategyType)}</EditorialTag>
      </div>

      <div className="col-span-2 mt-snug flex items-start justify-end gap-snug md:col-span-3 md:mt-0">
        <div className="text-right">
          <p className="font-editorial-display text-heading font-bold text-ink">
            {request.voteCount ?? 0}
          </p>
          <EditorialCaption>
            {request.voteCount === 1 ? "vote" : "votes"}
          </EditorialCaption>
        </div>
        {canVote && (
          <EditorialButton
            tone="outline"
            disabled={voting}
            onClick={() => onVote(request.id)}
          >
            {voting ? "Voting" : "Vote"}
          </EditorialButton>
        )}
      </div>
    </article>
  );
}

export function CommunityLedger() {
  const { status } = useSession();
  const authenticated = status === "authenticated";

  const [scope, setScope] = useState<string>(PRIVATE_SCOPE);
  const [strategy, setStrategy] = useState<StrategyFilter>("");
  const [voteError, setVoteError] = useState<string | null>(null);

  const teamsQuery = trpc.teams.listMyTeams.useQuery(undefined, {
    enabled: authenticated,
    retry: false,
  });

  const requestsQuery = trpc.community.getRequests.useQuery(
    {
      teamId: scope === PRIVATE_SCOPE ? undefined : scope,
      strategyType: strategy === "" ? undefined : strategy,
      limit: 100,
    },
    { enabled: authenticated, retry: false }
  );

  const priorityZonesQuery = trpc.community.getPriorityZones.useQuery(
    {},
    { enabled: authenticated, retry: false }
  );

  const voteMutation = trpc.community.voteOnRequest.useMutation({
    onSuccess: () => {
      setVoteError(null);
      requestsQuery.refetch();
    },
    onError: (error) => setVoteError(error.message),
  });

  const scopeOptions = useMemo(() => {
    const memberships = teamsQuery.data ?? [];
    return [
      { value: PRIVATE_SCOPE, label: "Private to you" },
      ...memberships.map(({ team }) => ({
        value: team.id,
        label: team.name,
      })),
    ];
  }, [teamsQuery.data]);

  if (status === "loading") {
    return (
      <EditorialContainer>
        <EditorialSection index="01" title="Ledger" id="ledger">
          <EditorialCaption>Checking your session…</EditorialCaption>
        </EditorialSection>
      </EditorialContainer>
    );
  }

  if (!authenticated) {
    return (
      <EditorialContainer>
        <SignedOutGate />
      </EditorialContainer>
    );
  }

  const requests = requestsQuery.data ?? [];
  const teamScoped = scope !== PRIVATE_SCOPE;

  return (
    <EditorialContainer>
      <EditorialSection index="01" title="The ledger" id="ledger">
        <LedgerControls
          scope={scope}
          onScopeChange={(value) => {
            setScope(value);
            setVoteError(null);
          }}
          scopeOptions={scopeOptions}
          strategy={strategy}
          onStrategyChange={setStrategy}
        />

        <EditorialProse className="mt-comfortable">
          <p>
            {teamScoped
              ? "These requests are visible to authenticated members of the selected workspace and to nobody else. Coordinates stay in the database; the ledger shows that a place exists, never where it is."
              : "These requests are private to your account. Nothing here is published, shared with a workspace, or turned into a public waypoint."}
          </p>
        </EditorialProse>

        {voteError && (
          <EditorialNotice
            tone="signal"
            title="Vote not recorded"
            role="alert"
            className="mt-comfortable"
          >
            <p>{voteError}</p>
          </EditorialNotice>
        )}

        <div className="mt-roomy rule-top-medium border-t-rule">
          {requestsQuery.isPending ? (
            <p className="py-roomy font-editorial-label text-label text-ink-muted uppercase">
              Loading requests…
            </p>
          ) : requestsQuery.error ? (
            <EditorialNotice
              tone="signal"
              title="Ledger unavailable"
              role="alert"
              className="my-roomy"
            >
              <p>{requestsQuery.error.message}</p>
            </EditorialNotice>
          ) : requests.length === 0 ? (
            <div className="py-roomy">
              <EditorialHeading>Nothing recorded yet</EditorialHeading>
              <p className="mt-tight max-w-measure font-editorial-text text-body text-ink-muted">
                {teamScoped
                  ? "This workspace has not submitted a strategy request. The first one will appear here the moment it lands."
                  : "You have not submitted a private strategy request yet."}
              </p>
            </div>
          ) : (
            requests.map((request) => (
              <RequestRow
                key={request.id}
                request={request}
                canVote={teamScoped}
                voting={
                  voteMutation.isPending &&
                  voteMutation.variables?.requestId === request.id
                }
                onVote={(requestId) => voteMutation.mutate({ requestId })}
              />
            ))
          )}
        </div>

        {!teamScoped && requests.length > 0 && (
          <EditorialCaption className="mt-comfortable">
            Voting applies to workspace requests only. Switch scope to a
            workspace to vote.
          </EditorialCaption>
        )}
      </EditorialSection>

      <EditorialSection
        index="02"
        title="Opportunity waypoints"
        id="opportunity-waypoints"
      >
        {priorityZonesQuery.isPending ? (
          <EditorialCaption>Checking availability…</EditorialCaption>
        ) : priorityZonesQuery.error ? (
          <EditorialNotice tone="signal" title="Not available" role="status">
            <p>{priorityZonesQuery.error.message}</p>
            <p className="mt-comfortable text-caption text-ink-muted">
              This section is deliberately empty. Publishing aggregate priority
              zones would leak the locations the ledger exists to protect, so it
              stays dark until a reviewed, access-controlled warehouse
              publication is in place.
            </p>
          </EditorialNotice>
        ) : (
          <EditorialNotice title="No zones published" role="status">
            <p>
              The warehouse returned no priority zones for the current
              selection.
            </p>
          </EditorialNotice>
        )}
      </EditorialSection>

      <EditorialSection index="03" title="Adding a request" id="submit">
        <EditorialProse>
          <p>
            A strategy request is anchored to a point on the ground, so it is
            created from the map rather than from this page. Open the map, drop a
            pin on the parcel you have in mind, and submit from the community
            panel. The location is stored against your account or your workspace
            and is never made public by the act of submitting it.
          </p>
        </EditorialProse>
        <div className="mt-comfortable flex flex-wrap gap-tight">
          <EditorialActionLink href="/">Open the map</EditorialActionLink>
          <EditorialActionLink href="/about#principles" tone="outline">
            Why this stays private
          </EditorialActionLink>
        </div>
      </EditorialSection>

      <EditorialRule weight="massive" className="mb-chapter" />
    </EditorialContainer>
  );
}
