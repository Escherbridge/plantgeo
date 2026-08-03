import type { Metadata } from "next";
import {
  EditorialActionLink,
  EditorialCaption,
  EditorialColophon,
  EditorialContainer,
  EditorialDefinitionList,
  EditorialDisplay,
  EditorialEyebrow,
  EditorialGrid,
  EditorialLead,
  EditorialLink,
  EditorialPage,
  EditorialProse,
  EditorialPullQuote,
  EditorialRule,
  EditorialSection,
  EditorialSubheading,
} from "@/components/ui/editorial";

export const metadata: Metadata = {
  title: "About - PlantGeo",
  description:
    "PlantGeo is a 3D geospatial platform assembled entirely from open-source parts: MapLibre, deck.gl, Martin, PMTiles, Valhalla, Photon and PostGIS.",
};

const mastheadIndex = [
  { term: "Rendering", description: "MapLibre GL JS v5, deck.gl v9, Three.js" },
  { term: "Tiles", description: "PMTiles v3 archives, Martin tile server" },
  { term: "Routing", description: "Valhalla, multi-modal" },
  { term: "Search", description: "Photon, indexed from Nominatim" },
  { term: "Store", description: "PostgreSQL, PostGIS, TimescaleDB" },
];

const parityIndex = [
  {
    term: "Interactive 3D map",
    description:
      "MapLibre GL JS v5 — globe projection, terrain exaggeration, extruded geometry.",
    note: "In the platform",
  },
  {
    term: "Basemap tiles",
    description:
      "PMTiles v3 archives read by range request, with Martin serving dynamic vector layers straight out of PostGIS.",
    note: "In the platform",
  },
  {
    term: "Address autocomplete",
    description: "Photon, running against a Nominatim index.",
    note: "In the platform",
  },
  {
    term: "Forward and reverse geocoding",
    description: "Nominatim over OpenStreetMap, self-hosted.",
    note: "In the platform",
  },
  {
    term: "Directions and turn-by-turn",
    description: "Valhalla, multi-modal, with route geometry returned inline.",
    note: "In the platform",
  },
  {
    term: "Travel-time isochrones",
    description:
      "Valhalla isolines. Google Maps Platform has no first-party equivalent.",
    note: "Beyond parity",
  },
  {
    term: "Large-scale data overlays",
    description:
      "deck.gl v9 in interleaved mode, so overlays live inside the 3D scene instead of floating above it.",
    note: "In the platform",
  },
  {
    term: "Live updates",
    description:
      "Server-sent events for broadcast alerts, WebSocket for bidirectional tracking.",
    note: "In the platform",
  },
  {
    term: "Custom 3D geometry",
    description: "Three.js mounted through the MapLibre CustomLayerInterface.",
    note: "In the platform",
  },
  {
    term: "Static map images",
    description:
      "No open drop-in we are happy with yet. Server-side rendering is still unspecified.",
    note: "Open",
  },
  {
    term: "Street-level imagery",
    description:
      "Mapillary and KartaView are the obvious candidates. Neither is integrated.",
    note: "Open",
  },
];

const stackIndex = [
  {
    term: "MapLibre GL JS",
    description:
      "The WebGL2 renderer. Globe projection, terrain, fill-extrusion, and the hook every custom layer hangs from.",
    note: "Render",
  },
  {
    term: "deck.gl",
    description:
      "Millions of features per frame, interleaved with the map render loop so depth and occlusion behave.",
    note: "Render",
  },
  {
    term: "Three.js",
    description:
      "Bespoke geometry — anything the style spec cannot express — drawn into the same scene graph.",
    note: "Render",
  },
  {
    term: "Martin",
    description:
      "A Rust tile server that turns PostGIS tables into MVT on request, and also fronts PMTiles and MBTiles archives.",
    note: "Tiles",
  },
  {
    term: "PMTiles v3",
    description:
      "Single-file tile archives on object storage, read by HTTP range request. No tile server in the hot path, no per-tile meter.",
    note: "Tiles",
  },
  {
    term: "Valhalla",
    description:
      "Routing, time-distance matrices, map matching and isochrones from one engine and one graph.",
    note: "Routing",
  },
  {
    term: "Photon and Nominatim",
    description:
      "Typeahead that answers in a keystroke, backed by a geocoder that knows what places are actually called.",
    note: "Search",
  },
  {
    term: "PostgreSQL, PostGIS, TimescaleDB",
    description:
      "Spatial truth and time-series history in one database, so where and when are answered by the same query.",
    note: "Data",
  },
  {
    term: "Redis",
    description:
      "Response cache for expensive GeoJSON, and pub/sub fan-out for anything that moves.",
    note: "Data",
  },
  {
    term: "Next.js, React, tRPC, Drizzle",
    description:
      "Typed end to end. A column rename in the schema is a compile error in the component that read it.",
    note: "App",
  },
];

const principles = [
  {
    index: "01",
    title: "Nothing metered by the millisecond",
    body: "Cost is a design constraint, not a footnote. PMTiles takes the tile server out of the request path; self-hosted routing and geocoding take out the per-call meter. A feature that only works with a billing relationship attached is not finished.",
  },
  {
    index: "02",
    title: "Data you can carry out",
    body: "PMTiles, MVT, GeoJSON, PostGIS. Every format we store in is one you can read without us, and every layer you bring stays yours — including the right to leave with it.",
  },
  {
    index: "03",
    title: "Legible internals",
    body: "The stack is a stack, not a black box. Layer styles, tile sources, routing costs and SQL are all inspectable, and the parts you are meant to change are documented as changeable.",
  },
  {
    index: "04",
    title: "Honest empty states",
    body: "When a dataset is not ready — not reviewed, not published, not permitted to leave a workspace — the interface says so and explains why. It does not fill the gap with plausible-looking content. That rule is enforced literally, not aspirationally.",
  },
];

export default function AboutPage() {
  return (
    <EditorialPage>
      <EditorialContainer>
        <header className="pt-section pb-roomy">
          <EditorialGrid>
            <div className="col-span-4 md:col-span-8">
              <EditorialEyebrow>
                About — Open-source geospatial infrastructure
              </EditorialEyebrow>
              <EditorialDisplay className="mt-comfortable">
                The whole map, unbundled.
              </EditorialDisplay>
            </div>

            <div className="col-span-4 mt-roomy md:col-span-3 md:col-start-10 md:mt-0 md:self-end">
              <EditorialRule weight="medium" />
              <dl className="mt-tight">
                {mastheadIndex.map((entry) => (
                  <div
                    key={entry.term}
                    className="rule-bottom-hairline flex flex-col gap-hairline border-b-rule-faint py-tight"
                  >
                    <dt className="font-editorial-label text-label text-accent uppercase">
                      {entry.term}
                    </dt>
                    <dd className="font-editorial-label text-caption text-ink-muted">
                      {entry.description}
                    </dd>
                  </div>
                ))}
              </dl>
            </div>
          </EditorialGrid>

          <EditorialGrid className="mt-roomy">
            <div className="col-span-4 md:col-span-7 md:col-start-5">
              <EditorialLead>
                PlantGeo is a 3D geospatial platform assembled entirely from
                open-source parts. Every capability Google Maps Platform sells by
                the request — basemaps, geocoding, routing, isolines, terrain,
                large-scale visualisation — already has a mature open
                counterpart. We run those counterparts ourselves, put them behind
                one coherent interface, and leave the seams visible.
              </EditorialLead>

              <div className="mt-comfortable flex flex-wrap gap-tight">
                <EditorialActionLink href="/">Open the map</EditorialActionLink>
                <EditorialActionLink href="/community" tone="outline">
                  Community ledger
                </EditorialActionLink>
                <EditorialActionLink href="/docs" tone="outline">
                  Documentation
                </EditorialActionLink>
              </div>
            </div>
          </EditorialGrid>
        </header>

        <EditorialSection index="01" title="What we are" id="what-we-are">
          <EditorialProse>
            <p>
              PlantGeo started from a plain observation: the hard parts of
              digital mapping are already solved, and they are solved in the
              open. MapLibre renders a globe. Valhalla routes across it.
              Nominatim knows what places are called. PostGIS answers spatial
              questions faster than most people expect. What has been missing is
              not capability — it is assembly. The unglamorous work of making a
              dozen excellent projects behave as one product.
            </p>
            <p>
              So that is the work. PlantGeo is an integration layer with
              opinions: a 3D map that is genuinely three-dimensional, a tile
              pipeline that does not need a server in the hot path, a routing
              engine that answers isochrone questions as readily as turn-by-turn
              ones, and a data layer that treats time as an axis rather than an
              afterthought.
            </p>
            <p>
              We build it for people who need the map to be infrastructure
              rather than a vendor relationship — wildfire prevention teams, land
              stewards, ecologists, agricultural partners — anyone whose
              questions outlive their tolerance for per-request pricing.
            </p>
          </EditorialProse>

          <EditorialPullQuote className="mt-roomy">
            Feature parity is the floor, not the ceiling. The point of owning the
            stack is the questions you can ask once nobody is charging you per
            answer.
          </EditorialPullQuote>
        </EditorialSection>

        <EditorialSection
          index="02"
          title="Parity, item by item"
          id="parity"
          className="pt-section"
        >
          <EditorialProse className="mb-roomy">
            <p>
              Parity claims are cheap, so here is the ledger. Each row names a
              capability, the open component that provides it, and where it
              honestly stands today. Two rows say <em>Open</em>. We would rather
              publish them than round them up.
            </p>
          </EditorialProse>
          <EditorialDefinitionList items={parityIndex} />
        </EditorialSection>

        <EditorialSection index="03" title="The stack" id="stack">
          <EditorialProse className="mb-roomy">
            <p>
              Nothing here is proprietary, and nothing here is a wrapper around
              something proprietary. Each component does one job well enough that
              replacing it would be a deliberate decision rather than a rescue.
            </p>
          </EditorialProse>
          <EditorialDefinitionList items={stackIndex} />
        </EditorialSection>

        <EditorialSection index="04" title="How we work" id="principles">
          <div className="rule-top-medium border-t-rule">
            {principles.map((principle) => (
              <article
                key={principle.index}
                className="rule-bottom-hairline grid grid-cols-4 gap-x-gutter border-b-rule-faint py-comfortable md:grid-cols-8"
              >
                <p className="col-span-4 font-editorial-label text-label text-accent uppercase md:col-span-1">
                  {principle.index}
                </p>
                <div className="col-span-4 mt-tight md:col-span-7 md:mt-0">
                  <EditorialSubheading>{principle.title}</EditorialSubheading>
                  <p className="mt-tight max-w-measure font-editorial-text text-body text-ink-muted">
                    {principle.body}
                  </p>
                </div>
              </article>
            ))}
          </div>
        </EditorialSection>

        <EditorialSection index="05" title="Where it runs" id="operations">
          <EditorialProse>
            <p>
              The application services run as a small constellation of
              containers: the Next.js app, the Martin tile server, Valhalla,
              Photon, Postgres and Redis. Tile archives live on object storage
              behind a CDN, which is the whole reason a basemap request never
              touches our compute.
            </p>
            <p>
              The default basemap currently ships from a Pacific Northwest
              extract, because shipping one region properly beats shipping the
              planet badly. Widening it is a rebuild of an archive, not a
              migration.
            </p>
            <p>
              None of this is load-bearing on a specific vendor. Every service is
              a container and every tile archive is a file, which means the
              platform can be lifted somewhere else by someone who is not us.
              That portability is the product as much as the map is. If you want
              the shape of it in more detail, the{" "}
              <EditorialLink href="/docs">documentation</EditorialLink> goes
              service by service.
            </p>
          </EditorialProse>
        </EditorialSection>
      </EditorialContainer>

      <EditorialContainer>
        <EditorialColophon>
          <EditorialGrid>
            <div className="col-span-4 md:col-span-5">
              <EditorialEyebrow>Colophon</EditorialEyebrow>
              <p className="mt-tight max-w-measure font-editorial-text text-body text-ink-muted">
                Set in Archivo for display, Newsreader for reading, and IBM Plex
                Mono for labels. Twelve columns above the fold of a tablet, four
                below. Rules at one, two, four and ten pixels. No corner radius
                anywhere, no shadow, no gradient — two ink values, one paper
                value, one accent.
              </p>
            </div>
            <div className="col-span-4 mt-comfortable md:col-span-3 md:col-start-10 md:mt-0 md:text-right">
              <EditorialCaption>PlantGeo</EditorialCaption>
              <EditorialCaption className="mt-hairline">
                Open-source 3D geospatial platform
              </EditorialCaption>
            </div>
          </EditorialGrid>
        </EditorialColophon>
      </EditorialContainer>
    </EditorialPage>
  );
}
