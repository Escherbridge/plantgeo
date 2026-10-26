"use client";

/**
 * The Alerts section of the map dock: the reader's own unread alerts, the locations they watch,
 * and per-location/per-type subscription toggles. `alertsRouter` is real CRUD and
 * `src/lib/server/jobs/alert-dispatcher.ts` actually populates `environmentalAlerts` every
 * 30 minutes (fire proximity, drought escalation, critically low streamflow) -- see
 * `src/components/panels/AGENTS.md` for why this section was removed on 2026-08-15 and what
 * changed to bring it back.
 *
 * Every `trpc.alerts.*` procedure is `protectedProcedure`, so a signed-out reader gets a prompt
 * and issues no query at all -- same `enabled: authenticated` pattern as
 * `InterventionLikeButton`, never a raw UNAUTHORIZED on screen.
 */

import { useState } from "react";
import type { inferRouterOutputs } from "@trpc/server";
import { useSession } from "next-auth/react";
import { Check, CheckCheck, ChevronDown, ChevronRight, MapPin, Plus } from "lucide-react";
import { trpc } from "@/lib/trpc/client";
import type { AppRouter } from "@/lib/trpc/client";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { TOUCH_TARGET, TOUCH_TARGET_SQUARE } from "@/components/ui/touch-target";
import { SocialSignInPrompt } from "@/components/intervention/sign-in-gate";
import type { MapQueryPoint } from "@/stores/map-store";

type RouterOutputs = inferRouterOutputs<AppRouter>;
type WatchedLocation = RouterOutputs["alerts"]["getWatchedLocations"][number];

const SEVERITY_STYLES: Record<string, string> = {
  critical: "border-red-500/40 bg-red-500/10 text-red-700 dark:text-red-400",
  warning: "border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-400",
  info: "border-[hsl(var(--border))] text-[hsl(var(--muted-foreground))]",
};

/** Byte-identical to the enum `alerts.updateSubscription` accepts. */
const ALERT_TYPES = [
  { id: "fire_proximity", label: "Fire proximity" },
  { id: "drought_escalation", label: "Drought escalation" },
  { id: "streamflow_critical", label: "Critically low streamflow" },
  { id: "priority_zone_created", label: "Priority zone created" },
] as const;

const DEFAULT_RADIUS_KM = 50;

/**
 * One watched location: a disclosure, and -- only once opened -- its own subscription toggles.
 * Its own component (not a row inside a `.map()`) because `getSubscriptions` is keyed per
 * location and gated on this row's own `expanded` state, which cannot be a conditional hook call
 * inside a loop.
 */
function WatchedLocationRow({ location }: { location: WatchedLocation }) {
  const [expanded, setExpanded] = useState(false);
  const subscriptionsQuery = trpc.alerts.getSubscriptions.useQuery(
    { locationId: location.id },
    { enabled: expanded }
  );
  const trpcUtils = trpc.useUtils();
  const updateSubscription = trpc.alerts.updateSubscription.useMutation({
    onSuccess: () => trpcUtils.alerts.getSubscriptions.invalidate({ locationId: location.id }),
  });

  return (
    <li className="rounded border border-[hsl(var(--border))] p-2">
      <button
        type="button"
        onClick={() => setExpanded((value) => !value)}
        aria-expanded={expanded}
        className={`flex w-full items-center gap-2 text-left text-xs ${TOUCH_TARGET}`}
      >
        {expanded ? (
          <ChevronDown aria-hidden="true" className="h-3.5 w-3.5 shrink-0" />
        ) : (
          <ChevronRight aria-hidden="true" className="h-3.5 w-3.5 shrink-0" />
        )}
        <MapPin
          aria-hidden="true"
          className="h-3.5 w-3.5 shrink-0 text-[hsl(var(--muted-foreground))]"
        />
        <span className="min-w-0 flex-1 truncate font-medium text-[hsl(var(--foreground))]">
          {location.name}
        </span>
        <span className="shrink-0 text-[hsl(var(--muted-foreground))]">
          {location.radiusKm ?? DEFAULT_RADIUS_KM} km
        </span>
      </button>

      {expanded && (
        <div className="mt-2 flex flex-col gap-1.5 border-t border-[hsl(var(--border))] pt-2">
          {subscriptionsQuery.isPending ? (
            <p className="text-[11px] text-[hsl(var(--muted-foreground))]">
              Loading subscriptions…
            </p>
          ) : (
            ALERT_TYPES.map((alertType) => {
              const saved = subscriptionsQuery.data?.find(
                (row) => row.alertType === alertType.id
              );
              // Unsaved default mirrors the schema's own column default (both true); it is
              // never shown as a confirmed server answer because `getSubscriptions` has
              // already resolved by the time this renders -- a location with a saved row
              // always shows THAT row's values instead.
              const emailEnabled = saved?.emailEnabled ?? true;
              const inAppEnabled = saved?.inAppEnabled ?? true;
              return (
                <div
                  key={alertType.id}
                  className="flex items-center justify-between gap-2 text-[11px]"
                >
                  <span className="min-w-0 flex-1 truncate text-[hsl(var(--foreground))]">
                    {alertType.label}
                  </span>
                  <label className={`flex items-center gap-1 ${TOUCH_TARGET}`}>
                    <input
                      type="checkbox"
                      checked={inAppEnabled}
                      onChange={(event) =>
                        updateSubscription.mutate({
                          locationId: location.id,
                          alertType: alertType.id,
                          emailEnabled,
                          inAppEnabled: event.target.checked,
                        })
                      }
                    />
                    In-app
                  </label>
                  <label className={`flex items-center gap-1 ${TOUCH_TARGET}`}>
                    <input
                      type="checkbox"
                      checked={emailEnabled}
                      onChange={(event) =>
                        updateSubscription.mutate({
                          locationId: location.id,
                          alertType: alertType.id,
                          emailEnabled: event.target.checked,
                          inAppEnabled,
                        })
                      }
                    />
                    Email
                  </label>
                </div>
              );
            })
          )}
        </div>
      )}
    </li>
  );
}

interface AlertsDetailsProps {
  /** Default target for "Add watched location", same derivation `CommunityDetailsBody` passes. */
  mapCenter: { lat: number; lon: number };
  /** A picked map point, armed only while this section is mounted -- see `DockDetails.tsx`. */
  queryPoint?: MapQueryPoint | null;
  onClearQueryPoint?: () => void;
}

export function AlertsDetails({ mapCenter, queryPoint, onClearQueryPoint }: AlertsDetailsProps) {
  const { status } = useSession();
  const authenticated = status === "authenticated";

  const [showUnreadOnly, setShowUnreadOnly] = useState(false);
  const [showAddLocation, setShowAddLocation] = useState(false);
  const [locationName, setLocationName] = useState("");
  const [radiusKm, setRadiusKm] = useState(DEFAULT_RADIUS_KM);

  const trpcUtils = trpc.useUtils();

  const unreadCountQuery = trpc.alerts.getUnreadCount.useQuery(undefined, {
    enabled: authenticated,
  });
  const alertsQuery = trpc.alerts.getAlerts.useQuery(
    { limit: 20, unreadOnly: showUnreadOnly },
    { enabled: authenticated }
  );
  const locationsQuery = trpc.alerts.getWatchedLocations.useQuery(undefined, {
    enabled: authenticated,
  });

  const markRead = trpc.alerts.markRead.useMutation({
    onSuccess: () => {
      trpcUtils.alerts.getAlerts.invalidate();
      trpcUtils.alerts.getUnreadCount.invalidate();
    },
  });
  const markAllRead = trpc.alerts.markAllRead.useMutation({
    onSuccess: () => {
      trpcUtils.alerts.getAlerts.invalidate();
      trpcUtils.alerts.getUnreadCount.invalidate();
    },
  });
  const addLocation = trpc.alerts.addWatchedLocation.useMutation({
    onSuccess: () => {
      trpcUtils.alerts.getWatchedLocations.invalidate();
      setShowAddLocation(false);
      setLocationName("");
      onClearQueryPoint?.();
    },
  });

  if (!authenticated) {
    return (
      <div className="flex flex-col gap-2">
        <p className="text-xs text-[hsl(var(--muted-foreground))]">
          Sign in to receive alerts about active fires, drought escalation and critically low
          streamflow near the places you watch.
        </p>
        <SocialSignInPrompt
          message="Sign in to set up environmental alerts."
          testId="alerts-signin-prompt"
        />
      </div>
    );
  }

  const alerts = alertsQuery.data ?? [];
  const locations = locationsQuery.data ?? [];
  const unreadCount = unreadCountQuery.data ?? 0;
  const addLocationTarget = queryPoint ?? mapCenter;

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-col gap-1.5">
        <div className="flex items-center justify-between gap-2">
          <h4 className="text-[11px] font-semibold uppercase tracking-wide text-[hsl(var(--muted-foreground))]">
            Alerts{unreadCount > 0 ? ` (${unreadCount} unread)` : ""}
          </h4>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => setShowUnreadOnly((value) => !value)}
              aria-pressed={showUnreadOnly}
              className={`text-[11px] text-[hsl(var(--muted-foreground))] hover:text-[hsl(var(--foreground))] ${TOUCH_TARGET}`}
            >
              {showUnreadOnly ? "Show all" : "Unread only"}
            </button>
            {unreadCount > 0 && (
              <button
                type="button"
                onClick={() => markAllRead.mutate()}
                disabled={markAllRead.isPending}
                className={`flex items-center gap-1 text-[11px] text-[hsl(var(--muted-foreground))] hover:text-[hsl(var(--foreground))] disabled:opacity-50 ${TOUCH_TARGET}`}
              >
                <CheckCheck aria-hidden="true" className="h-3 w-3" />
                Mark all read
              </button>
            )}
          </div>
        </div>

        {alertsQuery.isPending ? (
          <p className="text-xs text-[hsl(var(--muted-foreground))]">Loading alerts…</p>
        ) : alerts.length === 0 ? (
          <p className="text-xs text-[hsl(var(--muted-foreground))]">
            {showUnreadOnly
              ? "No unread alerts."
              : "No alerts yet. Add a watched location below to start monitoring it."}
          </p>
        ) : (
          <ul className="flex flex-col gap-1.5">
            {alerts.map((alert) => (
              <li
                key={alert.id}
                data-testid="alert-row"
                className={`rounded border px-2 py-1.5 text-xs ${
                  SEVERITY_STYLES[alert.severity] ?? SEVERITY_STYLES.info
                }`}
              >
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0 flex-1">
                    <p className="font-medium">{alert.title}</p>
                    {alert.body && <p className="mt-0.5 text-[11px] opacity-80">{alert.body}</p>}
                  </div>
                  {!alert.isRead && (
                    <button
                      type="button"
                      aria-label="Mark as read"
                      onClick={() => markRead.mutate({ alertId: alert.id })}
                      disabled={markRead.isPending}
                      className={`flex shrink-0 items-center justify-center rounded text-[hsl(var(--muted-foreground))] hover:text-[hsl(var(--foreground))] disabled:opacity-50 ${TOUCH_TARGET_SQUARE}`}
                    >
                      <Check aria-hidden="true" className="h-3.5 w-3.5" />
                    </button>
                  )}
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="flex flex-col gap-1.5 border-t border-[hsl(var(--border))] pt-2">
        <div className="flex items-center justify-between gap-2">
          <h4 className="text-[11px] font-semibold uppercase tracking-wide text-[hsl(var(--muted-foreground))]">
            Watched locations
          </h4>
          <button
            type="button"
            onClick={() => setShowAddLocation((value) => !value)}
            aria-expanded={showAddLocation}
            className={`flex items-center gap-1 text-[11px] text-[hsl(var(--muted-foreground))] hover:text-[hsl(var(--foreground))] ${TOUCH_TARGET}`}
          >
            <Plus aria-hidden="true" className="h-3 w-3" />
            Add
          </button>
        </div>

        {showAddLocation && (
          <form
            data-testid="add-watched-location-form"
            onSubmit={(event) => {
              event.preventDefault();
              if (!locationName.trim()) return;
              addLocation.mutate({
                name: locationName.trim(),
                lat: addLocationTarget.lat,
                lon: addLocationTarget.lon,
                radiusKm,
              });
            }}
            className="flex flex-col gap-1.5 rounded border border-[hsl(var(--border))] p-2"
          >
            <Input
              placeholder="Location name"
              value={locationName}
              onChange={(event) => setLocationName(event.target.value)}
              className="h-8 text-xs"
            />
            <div className="flex items-center justify-between gap-2 text-[11px] text-[hsl(var(--muted-foreground))]">
              <span>
                {queryPoint ? "Clicked point" : "Map center"}: {addLocationTarget.lat.toFixed(2)},{" "}
                {addLocationTarget.lon.toFixed(2)}
              </span>
              {queryPoint && (
                <button
                  type="button"
                  onClick={onClearQueryPoint}
                  className={`shrink-0 underline ${TOUCH_TARGET}`}
                >
                  Use map center
                </button>
              )}
            </div>
            <label className="flex items-center gap-2 text-[11px] text-[hsl(var(--muted-foreground))]">
              Radius (km)
              <Input
                type="number"
                min={1}
                max={500}
                value={radiusKm}
                onChange={(event) => setRadiusKm(Number(event.target.value) || DEFAULT_RADIUS_KM)}
                className="h-8 w-20 text-xs"
              />
            </label>
            <Button
              type="submit"
              size="sm"
              disabled={addLocation.isPending || !locationName.trim()}
            >
              Save location
            </Button>
          </form>
        )}

        {!showAddLocation && queryPoint && (
          <p className="text-[11px] text-[hsl(var(--muted-foreground))]">
            A point is picked on the map. Click Add to watch it, or Escape to drop the pin.
          </p>
        )}

        {locationsQuery.isPending ? (
          <p className="text-xs text-[hsl(var(--muted-foreground))]">
            Loading watched locations…
          </p>
        ) : locations.length === 0 ? (
          <p className="text-xs text-[hsl(var(--muted-foreground))]">No watched locations yet.</p>
        ) : (
          <ul className="flex flex-col gap-1.5">
            {locations.map((location) => (
              <WatchedLocationRow key={location.id} location={location} />
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
