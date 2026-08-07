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

// `note` carries the refresh rate. Every cadence here is quoted from the job that
// actually sets it — the nine cronSchedule values under `infra/cron-<source>` — or
// from the upstream's own release cycle where no job polls it.
const attributionIndex = [
  {
    term: "Oregon OEM",
    description:
      "Fire evacuation areas, statewide. Oregon only: no government-run aggregator exists for Washington, Idaho or western Montana, and the one vendor feed that reaches them carries no timestamp we could honestly publish.",
    note: "Every 15 min",
  },
  {
    term: "USGS NWIS",
    description:
      "Instantaneous streamflow discharge from active stream gauges.",
    note: "Every 30 min",
  },
  {
    term: "Open-Meteo",
    description:
      "Current conditions — temperature, humidity, wind, precipitation — sampled across the coverage grid, plus the live reading behind a map click.",
    note: "Hourly",
  },
  {
    term: "NOAA NWS",
    description: "Ground-station observations from api.weather.gov.",
    note: "Hourly",
  },
  {
    term: "WFIGS",
    description:
      "Interagency wildland fire perimeters, current incidents, published by NIFC.",
    note: "Hourly",
  },
  {
    term: "NASA FIRMS",
    description:
      "Active fire detections from VIIRS and MODIS thermal anomalies.",
    note: "Every 3 hours",
  },
  {
    term: "Sentinel-2 L2A",
    description:
      "Red and near-infrared bands read from the Earth Search STAC catalogue on AWS, reduced to NDVI on a fixed 0.25° lattice anchored to the global origin.",
    note: "Daily, 05:00 UTC",
  },
  {
    term: "U.S. Drought Monitor",
    description:
      "Drought classification polygons from the National Drought Mitigation Center, USDA and NOAA.",
    note: "Weekly, Thursday",
  },
  {
    term: "ERA5-Land",
    description:
      "Reanalysis soil moisture at three depths, read through the Open-Meteo archive across a 1,568-cell Pacific Northwest lattice.",
    note: "Backfilled",
  },
  {
    term: "NASA POWER",
    description:
      "Daily weather and soil-wetness signals on a 0.5° grid, used where the reanalysis lane needs a second opinion.",
    note: "Backfilled",
  },
  {
    term: "MTBS",
    description:
      "Monitoring Trends in Burn Severity perimeters and severity classes, USGS and USDA Forest Service.",
    note: "Annual release",
  },
  {
    term: "USDA NRCS SSURGO",
    description:
      "Soil survey attributes for a queried point, via the Soil Data Access service.",
    note: "On request",
  },
  {
    term: "LANDFIRE",
    description:
      "Fuel model and existing vegetation type identified at a queried point.",
    note: "On request",
  },
  {
    term: "USGS NHDPlus HR",
    description: "High-resolution hydrography — flowlines and waterbodies.",
    note: "On request",
  },
  {
    term: "OpenStreetMap",
    description:
      "Every line and label on the basemap, compiled into a Protomaps PMTiles archive. ODbL.",
    note: "Rebuilt on demand",
  },
  {
    term: "NASA GIBS",
    description:
      "MODIS/Terra NDVI raster overlay, proxied first-party so attribution and caching stay ours.",
    note: "8-day composite",
  },
  {
    term: "Terrarium DEM",
    description:
      "Elevation tiles behind terrain exaggeration and hillshade, from the AWS Open Data registry.",
    note: "Static archive",
  },
  {
    term: "Esri World Imagery",
    description:
      "Satellite basemap. © Esri, Maxar, Earthstar Geographics — the one tile service in the stack we do not host.",
    note: "Served upstream",
  },
];

const modelIndex = [
  {
    term: "Input release",
    description:
      "The exact upstream release a run consumed, checksummed. A separate table records when every input was recorded, so a run cannot reach forward for a value that did not exist at issue time.",
    note: "Leakage guard",
  },
  {
    term: "Feature snapshot",
    description:
      "The features as computed, checksummed and stored rather than recomputed at read time. A feature that cannot be reproduced from its inputs is not servable.",
    note: "Checksummed",
  },
  {
    term: "Model",
    description:
      "Either sql_linear — a statistical baseline that lives in SQL and can be read — or ml, which is refused registration unless it names a stored artifact. Both carry a checksum of the code that produced them.",
    note: "Two kinds",
  },
  {
    term: "Training run",
    description:
      "The run that fit the model, its own code checksum alongside it, so a served number can be walked back to the commit that trained it.",
    note: "Checksummed",
  },
  {
    term: "Backtest",
    description:
      "MAE, RMSE, bias, MAPE, interval coverage, and skill against a naive baseline. NaN and infinity are rejected by the table itself, not by a downstream reader.",
    note: "Measured",
  },
  {
    term: "Quality policy",
    description:
      "Minimum training points, minimum backtest points, minimum coverage, ceilings on error and a floor on skill. A run that misses the active policy does not publish.",
    note: "Gate",
  },
  {
    term: "Publication receipt",
    description:
      "The signed end of the chain. Serving reads publications, never raw run output, so an unpublished run is invisible to the map by construction rather than by convention.",
    note: "Serving boundary",
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

        <EditorialSection
          index="06"
          title="Where the data comes from"
          id="attribution"
          className="pt-section"
        >
          <EditorialProse className="mb-roomy">
            <p>
              None of the environmental data on the map is ours. It belongs to
              the agencies and projects below, and the honest thing to publish
              alongside a layer is not just who made it but how old it is
              allowed to get. So each row names the upstream, what it feeds, and
              the interval at which we go back for more.
            </p>
            <p>
              The cadences in the right column are the real ones, read off the
              jobs that set them rather than off an intention. Nine scheduled
              jobs do the polling; a tenth runs hourly to repair geometry that
              arrived malformed. Where a row says <em>Backfilled</em>, the
              history was walked once and is not re-polled — the series is a
              closed window, not a live feed. Where it says{" "}
              <em>On request</em>, nothing is stored in advance: the upstream is
              queried for the point you clicked.
            </p>
            <p>
              A cadence is an upper bound on staleness, not a promise of change.
              Fire perimeters are re-read hourly whether or not a fire moved,
              and the drought job runs Thursdays because that is when the U.S.
              Drought Monitor publishes — polling it more often would only
              produce the same week again.
            </p>
          </EditorialProse>
          <EditorialDefinitionList items={attributionIndex} />
        </EditorialSection>

        <EditorialSection index="07" title="On forecasts" id="forecasts">
          <EditorialProse>
            <p>
              PlantGeo publishes no forecasts. Every layer reports a forecast
              horizon of zero days and an empty list of forecast variants, and
              the map says so in those words when you scrub past today:{" "}
              <em>not forecast beyond today</em>. That is not a loading state or
              a gap in coverage. It is the true answer.
            </p>
            <p>
              This is worth stating plainly because the machinery is visibly
              there. The time slider draws thirty days past today, the read
              model distinguishes <em>not published</em> from <em>stale</em>{" "}
              from <em>not forecastable</em>, and the warehouse carries a
              complete forecasting schema — series, runs, models, values,
              quantiles, backtests, publications. Any of that could be mistaken
              for a forecast capability that is merely switched off. It is not
              switched off. Nothing has produced a forecast row.
            </p>
            <p>
              What the warehouse does hold is observation, and a great deal of
              it: four years of daily soil moisture at three depths across a
              1,568-cell lattice, plus weather and vegetation series behind it.
              Every one of those rows is marked observed, with a timestamp in
              the past. Millions of rows of history is not a forecast, and the
              zero horizon is correct even with the warehouse full.
            </p>
            <p>
              The future band on the slider exists so the boundary of today
              lands somewhere visible instead of at the right edge of the track.
              Scrubbing into it is allowed, and every layer answers with the
              reason it cannot serve that day — including that events, like fire
              detections, are not the kind of thing a forecast has an opinion
              about. When a producer does land, one constant in one file opens
              the horizon, and these pages will say what it forecasts and how
              well.
            </p>
          </EditorialProse>

          <EditorialPullQuote className="mt-roomy">
            An empty map that means &ldquo;we do not know&rdquo; is worth more
            than a full one that means &ldquo;we guessed.&rdquo;
          </EditorialPullQuote>
        </EditorialSection>

        <EditorialSection index="08" title="Models and strategy" id="models">
          <EditorialProse className="mb-roomy">
            <p>
              The same rule governs the model layer. Strategy recommendations —
              what to plant, where to intervene, which treatment a parcel
              actually warrants — are refused today with a specific code rather
              than answered with a plausible list:{" "}
              <em>validated strategy evidence not published</em>. Partner
              supplier matching is likewise inactive until there is reviewed
              directory data, an entitlement to use it, and consent to send a
              location outward.
            </p>
            <p>
              Refusing is the easy part. The reason it can be lifted safely is
              the chain below, which every served number has to walk before the
              application will read it. Two kinds of model are allowed —
              a statistical baseline expressed in SQL, and a trained artefact —
              and they are held to the same standard: a machine-learned model
              cannot even be registered without a stored artefact attached, and
              cannot serve a daily aggregate unless its series has explicitly
              opted in.
            </p>
            <p>
              Models are also the place where a subtle failure does the most
              damage, so the guards are structural rather than procedural. The
              leakage guard is a table, the error ceilings are constraints, and
              a run that misses the active quality policy simply has no
              publication for the map to read. A model that is quietly wrong
              should be unable to reach you without someone deliberately
              changing a rule — and that change should be legible in the diff.
            </p>
          </EditorialProse>
          <EditorialDefinitionList items={modelIndex} />
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
