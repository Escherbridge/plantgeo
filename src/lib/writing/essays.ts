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
    title: "Not forecast beyond today",
    slug: "not-forecast-beyond-today",
    date: "2026-11-09",
    summary:
      "PlantGeo publishes no forecasts. The time slider still draws a future, and the warehouse still holds years of observations, and neither of those facts is a prediction. An empty map that means 'we do not know' is worth more than a full one that means 'we guessed.'",
    tags: ["PlantGeo", "forecasts", "data honesty"],
    sources: [{ label: "About PlantGeo", href: "https://plantgeo.aevani.com/about" }],
    paragraphs: [
      "A map with a time slider looks as if it can tell you what happens next. The handle travels into next week, the way a video playhead travels past the last frame, and the usual habit of software is to fill that space with a color. The color is almost always a guess that has been dressed as a layer. PlantGeo does not dress it. When you scrub past today, the map says, in those words, not forecast beyond today.",
      "That sentence is already on the About page, and it is the one this essay exists to keep from drifting. PlantGeo publishes no forecasts. Every layer reports a forecast horizon of zero days and an empty list of forecast variants. This is not a loading state, and it is not a feature flag waiting for a model to be switched on. Nothing has produced a forecast row.",
      'The reason the sentence has to be repeated is that the machinery is visible. The slider draws thirty days past today. The read model already distinguishes "not published" from "stale" from "not forecastable." The warehouse carries a complete forecasting schema — series, runs, models, values, quantiles, backtests, publications. Anyone who has shipped a data product will recognize the shape of a forecast pipeline that is "almost ready." It is not almost ready. A schema is not a prediction, and a vacant table is not a model that has been modestly hidden.',
      "What the warehouse does hold is observation. The About page is specific about that, and this essay will not inflate it: among other series, there are four years of daily soil moisture at three depths across a 1,568-cell lattice, plus weather and vegetation series behind it. Every one of those rows is marked observed, with a timestamp in the past. A long history is a good reason to be tempted. It is also the usual alibi for a forecast that has not been earned. Millions of rows of history are not a forecast. The zero horizon is correct even with the warehouse full.",
      "The future band on the slider is there so that the boundary of today is visible, instead of sitting against the right edge of the track. Scrubbing into it is allowed. Every layer is then supposed to answer with the reason it cannot serve that day — including that some events, like a fire detection, are not the kind of thing a forecast has an opinion about. That is a harder sentence than a smooth heatmap, and it is the one worth keeping.",
      "The same rule governs strategy. Recommendations about what to plant, where to intervene, or which treatment a parcel warrants are refused today with a specific code: validated strategy evidence not published. Partner supplier matching is inactive until there is reviewed directory data, an entitlement to use it, and consent to send a location outward. The refusal is not a vibe. It is a publication boundary. A model that cannot meet the quality policy simply has no publication for the map to read.",
      'This is the unglamorous half of "the whole map, unbundled." The glamorous half is the stack: MapLibre, PMTiles, Martin, Valhalla, PostGIS, a basemap compiled from OpenStreetMap, environmental layers attributed to the agencies that actually produce them. The unglamorous half is leaving a hole where a vendor map would be happy to interpolate. The default basemap is still a Pacific Northwest extract, because shipping one region properly beats shipping the planet badly. Widening it is a rebuild of an archive, not a claim that coverage has already arrived.',
      'When a producer does land, the About page already says what should happen: one constant in one file opens the horizon, and the public pages say what it forecasts and how well. Until then, the honest layer is the one that stops at today. An empty map that means "we do not know" is worth more than a full one that means "we guessed."',
    ],
  },
  {
    title: "Plan the land, then pick the tools",
    slug: "plan-the-land-then-pick-the-tools",
    date: "2026-11-09",
    summary:
      "PlantGeo is a map you can plan on, not a catalogue with a basemap behind it. Affiliates invite someone to plan a site here. Aevani items can be added to that plan later, once there are real sellers to sell them. No referral rate is being quoted, because none has been set.",
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
      "Most \"maps with shopping\" are shops first. A pin is a product, a parcel is a landing page, and the land is an illustration. PlantGeo is the other way around. It is a 3D geospatial platform with real data, assembled from open-source parts, and it is meant to be a place where a site can be planned before anyone is asked to buy a thing for it.",
      "The path is simple, and it is not a funnel with a rate attached. An affiliate — a designer, an extension agent, an installer, a school-garden coordinator, or someone in a related role — invites a person to plan a site on the live map. That person might be a landowner, a grower, a school, or a homestead. The plan lives on PlantGeo, against the layers that are actually published there. Later, catalogue items from [Aevani](https://aevani.com) can be picked for that plan, and those items are supposed to be sold by recruited Aevani sellers, not by a silent middle. Aevani is never presented as the seller of a brand it does not make.",
      "Two facts about the present tense have to sit next to that path, or it becomes a brochure. First, [Aevani's catalogue](https://aevani.com) is still a preview. Nothing in it is for sale yet. A plan may one day point at a listing; it cannot complete a purchase today, and it should not look as if it can. Second, the referral share that Aevani would pay a PlantGeo affiliate has not been set. When it is set, it will be paid from Aevani's own margin, only on completed retail sales, and never for recruiting. Until then, the affiliate page should do what it already mostly does: take an application, say that terms will be shared on review, and refuse to quote a rate or a payout date.",
      "The application does not grant access, and it does not issue a code. The sample plan on the site, if you come across one, is a demonstration on real map data. It is not a customer. That last sentence is already on the affiliate page. It should remain as hard to delete as the forecast refusal.",
      "None of this asks the map to become less of a map. The About page still has the job of explaining the stack, the cadences, the Pacific Northwest extract, and the things the platform will not say. Wildfire prevention teams, land stewards, ecologists, and agricultural partners are still the people the infrastructure is built for. Site planning does not replace that work; it is a way to stand on the same map and decide what to do with a particular piece of ground, without pretending that a strategy has been validated when the platform itself still answers `validated strategy evidence not published`.",
      "A request on the community map is the same kind of honesty in a smaller type. People can put a strategy request on the map — keyline, silvopasture, reforestation, and the rest — and a submitted request publishes immediately. A request is not a claim that an intervention has been built. Proposals that describe a drawn point or boundary wait for review. If those queues are empty, they are empty.",
      "The temptation, once invitations start, will be to make the map look inhabited: sample sellers, sample carts, a cheerful pin where a tank might go. Resist it. The land is real. The data has a cadence and a source. The shop is not ready. A plan that tells the truth about all three is already more useful than a storefront with a hillshade.",
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
  "/writing",
  ...ESSAYS.map((essay) => blogPath(essay)),
] as const;
