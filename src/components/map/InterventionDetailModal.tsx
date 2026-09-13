"use client";

import { useMemo, type ReactNode } from "react";
import { trpc } from "@/lib/trpc/client";
import { InterventionCommentThread } from "@/components/intervention/InterventionCommentThread";
import { InterventionLikeButton } from "@/components/intervention/InterventionLikeButton";
import {
  contributorFallbackLabel,
  useDisplayNames,
} from "@/components/intervention/use-display-names";
import type { InterventionDetailRecord } from "@/lib/map/intervention-detail";
import {
  INTERVENTION_CATEGORY_CLASSES,
  INTERVENTION_PENDING_REVIEW_COLOR,
  INTERVENTION_PENDING_REVIEW_LABEL,
  INTERVENTION_REQUEST_COLOR,
  INTERVENTION_REQUEST_LABEL,
  INTERVENTION_UNCLASSIFIED_LABEL,
} from "@/lib/map/layers";
import { useInterventionDetailStore } from "@/stores/intervention-detail-store";

/**
 * The standalone intervention detail surface (track OQ-3).
 *
 * It is a MODAL, not a MapLibre popup and not a mode of
 * `AiInterventionWorkspace`: it opens as a compact card and expands to take
 * over the viewport, the way a Facebook feed post expands into a lightbox. The
 * two states are one boolean (`isExpanded` in
 * `src/stores/intervention-detail-store.ts`) and one component -- expanding
 * re-lays-out the same card rather than mounting a second one, so an in-flight
 * fetch, scroll position and (from Phase 5) a half-typed comment all survive the
 * transition.
 *
 * Placement, compact: bottom centre. The AI workspace anchors to `right-0` at
 * `min(24rem, 100vw - 19rem)` and the layer dock owns the left 19rem, so the
 * compact card is positioned to overlap NEITHER while both are open. Expanded,
 * it covers the workspace on purpose and temporarily -- that is what the
 * lightbox pattern asks for -- and collapses back to the same card.
 *
 * Neither state touches the workspace's store or its mount, which is what lets
 * the two coexist (FR-2's last acceptance criterion).
 */
export function InterventionDetailModal({ children }: { children?: ReactNode }) {
  const featureId = useInterventionDetailStore((state) => state.featureId);
  const heldRecord = useInterventionDetailStore((state) => state.record);
  const isExpanded = useInterventionDetailStore((state) => state.isExpanded);
  const setExpanded = useInterventionDetailStore((state) => state.setExpanded);
  const close = useInterventionDetailStore((state) => state.close);

  if (!featureId) return null;

  // FR-4: the social surface is keyed off the feature id the store opened on,
  // which is the resolved record's id in both branches below -- so it mounts
  // once, here, and survives the compact/expanded transition with its
  // half-typed comment intact.
  const social = (
    <div className="mt-3 space-y-3 border-t border-[hsl(var(--border))] pt-3">
      <InterventionLikeButton featureId={featureId} />
      <InterventionCommentThread featureId={featureId} />
      {children}
    </div>
  );

  const shell = {
    isExpanded,
    onToggleExpanded: () => setExpanded(!isExpanded),
    onClose: close,
  };

  // A drafts-overlay click arrived with its record and must cost no round trip
  // (NFR-1), so the fetching variant is a SEPARATE component that only mounts
  // for the published Martin-tile case. A single component holding a disabled
  // `useQuery` would still reach the tRPC client on every render of every
  // consumer -- including `LayerManager`, which mounts this modal closed.
  return heldRecord ? (
    <HeldInterventionDetail record={heldRecord} {...shell}>
      {social}
    </HeldInterventionDetail>
  ) : (
    <FetchedInterventionDetail featureId={featureId} {...shell}>
      {social}
    </FetchedInterventionDetail>
  );
}

/**
 * The submitter's display name for one record (Phase 4 / FR-4).
 *
 * Resolved in the container rather than in `InterventionDetailCard` so the card
 * stays prop-driven and renders without a tRPC client, which is how its own
 * test exercises it. Same procedure and same fallback as the comment thread.
 */
function useSubmitterLabel(
  submittedByUserId: string | null | undefined
): string | null {
  const ids = useMemo(
    () => (submittedByUserId ? [submittedByUserId] : []),
    [submittedByUserId]
  );
  const directory = useDisplayNames(ids);
  if (!submittedByUserId) return null;
  return directory.nameFor(submittedByUserId) ?? contributorFallbackLabel(submittedByUserId);
}

/** The already-resolved record a drafts-overlay click arrived with. */
function HeldInterventionDetail({
  record,
  children,
  ...shell
}: {
  record: InterventionDetailRecord;
  isExpanded: boolean;
  onToggleExpanded: () => void;
  onClose: () => void;
  children?: ReactNode;
}) {
  const submitterLabel = useSubmitterLabel(record.submittedByUserId);

  return (
    <InterventionDetailCard
      record={record}
      submitterLabel={submitterLabel}
      isLoading={false}
      isError={false}
      {...shell}
    >
      {children}
    </InterventionDetailCard>
  );
}

/** The by-id read for a feature that arrived as a tile, and nothing else. */
function FetchedInterventionDetail({
  featureId,
  children,
  ...shell
}: {
  featureId: string;
  isExpanded: boolean;
  onToggleExpanded: () => void;
  onClose: () => void;
  children?: ReactNode;
}) {
  const detailQuery = trpc.interventions.getInterventionDetail.useQuery(
    { featureId },
    { retry: false }
  );
  const record = (detailQuery.data as InterventionDetailRecord | undefined) ?? null;
  const submitterLabel = useSubmitterLabel(record?.submittedByUserId);

  return (
    <InterventionDetailCard
      record={record}
      submitterLabel={submitterLabel}
      isLoading={detailQuery.isLoading}
      isError={detailQuery.isError}
      {...shell}
    >
      {children}
    </InterventionDetailCard>
  );
}

export interface InterventionDetailCardProps {
  /** Null while the by-id fetch is in flight, or when it failed. */
  record: InterventionDetailRecord | null;
  /**
   * `submittedByUserId` resolved through `users.getDisplayNames` by the
   * container. Omitted (the card's own default) means "no directory read has
   * happened", and the card applies the same id-fragment fallback the comment
   * thread uses rather than printing a raw uuid at a reader.
   */
  submitterLabel?: string | null;
  isLoading: boolean;
  isError: boolean;
  /** False is the compact card; true is the full-viewport lightbox. */
  isExpanded: boolean;
  onToggleExpanded: () => void;
  onClose: () => void;
  /**
   * Phase 5's mount point: the like control and comment thread backed by
   * `trpc.interventionSocial.*` render here, below the fields. Nothing in this
   * component reads or writes social state -- deliberately, so Phase 5 adds a
   * child rather than editing this file.
   */
  children?: ReactNode;
}

/** The presentational half, kept prop-driven so it renders without a tRPC client. */
export function InterventionDetailCard({
  record,
  submitterLabel,
  isLoading,
  isError,
  isExpanded,
  onToggleExpanded,
  onClose,
  children,
}: InterventionDetailCardProps) {
  const shell = isExpanded
    ? "inset-4 sm:inset-8"
    : // Bottom centre: clear of the left dock (19rem) and the right-edge AI
      // workspace (max 24rem) so both can be open without a permanent overlap.
      "bottom-6 left-1/2 w-[min(28rem,calc(100vw-2rem))] -translate-x-1/2";

  return (
    <div
      role="dialog"
      aria-modal={isExpanded}
      aria-label={
        record && isRequestRecord(record)
          ? "Strategy request details"
          : "Intervention details"
      }
      data-kind={record ? (isRequestRecord(record) ? "request" : "intervention") : undefined}
      data-testid="intervention-detail-modal"
      data-expanded={isExpanded}
      className={`absolute z-40 flex max-h-[calc(100%-3rem)] flex-col overflow-hidden rounded-xl border border-[hsl(var(--border))] bg-[hsl(var(--card))] text-[hsl(var(--card-foreground))] shadow-2xl ${shell}`}
    >
      <header className="flex items-start gap-2 border-b border-[hsl(var(--border))] px-4 py-3">
        <div className="min-w-0 flex-1">
          <h2 className="truncate text-sm font-semibold">
            {record?.name ?? (isLoading ? "Loading intervention" : "Intervention")}
          </h2>
          {record ? (
            <div className="mt-1 flex flex-wrap items-center gap-1">
              {isRequestRecord(record) ? <RequestBadge /> : null}
              <StatusPill status={record.status} category={record.category} />
            </div>
          ) : null}
        </div>
        <button
          type="button"
          onClick={onToggleExpanded}
          aria-expanded={isExpanded}
          className="rounded border border-[hsl(var(--border))] px-2 py-1 text-xs"
        >
          {isExpanded ? "Collapse" : "Expand"}
        </button>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close intervention details"
          className="rounded border border-[hsl(var(--border))] px-2 py-1 text-xs"
        >
          Close
        </button>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3 text-xs">
        {isError ? (
          <p role="alert">This intervention could not be loaded.</p>
        ) : isLoading || !record ? (
          <p>Loading intervention details…</p>
        ) : (
          <InterventionDetailBody
            record={record}
            submitterLabel={submitterLabel}
            isExpanded={isExpanded}
          />
        )}
        {/* Phase 5 (FR-4) mounts the like control and comment thread here. */}
        {children}
      </div>
    </div>
  );
}

function InterventionDetailBody({
  record,
  submitterLabel,
  isExpanded,
}: {
  record: InterventionDetailRecord;
  submitterLabel?: string | null;
  isExpanded: boolean;
}) {
  const submittedBy =
    submitterLabel ??
    (record.submittedByUserId
      ? contributorFallbackLabel(record.submittedByUserId)
      : null);

  return (
    <div className="space-y-3">
      <GeometryPreview record={record} size={isExpanded ? 320 : 160} />
      <dl className="grid grid-cols-2 gap-x-3 gap-y-1">
        <Field label="Type" value={record.type} />
        <Field label="Category" value={categoryLabel(record.category)} />
        <Field label="Status" value={statusLabel(record.status)} />
        <Field label="Submitted by" value={submittedBy} />
        <Field label="Workspace" value={record.submittedByTeamId} />
        <Field label="Created" value={formatTimestamp(record.createdAt)} />
        <Field label="Updated" value={formatTimestamp(record.updatedAt)} />
      </dl>
      <section>
        <h3 className="font-medium">Description</h3>
        <p className="whitespace-pre-wrap">{record.description ?? "None given"}</p>
      </section>
      {/* `reviewNote` is `contributions.rejectContribution`'s column and means
          nothing on any other status, so it is shown on exactly one. */}
      {record.status === "rejected" && record.reviewNote ? (
        <section data-testid="intervention-review-note">
          <h3 className="font-medium">Reviewer note</h3>
          <p className="whitespace-pre-wrap">{record.reviewNote}</p>
        </section>
      ) : null}
    </div>
  );
}

function Field({ label, value }: { label: string; value: string | null }) {
  return (
    <div className="min-w-0">
      <dt className="text-[hsl(var(--muted-foreground))]">{label}</dt>
      <dd className="truncate">{value ?? "Not recorded"}</dd>
    </div>
  );
}

/**
 * Is this row a public strategy request rather than a drawn recommendation?
 *
 * Stated once, here, because `kind` is absent on every row written before
 * 2026-09-13 and on every drafts-overlay record (a request publishes
 * immediately and so never reaches the draft path) -- an absent value is an
 * intervention, and three call sites re-deriving that default is how the two
 * would eventually disagree.
 */
export function isRequestRecord(record: InterventionDetailRecord): boolean {
  return record.kind === "request";
}

/**
 * What tells a reader they are looking at an ask rather than a proposal.
 *
 * Its own badge, next to the status pill, rather than a value folded into the
 * Type or Category field: type and category say what KIND OF WORK is involved,
 * which a request and a recommendation answer identically -- the thing that
 * differs is whether anybody has committed to doing it. It carries the same blue
 * the map paints a request with (`INTERVENTION_REQUEST_COLOR`), so the dot the
 * reader clicked and the panel that opened agree on sight.
 */
function RequestBadge() {
  return (
    <span
      data-testid="intervention-kind-badge"
      data-kind="request"
      style={{ backgroundColor: INTERVENTION_REQUEST_COLOR }}
      className="inline-block rounded px-2 py-0.5 text-[10px] font-medium text-white"
    >
      {INTERVENTION_REQUEST_LABEL}
    </span>
  );
}

function StatusPill({
  status,
  category,
}: {
  status: string;
  category: string | null;
}) {
  // The same status-over-category precedence the map paints with
  // (`INTERVENTION_STATUS_COLOR`): in-review is orange whatever its category,
  // so the panel and the dot on the map never disagree.
  const color =
    status === "pending_review"
      ? INTERVENTION_PENDING_REVIEW_COLOR
      : (INTERVENTION_CATEGORY_CLASSES.find((entry) => entry.value === category)
          ?.color ?? "#6b7280");

  return (
    <span
      data-testid="intervention-status-pill"
      data-status={status}
      style={{ backgroundColor: color }}
      className="mt-1 inline-block rounded px-2 py-0.5 text-[10px] font-medium text-white"
    >
      {statusLabel(status)}
    </span>
  );
}

/**
 * The drawn shape itself, drawn.
 *
 * An inline SVG rather than a second MapLibre instance: the requirement is that
 * the reader sees the real outline they or someone else submitted, not that the
 * outline is basemapped. Every position of every ring is plotted, so a polygon
 * renders as its polygon and a multi-point submission as each of its points --
 * a centroid pin would be exactly the thing FR-2 forbids.
 */
export function GeometryPreview({
  record,
  size,
}: {
  record: InterventionDetailRecord;
  size: number;
}) {
  // The same precedence the map paints with, so the preview of a request is the
  // colour of the dot the reader just clicked: kind first, then status, then the
  // published default.
  const strokeColor = isRequestRecord(record)
    ? INTERVENTION_REQUEST_COLOR
    : record.status === "pending_review"
      ? INTERVENTION_PENDING_REVIEW_COLOR
      : "#0d9488";
  const rings = useMemo(
    () => (record.geometry ? geometryRings(record.geometry) : []),
    [record.geometry]
  );
  const positions = rings.flat();

  if (positions.length === 0) {
    return <p data-testid="intervention-geometry-missing">No geometry recorded.</p>;
  }

  const longitudes = positions.map(([longitude]) => longitude);
  const latitudes = positions.map(([, latitude]) => latitude);
  const minX = Math.min(...longitudes);
  const maxX = Math.max(...longitudes);
  const minY = Math.min(...latitudes);
  const maxY = Math.max(...latitudes);
  // A degenerate extent (a single point, or a perfectly axis-aligned line) would
  // divide by zero; pad it so the shape lands in the middle of the box instead.
  const spanX = maxX - minX || 1e-4;
  const spanY = maxY - minY || 1e-4;
  const project = ([longitude, latitude]: [number, number]): [number, number] => [
    ((longitude - minX) / spanX) * 92 + 4,
    // SVG y grows downward; latitude grows upward.
    (1 - (latitude - minY) / spanY) * 92 + 4,
  ];

  return (
    <figure className="m-0">
      <svg
        data-testid="intervention-geometry-preview"
        data-position-count={positions.length}
        viewBox="0 0 100 100"
        width={size}
        height={size}
        role="img"
        aria-label={`Drawn geometry: ${record.geometry?.type ?? "unknown"} with ${positions.length} positions`}
        className="w-full rounded border border-[hsl(var(--border))] bg-[hsl(var(--muted))]"
      >
        {rings.map((ring, index) =>
          ring.length > 1 ? (
            <polygon
              key={`ring-${index}`}
              points={ring.map(project).map(([x, y]) => `${x},${y}`).join(" ")}
              fill="rgba(13,148,136,0.25)"
              stroke={strokeColor}
              strokeWidth={1}
            />
          ) : null
        )}
        {positions.map((position, index) => {
          const [x, y] = project(position);
          return (
            <circle
              key={`position-${index}`}
              cx={x}
              cy={y}
              r={positions.length > 1 ? 1.2 : 3}
              fill={strokeColor}
            />
          );
        })}
      </svg>
      <figcaption className="mt-1 text-[hsl(var(--muted-foreground))]">
        {record.hasFullGeometry
          ? `${record.geometry?.type} · ${positions.length} positions as drawn`
          : "Approximate location only — the full outline was not shared for this submission"}
      </figcaption>
    </figure>
  );
}

/** Every coordinate ring of any geometry type, as flat position arrays. */
function geometryRings(geometry: GeoJSON.Geometry): [number, number][][] {
  switch (geometry.type) {
    case "Point":
      return [[geometry.coordinates as [number, number]]];
    case "MultiPoint":
    case "LineString":
      return [geometry.coordinates as [number, number][]];
    case "MultiLineString":
    case "Polygon":
      return geometry.coordinates as [number, number][][];
    case "MultiPolygon":
      return (geometry.coordinates as [number, number][][][]).flat();
    case "GeometryCollection":
      return geometry.geometries.flatMap(geometryRings);
    default:
      return [];
  }
}

function categoryLabel(category: string | null): string {
  return (
    INTERVENTION_CATEGORY_CLASSES.find((entry) => entry.value === category)?.label ??
    INTERVENTION_UNCLASSIFIED_LABEL
  );
}

/** The served vocabulary only: `castModerationVote`'s parallel states are not live state. */
function statusLabel(status: string): string {
  if (status === "pending_review") return INTERVENTION_PENDING_REVIEW_LABEL;
  if (status === "published") return "Published";
  if (status === "rejected") return "Rejected";
  return status;
}

function formatTimestamp(value: Date | string | null): string | null {
  if (!value) return null;
  const date = value instanceof Date ? value : new Date(value);
  return Number.isNaN(date.getTime()) ? null : date.toISOString().slice(0, 10);
}
