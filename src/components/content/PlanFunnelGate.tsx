import {
  EditorialActionLink,
  EditorialEyebrow,
  EditorialLink,
  EditorialPanel,
  EditorialProse,
  EditorialSubheading,
} from "@/components/ui/editorial";

/**
 * Shared top-of-funnel gate moving readers from editorial content into the
 * interactive Ada County sample plan or a private new plan.
 */
export function PlanFunnelGate({
  eyebrow = "Put it on the map",
  heading = "Walk the Ada County sample plan",
  body = "Toggle bare-earth contours, low drainage swales, wetlands, soil units, and flood lines on a real Ada County parcel, then start a private plan for your own land.",
}: {
  eyebrow?: string;
  heading?: string;
  body?: string;
}) {
  return (
    <EditorialPanel className="mt-section max-w-measure">
      <EditorialEyebrow>{eyebrow}</EditorialEyebrow>
      <EditorialSubheading as="h2" className="mt-tight">
        {heading}
      </EditorialSubheading>
      <EditorialProse className="mt-snug">
        <p>{body}</p>
      </EditorialProse>
      <div className="mt-comfortable flex flex-wrap gap-snug">
        <EditorialActionLink href="/plan/sample-ada-county" tone="solid">
          Open the Ada County Sample Plan
        </EditorialActionLink>
        <EditorialActionLink href="/plan/new" tone="outline">
          Start your own site plan
        </EditorialActionLink>
      </div>
      <p className="mt-comfortable font-editorial-text text-caption text-ink-muted">
        Walking parcels with clients or growers?{" "}
        <EditorialLink href="/affiliate">Become a site-planning affiliate</EditorialLink>.
      </p>
    </EditorialPanel>
  );
}

/**
 * Uniform bottom caveat keeping legal, scope, forecast, and preview boundaries
 * clear without cluttering article openings.
 */
export function MapLegalNote({
  author,
  lastReviewed,
  includeCatalogueNote = false,
}: {
  author?: string;
  lastReviewed?: string;
  includeCatalogueNote?: boolean;
}) {
  return (
    <aside
      aria-label="Map and legal notes"
      className="mt-section max-w-measure rule-top-hairline border-t-rule-faint pt-comfortable font-editorial-text text-caption text-ink-muted [&>*+*]:mt-tight"
    >
      <p>
        <strong>Map &amp; legal note:</strong> Public layers on PlantGeo are site-planning aids,
        not a property survey, engineered grading plan, permitting decision, or insurance advice.
        PlantGeo publishes no forecasts and claims no agency deployments.
      </p>
      <p>
        {includeCatalogueNote ? (
          <>
            Tools and growing systems linked from{" "}
            <a href="https://aevani.com" className="underline decoration-accent decoration-2 underline-offset-4">
              Aevani&apos;s catalogue
            </a>{" "}
            are planning previews and are not for sale yet.{" "}
          </>
        ) : (
          "Any Aevani catalogue listings shown alongside a site plan are planning previews and are not for sale yet. "
        )}
        No affiliate referral share is quoted because terms are set and reviewed individually by
        hand. Companion pages live in <EditorialLink href="/site-types">site types</EditorialLink>{" "}
        and <EditorialLink href="/about">About</EditorialLink>.
      </p>
      {author && lastReviewed ? (
        <p>
          Author: {author}. Last reviewed: {lastReviewed}, America/Denver.
        </p>
      ) : null}
    </aside>
  );
}
