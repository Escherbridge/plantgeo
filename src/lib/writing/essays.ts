export interface EssaySource {
  label: string;
  href: string;
}

export interface Essay {
  title: string;
  slug: string;
  date: string;
  summary: string;
  tags: readonly string[];
  sources: readonly EssaySource[];
  paragraphs: readonly string[];
}

const MONTHS = [
  "January",
  "February",
  "March",
  "April",
  "May",
  "June",
  "July",
  "August",
  "September",
  "October",
  "November",
  "December",
] as const;

/** Calendar date from a `YYYY-MM-DD` frontmatter value, with no timezone shift. */
export function essayDisplayDate(iso: string): string {
  const [year, month, day] = iso.split("-");
  const monthName = MONTHS[Number(month) - 1];
  return `${Number(day)} ${monthName} ${year}`;
}

export const ESSAYS: readonly Essay[] = [
  {
    title: "What a muddy walk after autumn rain tells you about a five-acre parcel",
    slug: "walking-a-five-acre-parcel-after-rain",
    date: "2026-11-14",
    summary:
      "Dry summer grass hides half the hydraulics on a yard or small acreage. Walking the ground right after heavy rain—with bare-earth contours open on your screen—shows where water actually runs, pools, and freezes.",
    tags: ["field walk", "drainage", "frost pockets", "site planning"],
    sources: [
      { label: "USGS 3D Elevation Program", href: "https://www.usgs.gov/3d-elevation-program" },
      { label: "Ready.gov — Floods", href: "https://www.ready.gov/floods" },
      { label: "FEMA Flood Maps", href: "https://www.fema.gov/flood-maps" },
      {
        label: "Ada County Sample Plan",
        href: "https://plantgeo.aevani.com/plan/sample-ada-county",
      },
    ],
    paragraphs: [
      "A five-acre pasture or sloping half-acre looks forgiving in August. Everything is tan, tire ruts bake hard, and the gentle dip behind the barn looks like the sunniest place to grade a greenhouse pad or plant twenty bare-root apples. Then a three-day autumn soak arrives and tells you what the topography has been doing for decades.",
      "Before putting a stake in the ground or ordering orchard stock, pair two slow passes: one on the desktop map using bare-earth terrain and official flood boundaries, and one in rubber boots while runoff is still shiny in the wheel tracks.",
      "## Start with bare-earth contours before stepping outside",
      "Tree canopy hides small saddles, old irrigation ditches, and sheet-flow seams on satellite photos. Opening bare-earth elevation layers from the [USGS 3D Elevation Program](/guides/usgs-3dep) helps strip vegetation off the hillshade so you can spot subtle breaks in slope before walking the fence line.",
      "Keep [FEMA flood maps](/guides/fema-flood-maps) and [Ready.gov flood guidance](/guides/ready-floods) alongside that screen view, especially near creeks, dry coulees, or roadside ditches. Official community maps name regulated zones and Base Flood Elevations where studied, yet Ready.gov states the rule every field walk confirms: flooding doesn't follow lines on a map, and where it rains it can flood.",
      "## Five things to note while water is still on top of the ground",
      "- **Moving threads vs standing puddles:** Follow thin ribbons of cloudy runoff down the slope. Sheet flow carving a two-inch channel in bare gravel belongs on your sketch as an active snowmelt path, not just an aesthetic dip.",
      "- **Cold-air bowls and frost pockets:** Dense cold air behaves like water on clear, still nights. If a low hollow sits uphill from a thick blackberry hedge, rock wall, or solid timber fenceline, mark it as a frost pocket where spring blossoms burn weeks after an upper terrace stays safe.",
      "- **Compacted hardpan lanes:** Old equipment tracks, driveway aprons, and barnyard paths hold bright standing water hours after surrounding perennial sod has soaked clean. A post-rain walk shows compaction faster than a textbook.",
      "- **Road ditches and culvert pinch points:** Look just outside your own property line. Often a neighbor's graded crown or a plugged roadside culvert throws storm runoff diagonally across the cleanest corner of a parcel.",
      "- **Two-hour vs twenty-four-hour soak test:** Come back the next morning with dry chalk flags or stakes. Spots that drain within two hours act very differently in root zones than depressions still shining twenty-four hours later.",
      "## Put your walk onto a real workspace",
      "Once your mud notes exist, avoid relying on memory six months later when winter bare-root catalogs open. Inspect how terrain, drainage lines, soils, and flood edges stack together on the [Ada County sample plan](/plan/sample-ada-county), then [start your own site plan](/plan/new) to save your parcel notes under one private link.",
    ],
  },
  {
    title: "Reading winter sun, south slopes, and windbreaks before planting trees",
    slug: "sun-arc-aspect-and-windbreaks-for-orchards-and-beds",
    date: "2026-11-12",
    summary:
      "Flat paper layouts fail the moment December shade, afternoon slope heat, and winter winds hit real ground. Here is how to match aspect and shelterbelts to terrain contours without trapping cold air.",
    tags: ["microclimates", "sun angle", "windbreaks", "orchards"],
    sources: [
      {
        label: "USGS Topographic Maps",
        href: "https://www.usgs.gov/programs/national-geospatial-program/topographic-maps",
      },
      {
        label: "USGS The National Map",
        href: "https://www.usgs.gov/programs/national-geospatial-program/national-map",
      },
      { label: "Ready.gov — Wildfires", href: "https://www.ready.gov/wildfires" },
      {
        label: "Ada County Sample Plan",
        href: "https://plantgeo.aevani.com/plan/sample-ada-county",
      },
    ],
    paragraphs: [
      "Every gardener learns quickly that full sun in July can sit inside cold shade by November. On small rural lots and terraced urban yards alike, aspect—the compass direction a hillside tilts toward—often shifts bloom timing, soil thaw, and moisture loss by two or three weeks inside the same fence line.",
      "Combining contour shape from [USGS topographic maps](/guides/us-topo) and [The National Map](/guides/national-map) with midday shadow checks keeps tender perennials out of winter dead zones and protects young stock from wind scorch.",
      "## Why south-facing benches behave differently from flat bottomland",
      "A gentle south- or southeast-facing berm catches low winter sun squarely when surrounding flat pasture sits cold, drying muddy topsoil earlier for spring greens and forgiving late-harvest beds. West-facing slopes read the opposite way: cool at sunrise, then slammed by hot late-afternoon sun that stresses thin-barked fruit trees in dry August valleys.",
      "North-facing backsides hold moisture and winter snowpack longer. Instead of fighting them with heat-loving annuals, practitioners often turn north slopes into summer forage, shade berries, mushroom logs, or mulch crops while keeping warm mid-slope contours for stone fruit and cold frames.",
      "## Plant windbreaks to filter gusts without damming frost",
      "- **Stagger species instead of building a green wall:** A dense, unbroken row of conifers across the bottom of a slope blocks drying wind during the day, then pools sinking nighttime frost uphill right around orchard trunks.",
      "- **Leave a cold-air drain notch:** At the lowest swale outlet, leave breathing room under lower branches or step canopy height down so chilled air keeps sliding off the orchard floor into pasture or brush below.",
      "- **Respect home-clearance zones on treed lots:** Do not weave resinous windbreaks tight against houses, shops, or hay sheds. [Ready.gov wildfire guidance](/guides/ready-wildfires) advises keeping a fire-resistant zone free of leaves, debris, and flammable materials for at least 30 feet from the home, while neighborhood codes and Firewise rules carry their own separate zone widths on our [WUI parcel notes](/site-types/wui-parcel).",
      "## Test elevation and aspect lines side by side",
      "Turn terrain contours and official basemap layers on together inside the [Ada County sample plan](/plan/sample-ada-county) to trace where warm ridges break into shaded draws. When you are ready to map your own orchard rows and buffer lines, open [a new private site plan](/plan/new).",
    ],
  },
  {
    title: "Pairing soil maps and wetland layers with a shovel before digging",
    slug: "pairing-wetland-and-soil-maps-with-a-shovel-test",
    date: "2026-11-11",
    summary:
      "Public wetland inventories and county soil surveys narrow down where to walk first—never replace a shovel, a jar test, or site work. Here is how practitioners verify desktop boundaries in twenty minutes.",
    tags: ["soils", "wetlands", "site inspection", "drainage"],
    sources: [
      {
        label: "USDA NRCS Web Soil Survey",
        href: "https://websoilsurvey.nrcs.usda.gov/app/",
      },
      {
        label: "U.S. Fish and Wildlife Service — National Wetlands Inventory",
        href: "https://www.fws.gov/program/national-wetlands-inventory",
      },
      {
        label: "PlantGeo Homestead Soils Site Type",
        href: "https://plantgeo.aevani.com/site-types/homestead-mapped-soils",
      },
      {
        label: "Ada County Sample Plan",
        href: "https://plantgeo.aevani.com/plan/sample-ada-county",
      },
    ],
    paragraphs: [
      "A single pasture can hide gravel bar, silty loam, and heavy subsurface clay within two hundred paces. Opening the USDA NRCS Web Soil Survey alongside [National Wetlands Inventory maps](/guides/nwi-wetlands) gives you field-verified regional clues before renting a trencher or laying out perennial guilds.",
      "Both datasets share one practical boundary that county extension offices repeat constantly: soil map units contain mixed components at survey scale, and public wetland polygons record extent and change rather than granting or refusing a local permit. They tell you **where to bring a shovel**, not the final depth beneath your boot.",
      "## Three quick checks to bring from desktop layers to dirt",
      "- **Sample across map boundaries, not one lucky spot:** Pull one spade core on the high bench, one mid-slope, and one inside any mapped swale or wetland edge. Notice where topsoil color shifts abruptly or gravel starts ringing against the blade.",
      "- **Look for gray and rust-orange streaks:** Even bone-dry August clay gives itself away down at ten to eighteen inches. Pale gray soil dappled with bright rust flecks signals iron depletion from fluctuating groundwater—a warning before planting tap-rooted chestnuts or digging unlined root cellars.",
      "- **Run a real jar and hole percolation check:** Fill a shoebox-deep hole with water twice (once to saturate dry soil, once to time the fall), and shake topsoil in a clear jar with water and a drop of dish soap to see sand settle in minutes, silt in hours, and suspended clay overnight.",
      "## Let wet corners stay wet",
      "Trying to force Mediterranean herbs into a seasonal sedge corner burns years of mulch and trench work. Once soil units, wetland polygons, and real percolation timings line up, practitioners either step high beds above seasonal moisture or match low ground to willow, dogwood, sedge buffers, and stormwater swales.",
      "Explore how public wetlands and soils read side by side in the [Ada County sample plan](/plan/sample-ada-county), read our deeper breakdown of [homestead mapped soils](/site-types/homestead-mapped-soils), or [start your own site plan](/plan/new) to record your notes before breaking sod.",
    ],
  },
  {
    title: "Plan the land, then pick the tools",
    slug: "plan-the-land-then-pick-the-tools",
    date: "2026-11-09",
    summary:
      "Tools come after walking the terrain, reading drainage, and mapping the parcel. PlantGeo puts land planning first, links Aevani preview items as optional planning references (not for sale yet), and quotes no referral rate because none has been set.",
    tags: ["PlantGeo", "Aevani", "site planning", "affiliates"],
    sources: [
      { label: "About PlantGeo", href: "https://plantgeo.aevani.com/about" },
      { label: "Become a PlantGeo affiliate", href: "https://plantgeo.aevani.com/affiliate" },
      {
        label: "PlantGeo community — public strategy requests",
        href: "https://plantgeo.aevani.com/community",
      },
      { label: "Aevani homepage", href: "https://aevani.com" },
    ],
    paragraphs: [
      "Too many outdoor planning sites work backward: they hand you a shopping cart of tanks, raised beds, drip kits, and seed mixes before anyone has traced a contour line or looked at winter sun shadows. PlantGeo works from the ground up. Walk the terrain first, test the drainage and soil layers on the [Ada County sample plan](/plan/sample-ada-county), and build a site plan around what your yard or acreage actually does.",
      "When a designer, extension agent, master gardener, installer, or school-garden coordinator helps someone plan a parcel, the workflow stays grounded. An affiliate invites a landowner, grower, school, or homestead to plan a site on real public layers here on PlantGeo. Later, compatible growing gear and tools from [Aevani](https://aevani.com) can be referenced inside that plan—items intended to be sold by verified Aevani sellers, never a silent middleman marking up anonymous listings.",
      "## Staying transparent about what exists today",
      "Two present-tense boundaries keep the planning workspace honest instead of reading like a brochure. First, [Aevani's catalogue](https://aevani.com) is still a preview. Nothing in it is for sale yet, so planning references inside PlantGeo act strictly as layout placeholders while seller listings open.",
      "Second, no referral rate is being quoted because none has been set. When affiliate terms are established, any referral share will be paid from Aevani's own margin, strictly on completed retail sales, and never for recruiting. Applications are reviewed individually on the [affiliate page](/affiliate), which shares terms directly upon review without issuing instant codes or payout promises.",
      "Even on the [community strategy map](/community), transparency comes first: visitors can post site strategies—keyline, silvopasture, reforestation, and hedgerow windbreaks—and read the platform's strict publication guard whenever automated recommendations respond `validated strategy evidence not published`.",
      "That restraint serves working growers and landscape designers far better than fake carts or noisy pins. Read the land carefully first; the right tools follow naturally once your sun, slope, and water are mapped.",
    ],
  },
  {
    title: "Not forecast beyond today",
    slug: "not-forecast-beyond-today",
    date: "2026-11-09",
    summary:
      "PlantGeo publishes no forecasts. Historical moisture and weather series help you understand how ground behaved in past seasons, while the time slider stops clearly at today instead of dressing guesses as predictions.",
    tags: ["PlantGeo", "forecasts", "data honesty"],
    sources: [{ label: "About PlantGeo", href: "https://plantgeo.aevani.com/about" }],
    paragraphs: [
      "A map with a time slider looks as if it can tell you what happens next. When software lets a playhead scrub into next month, the usual habit is to fill empty days with smooth interpolated colors that look like certainty. PlantGeo refuses that shortcut: scrub past today and the interface tells you directly, not forecast beyond today.",
      "That rule keeps landowners, orchardists, and ecologists grounded in verified facts. PlantGeo publishes no forecasts. Every published layer carries a zero-day forecast horizon—nothing has produced a forecast row, and vacant forecasting tables in the database schema stay empty until real, validated models earn publication.",
      "## Past measurements are still essential for site walks",
      "Skipping unearned predictions does not make historical maps empty. Among its observed environmental series, the archive carries four years of daily soil moisture measurements at three depths across a 1,568-cell lattice, paired with observed weather and vegetation records stamped in the past. Those historical records help practitioners see how seasons dried out or saturated soils in earlier years without pretending to predict next spring.",
      "The same honesty governs automated strategy advice: recommendations about what to plant or build are refused with `validated strategy evidence not published` until reviewed evidence backs them. Paired with an open-source mapping stack (MapLibre, PMTiles, Martin, Valhalla, PostGIS, OpenStreetMap basemap data anchored on a Pacific Northwest default extract), PlantGeo leaves a clear boundary between measured land history and on-foot human design judgment.",
    ],
  },
];

export function essayBySlug(slug: string): Essay | undefined {
  return ESSAYS.find((essay) => essay.slug === slug);
}

export function essayPath(essay: Essay): string {
  return `/writing/${essay.slug}`;
}

/** Blog is the public URL for these essays. `/writing/[slug]` stays and uses the same canonical. */
export function blogPath(essay: Essay): string {
  return `/blog/${essay.slug}`;
}

export const PUBLISHED_MODULE_PATHS = [
  "/blog",
  "/news",
  "/site-types",
  "/guides",
  "/writing",
  ...ESSAYS.map((essay) => blogPath(essay)),
] as const;
