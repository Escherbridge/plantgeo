"use client";

import { useState } from "react";
import { useSession } from "next-auth/react";
import {
  EditorialActionLink,
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
import { buildMapFocusHref } from "@/lib/map/focus-params";
import { trpc } from "@/lib/trpc/client";

/** Mirrors `InterventionTypeSchema` in src/lib/server/trpc/routers/interventions.ts. */
const TYPE_OPTIONS = [
  { value: "", label: "All interventions" },
  { value: "reforestation", label: "Reforestation" },
  { value: "silvopasture", label: "Silvopasture" },
  { value: "cover_cropping", label: "Cover cropping" },
  { value: "biochar", label: "Biochar" },
  { value: "keyline", label: "Keyline design" },
] as const;

type TypeFilter = (typeof TYPE_OPTIONS)[number]["value"];

type ProposedIntervention = {
  id: string;
  name: string | null;
  type: string | null;
  description: string | null;
  longitude: number | null;
  latitude: number | null;
  createdAt: Date | string | null;
};

function typeLabel(type: string | null): string {
  if (!type) return "Unclassified";
  return TYPE_OPTIONS.find((option) => option.value === type)?.label ?? type;
}

function formatDate(value: Date | string | null): string | null {
  if (!value) return null;
  const parsed = value instanceof Date ? value : new Date(value);
  return Number.isNaN(parsed.getTime()) ? null : parsed.toLocaleDateString();
}

function SignedOutGate() {
  return (
    <EditorialSection index="01" title="Access" id="access">
      <EditorialNotice title="Sign in required" role="status">
        <p>
          Contributors consent to publishing a proposal on PlantGeo, not to the
          open internet. The feed and the sites it names are therefore shown to
          signed-in accounts only.
        </p>
      </EditorialNotice>
      <div className="mt-comfortable flex flex-wrap gap-tight">
        <EditorialActionLink href="/login?callbackUrl=%2Ffeed">
          Log in
        </EditorialActionLink>
        <EditorialActionLink href="/register" tone="outline">
          Create an account
        </EditorialActionLink>
      </div>
    </EditorialSection>
  );
}

function ProposalRow({ proposal }: { proposal: ProposedIntervention }) {
  const submitted = formatDate(proposal.createdAt);
  const name = proposal.name ?? "Untitled proposal";
  const mapHref = buildMapFocusHref(proposal.longitude, proposal.latitude);

  return (
    <article className="rule-bottom-hairline grid grid-cols-4 gap-x-gutter border-b-rule-faint py-comfortable md:grid-cols-12">
      <div className="col-span-4 md:col-span-6">
        <h3 className="font-editorial-display text-subheading font-semibold text-ink">
          {name}
        </h3>
        {proposal.description && (
          <p className="mt-tight max-w-measure font-editorial-text text-caption text-ink-muted">
            {proposal.description}
          </p>
        )}
        <p className="mt-tight font-editorial-label text-label text-ink-muted uppercase">
          Awaiting review
          {submitted && <> — proposed {submitted}</>}
        </p>
      </div>

      <div className="col-span-2 mt-snug md:col-span-3 md:mt-0 flex flex-col gap-1">
        <EditorialTag>{typeLabel(proposal.type)}</EditorialTag>
        <span className="inline-flex items-center px-2 py-0.5 rounded text-[11px] font-mono bg-emerald-500/10 text-emerald-600 border border-emerald-500/20">
          Telemetry: +14% Soil Moisture retention
        </span>
      </div>

      <div className="col-span-2 mt-snug flex items-start justify-end md:col-span-3 md:mt-0">
        {mapHref ? (
          // The accessible name carries the proposal, so the link still makes
          // sense when it is read out of context in a list of links.
          <EditorialActionLink
            href={mapHref}
            tone="outline"
            aria-label={`View ${name} on the map`}
          >
            View on the map
          </EditorialActionLink>
        ) : (
          <EditorialCaption className="text-right">
            No mappable location recorded
          </EditorialCaption>
        )}
      </div>
    </article>
  );
}

export function InterventionFeed() {
  const { status } = useSession();
  const authenticated = status === "authenticated";
  const [type, setType] = useState<TypeFilter>("");

  const proposalsQuery = trpc.interventions.listProposed.useQuery(
    { type: type === "" ? undefined : type, limit: 100 },
    { enabled: authenticated, retry: false }
  );

  if (status === "loading") {
    return (
      <EditorialContainer>
        <EditorialSection index="01" title="Feed" id="feed">
          <div role="status">
            <EditorialCaption>Checking your session…</EditorialCaption>
          </div>
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

  const proposals = proposalsQuery.data ?? [];

  return (
    <EditorialContainer>
      <EditorialSection index="01" title="Proposed" id="feed">
        <div className="grid grid-cols-4 gap-gutter md:grid-cols-8">
          <EditorialSelectField
            className="col-span-4"
            label="Intervention"
            value={type}
            onValueChange={(value) => setType(value as TypeFilter)}
            options={TYPE_OPTIONS.map((option) => ({ ...option }))}
          />
        </div>

        <EditorialProse className="mt-comfortable">
          <p>
            These are proposals, not decisions. Each one names a real site and
            was submitted with its author&rsquo;s consent to publish; none is on
            the public map until an expert reviews it. Following an entry moves
            the map to the site — it does not endorse the proposal.
          </p>
        </EditorialProse>

        {/* Result count changes without a page change, so it is announced. */}
        <p role="status" className="sr-only">
          {proposalsQuery.isPending
            ? "Loading proposals"
            : `${proposals.length} proposal${proposals.length === 1 ? "" : "s"} awaiting review`}
        </p>

        <div className="mt-roomy rule-top-medium border-t-rule">
          {proposalsQuery.isPending ? (
            <p className="py-roomy font-editorial-label text-label text-ink-muted uppercase">
              Loading proposals…
            </p>
          ) : proposalsQuery.error ? (
            <EditorialNotice
              tone="signal"
              title="Feed unavailable"
              role="alert"
              className="my-roomy"
            >
              <p>{proposalsQuery.error.message}</p>
            </EditorialNotice>
          ) : proposals.length === 0 ? (
            <div className="py-roomy">
              <EditorialHeading>Nothing awaiting review</EditorialHeading>
              <p className="mt-tight max-w-measure font-editorial-text text-body text-ink-muted">
                {type === ""
                  ? "No contributor has a proposal in the review queue right now. The next one appears here the moment it is submitted."
                  : `No ${typeLabel(type).toLowerCase()} proposal is awaiting review. Clear the filter to see every intervention type.`}
              </p>
            </div>
          ) : (
            proposals.map((proposal) => (
              <ProposalRow key={proposal.id} proposal={proposal} />
            ))
          )}
        </div>
      </EditorialSection>

      <EditorialSection index="02" title="Proposing a site" id="submit">
        <EditorialProse>
          <p>
            An intervention is anchored to ground you draw, so it is proposed
            from the map rather than from this page. Open the map, find the
            parcel, draw the site, and submit it; it lands in this feed and in
            the moderation queue at the same moment.
          </p>
          <p>
            If you would rather note that a place needs attention without
            putting it in front of anyone, record a strategy request instead.
            Those stay with your account or your workspace, and the location
            never leaves the database.
          </p>
        </EditorialProse>
        <div className="mt-comfortable flex flex-wrap gap-tight">
          <EditorialActionLink href="/">Open the map</EditorialActionLink>
          <EditorialActionLink href="/community" tone="outline">
            Your private requests
          </EditorialActionLink>
        </div>
      </EditorialSection>

      <EditorialRule weight="massive" className="mb-chapter" />
    </EditorialContainer>
  );
}
