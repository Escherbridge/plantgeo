import type { Metadata } from "next";
import { DocumentPage } from "@/components/layout/DocumentPage";
import {
  EditorialDefinitionList,
  EditorialLink,
  EditorialProse,
  EditorialSection,
} from "@/components/ui/editorial";
import { AI_GENERATED_DISCLAIMER } from "@/lib/regional-intelligence";
import {
  OPERATOR_EMAIL,
  OPERATOR_LEGAL_NAME,
  OPERATOR_MAILING_ADDRESS,
} from "@/lib/site-operator";

export const metadata: Metadata = {
  title: "Privacy - PlantGeo",
  description:
    "What PlantGeo stores for accounts, the map, the assistant, and the browser, and which hosts receive a request.",
};

const browserHosts = [
  {
    term: "tiles.aevani.com",
    description:
      "The basemap archive. The browser requests byte ranges from this host as you pan.",
    note: "Basemap",
  },
  {
    term: "Esri World Imagery",
    description:
      "Satellite tiles, requested by the browser from Esri. The tile address is the area on screen.",
    note: "Satellite",
  },
  {
    term: "Elevation tiles",
    description:
      "Terrain images, requested by the browser from the public AWS Open Data elevation bucket.",
    note: "Terrain",
  },
  {
    term: "Protomaps assets",
    description:
      "Map fonts and sprites, requested by the browser from the Protomaps basemap asset host.",
    note: "Labels",
  },
];

const deviceStores = [
  {
    term: "plantgeo-search",
    description: "Up to ten recent place-search results, including coordinates.",
    note: "localStorage",
  },
  {
    term: "plantgeo-layer-opacity",
    description: "Per-layer opacity multipliers.",
    note: "localStorage",
  },
  {
    term: "plantgeo-layer-window",
    description: "The 7, 30, 90, or 365 day history window chosen per layer.",
    note: "localStorage",
  },
  {
    term: "plantgeo-layer-cache-policy",
    description: "Per-layer choices for how long saved map answers are kept.",
    note: "localStorage",
  },
  {
    term: "plantgeo_dashboard_layout",
    description: "The signed-in dashboard arrangement.",
    note: "localStorage",
  },
  {
    term: "plantgeo-rail-hint-dismissed",
    description: "Whether the map-manager hint has been dismissed.",
    note: "localStorage",
  },
];

export default function PrivacyPage() {
  return (
    <DocumentPage
      eyebrow="Privacy"
      title="What the application stores."
      lead="This page describes the records and requests the running application makes. Account, map, and assistant behavior is summarized again on the Terms page."
    >
      <EditorialSection index="01" title="Account records" id="accounts">
        <EditorialProse>
          <p>
            An account stores an email address, an optional name, an optional
            image, a bcrypt password hash, an email-verified time, a platform
            role, a verified flag, and the active organization. Google and
            GitHub sign-in are available when that provider&apos;s client id and
            secret are both set. A linked provider stores its account id and the
            access token, refresh token, and id token it returned.
          </p>
          <p>
            Email-verification and password-reset tokens are stored as SHA-256
            hashes. A verification token expires after 24 hours and a reset
            token after one hour. Organization invitations store the invited
            email and expire after seven days. An organization profile stores
            the name, slug, description, type, specialties, website, and service
            area its members enter, plus each member&apos;s role.
          </p>
          <p>
            An API key is stored as a hash, with a name, a permission list, a
            rate limit, and a last-used time.
          </p>
        </EditorialProse>
      </EditorialSection>

      <EditorialSection index="02" title="Session" id="session">
        <EditorialProse>
          <p>
            Signing in sets a NextAuth session cookie. The session is a JSON
            Web Token carrying the account id, platform role, and active
            organization. Signing out ends that session. The application code
            does not load a third-party analytics tag.
          </p>
          <p>
            Registration, sign-in, and several public map routes rate-limit
            with Redis. The key is a SHA-256 digest of the caller address, or
            of the submitted email for the per-account sign-in limit. The
            counter expires in about a minute.
          </p>
        </EditorialProse>
      </EditorialSection>

      <EditorialSection index="03" title="Map and community" id="map">
        <EditorialProse>
          <p>
            A strategy request publishes its location, title, and description
            on the public map immediately. Anyone can read it. An intervention
            proposal stores the drawn point or boundary and the fields on the
            form. A proposal submitted for publication review is visible to
            signed-in readers while it is pending, and becomes public after
            approval. An evidence link on a community data submission stays
            limited to the submitter, their organization, or an expert or admin
            until the row is approved.
          </p>
          <p>
            Comments and likes are tied to the signed-in account. The author of
            a comment, or an expert or admin, can remove it. Removal records
            the deletion time and the account that removed it, and keeps the
            row. A watched location stores a name, latitude, longitude, and
            radius for the account, along with alert subscriptions and the
            alerts those subscriptions produce. The demand heatmap draws a cell
            only when at least three records fall in it.
          </p>
          <p>
            A link from the feed can put <em>focusLng</em>, <em>focusLat</em>,
            and <em>focusZoom</em> on the map address, so that place is in the
            browser address and its history.
          </p>
        </EditorialProse>
      </EditorialSection>

      <EditorialSection index="04" title="Location" id="location">
        <EditorialProse>
          <p>
            The locate control uses the browser location API when you turn it
            on, and can keep watching while it stays on. It recenters the map.
            The camera for the session is held in memory. Place search is sent
            to this site, which queries the deployment&apos;s Photon geocoder.
            Routing queries are sent to this site&apos;s Valhalla service.
            Choosing a point for analysis asks for an approximate coordinate,
            two decimal places, or an exact one, six decimal places, before the
            assistant request is sent.
          </p>
          <p>
            An authorized client can post a position for a registered asset:
            coordinates, heading, speed, altitude, and time. Those rows are
            what the admin tracking page reads.
          </p>
        </EditorialProse>
      </EditorialSection>

      <EditorialSection index="05" title="Assistant" id="assistant">
        <EditorialProse>
          <p>
            An analysis stores the account, the coordinates, a title, the
            messages, any structured report, token counts, and an optional
            helpful or not-helpful rating with a reason. A daily job deletes
            conversations whose last update is older than 30 days, and the
            messages on them.
          </p>
          <p>
            The question and the coordinates are sent to OpenRouter. The
            default model is <em>google/gemini-2.5-flash-lite</em>. The{" "}
            <em>OPENROUTER_MODEL</em> setting can name another model. When{" "}
            <em>JINA_API_KEY</em> is set, the question text is also sent to
            Jina for a web search. Street-level imagery is fetched by this
            site from Mapillary, and the NDVI overlay is fetched by this site
            from NASA GIBS. The report shown in the product carries this
            sentence: {AI_GENERATED_DISCLAIMER}
          </p>
        </EditorialProse>
      </EditorialSection>

      <EditorialSection index="06" title="Hosts the browser calls" id="browsers">
        <EditorialProse>
          <p>
            These hosts are requested from the browser, so each one receives
            the network address and the tiles or files the view asked for.
            Published community features are requested from the deployment&apos;s
            Martin tile server. Environmental readings are requested from this
            site, which reads them from the governed data service.
          </p>
        </EditorialProse>
        <EditorialDefinitionList items={browserHosts} className="mt-roomy" />
      </EditorialSection>

      <EditorialSection index="07" title="On this device" id="device">
        <EditorialProse>
          <p>
            The browser keeps the preferences below. Layer answers the map has
            already loaded, for the layers the cache accepts, are saved in
            IndexedDB so that day can be shown again. A service worker caches
            the app shell and some tile responses on the device. API responses
            stay on the network. See{" "}
            <EditorialLink href="/terms">Terms</EditorialLink> for how
            publishing and sign-in behave.
          </p>
        </EditorialProse>
        <EditorialDefinitionList items={deviceStores} className="mt-roomy" />
      </EditorialSection>

      <EditorialSection index="08" title="Operator" id="operator">
        <EditorialProse>
          <p>
            This site is operated by {OPERATOR_LEGAL_NAME}. Questions, privacy
            requests, and data requests can be sent to{" "}
            <EditorialLink href={`mailto:${OPERATOR_EMAIL}`} prefetch={false}>
              {OPERATOR_EMAIL}
            </EditorialLink>{" "}
            or by mail to {OPERATOR_MAILING_ADDRESS}.
          </p>
        </EditorialProse>
      </EditorialSection>
    </DocumentPage>
  );
}
