/**
 * Site-type copy from the reviewed drafts. The sentences are the draft text.
 * The drafts' JSON-LD notes are structured data on the page, not a visible section.
 */

export const SITE_TYPE_AUTHOR = "Escherbridge";

export interface SiteTypeCitation {
  /** Citation line as written in the draft. */
  text: string;
  name: string;
  url: string;
  datePublished?: string;
  dateModified?: string;
  sameAs?: readonly string[];
}

export type SiteTypeBlock =
  | { type: "paragraph"; text: string }
  | { type: "heading"; text: string }
  | { type: "list"; items: readonly string[] }
  | { type: "sources" };

export interface SiteTypeIndex {
  h1: string;
  seoTitle: string;
  metaDescription: string;
  eyebrow: string;
  publishedAt: string;
  lastReviewed: string;
  blocks: readonly SiteTypeBlock[];
}

export interface SiteTypeArticle extends SiteTypeIndex {
  slug: string;
  crumb: string;
  sources: readonly SiteTypeCitation[];
}

export const SITE_TYPE_INDEX: SiteTypeIndex = {
  h1: "The whole map, with the grain left on.",
  seoTitle: "Site types | PlantGeo",
  metaDescription: "Site-type guides for land a planner actually maps: WUI parcels, riparian edges, and homestead soils. Official layers, no forecasts, no agency-deployment claims.",
  eyebrow: "Three site types · a map class is not a plan",
  publishedAt: "2026-11-10",
  lastReviewed: "2026-11-10",
  blocks: [
    {
      type: "paragraph",
      text: "These pages are about kinds of land a planner actually opens a map for: a wildland–urban interface parcel, a riparian or watershed edge, and a homestead on mapped soils. Each one starts from an official layer — a WUI class, a hydrography feature, a soil map unit — and stays inside what that layer can honestly say. The figures are other people’s, with the links left on. PlantGeo publishes no forecasts and claims no agency deployments. The [Ada County plan](https://plantgeo.aevani.com/plan/sample-ada-county) is a sample on live public data, not a client.",
    },
    {
      type: "paragraph",
      text: "The shared failure is the same in three costumes: treating a national percentage as a parcel score, a blue line as a restoration recipe, or a colorful polygon as a lab report. An empty map that means “we do not know” is still worth more than a full one that means “we guessed.”",
    },
    {
      type: "heading",
      text: "The first three",
    },
    {
      type: "list",
      items: [
        "**[WUI parcels](/site-types/wui-parcel)** — As of 2020, 9.4 percent of CONUS land and more than 44 million homes sit in the WUI (USDA Forest Service). The interface is a map class, not a forecast.",
        "**[Riparian and watershed edges](/site-types/riparian-watershed-edges)** — High-resolution NHD was mapped at 1:24,000 or larger, and the dataset was retired on 1 October 2023 (USGS). The blue line is a mapped feature, not a restoration plan.",
        "**[Homestead, mapped soils](/site-types/homestead-mapped-soils)** — Web Soil Survey covers more than 95 percent of U.S. counties and still tells you when to walk the site (NRCS). A map unit is not a soil test.",
      ],
    },
    {
      type: "heading",
      text: "What PlantGeo is doing here",
    },
    {
      type: "paragraph",
      text: "The map is the product, and [About](https://plantgeo.aevani.com/about) already says how the layers are framed. House notes: [Not forecast beyond today](https://plantgeo.aevani.com/blog/not-forecast-beyond-today) and [Plan the land, then pick the tools](https://plantgeo.aevani.com/blog/plan-the-land-then-pick-the-tools). If tools come after the plan, they come from [Aevani’s catalogue](https://aevani.com), which is not for sale yet. Official-layer how-tos live in the [field guides](/guides). Site notes, when there are any, live in [News](/news).",
    },
    {
      type: "paragraph",
      text: "Author: Escherbridge. Last reviewed: 2026-11-10, America/Denver.",
    },
  ],
};

export const SITE_TYPES: readonly SiteTypeArticle[] = [
  {
    h1: "The interface is a map class, not a forecast",
    seoTitle: "WUI parcels — Site types | PlantGeo",
    metaDescription: "A WUI parcel can be planned from the official map definition and a public ignition-zone standard, not from a fire forecast or a Firewise badge.",
    eyebrow: "Wildland–urban interface",
    slug: "wui-parcel",
    crumb: "WUI parcels",
    publishedAt: "2026-11-10",
    lastReviewed: "2026-11-10",
    blocks: [
      {
        type: "paragraph",
        text: "The federal definition is already in a 2001 notice: the urban wildland interface community exists where humans and their development meet or intermix with wildland fuel. The Forest Service’s 1990–2020 map operationalizes that definition for the conterminous United States. PlantGeo can show the overlay, and it cannot tell you what will burn. A WUI parcel can be planned from that official map class and a public ignition-zone standard. This page will not predict a fire, and it will not award a Firewise badge.",
      },
      {
        type: "paragraph",
        text: "The figures on this page are other people’s, with the links left on. Ada County appears only as a sample geography, not as a client.",
      },
      {
        type: "heading",
        text: "Hero figures",
      },
      {
        type: "list",
        items: [
          "**As of 2020, 9.4 percent** of the land area of the contiguous United States is in the WUI, and that land is home to **more than 44 million homes — 32 percent of all housing nationwide** (USDA Forest Service, Northern Research Station, “Wildland-Urban Interface Growth in the U.S.”).",
          "**Between 1990 and 2020**, the number of homes in the WUI rose **47 percent** and WUI acreage rose **31 percent**; the United States added 179,000 square kilometers of WUI, an area the source compares to the state of Washington. Expansion slowed after 2008, but 2.6 million homes were still built in the WUI between 2010 and 2020. These are national, conterminous-U.S. figures, not an Ada County count and not a prediction.",
          "**Immediate Zone 0 to 5 feet**, Intermediate Zone 5 to 30 feet, and Extended Zone 30 to 100 feet around the house, from NFPA’s Firewise fact sheet “How to Prepare Your Home for Wildfires,” labeled here as a public standard, not a PlantGeo rating.",
        ],
      },
      {
        type: "heading",
        text: "The bill for a split system",
      },
      {
        type: "paragraph",
        text: "**A national percentage is not your parcel.** The accompanying geospatial publication is the 4th edition of “The 1990-2020 wildland-urban interface of the conterminous United States,” published 9 August 2023, with minor metadata updates on 16 September 2024 (Radeloff et al.). It maps the Federal Register definition of WUI (Federal Register 66:751, 2001) by integrating U.S. Census and USGS National Land Cover Data for 1990, 2000, 2010, and 2020. Coverage is the conterminous United States only. No data were generated for Alaska, Hawaii, or U.S. territories. The [Ada County sample plan](https://plantgeo.aevani.com/plan/sample-ada-county) is a walkthrough of live public data, not a measured local share and not a customer.",
      },
      {
        type: "paragraph",
        text: "**Interface and intermix are different.** The official govinfo PDF of the 4 January 2001 Federal Register notice (Vol. 66, No. 3, starting at page 751) distinguishes three categories. An Interface Community is where structures directly abut wildland fuels, usually at a density of 3 or more structures per acre, with a clear line of demarcation. An Intermix Community is where structures are scattered throughout a wildland area, with no clear line of demarcation and wildland fuels continuous outside and within the developed area; density ranges from structures very close together to one structure per 40 acres. An Occluded Community is a smaller inclusion, usually less than 1,000 acres. Collapsing those into a single “in the WUI” label erases the definition the map is using.",
      },
      {
        type: "paragraph",
        text: "**A zone diagram is not a rating, a code, or a badge.** The Firewise fact sheet divides the home ignition zone into those three bands and states that driveways should be at least 12 feet wide with a vertical clearance of 15 feet for emergency vehicle access. It was produced in cooperation with the USDA Forest Service, the U.S. Department of the Interior, and the National Association of State Foresters. It is a public educational standard, not a PlantGeo endorsement, a certification, or a local code. A related Firewise / IBHS fact sheet on the Immediate (Noncombustible) Zone states that changes within 100 feet can make a big impact, and that the first 0 to 5 feet around the structure “has the greatest impact on your risk.” It reports that more than 180 tests were conducted in 2018 at the IBHS Research Center, and that when flames are 5 feet away, a building’s surface temperature is below temperatures that could cause ignition, with the caveat that corners remain more vulnerable. Mitigation “shouldn’t stop at 5 feet” and “should be combined with the footprint of an attached deck and area that extends away from the building up to 100 feet or to the property line.” The 180-test figure belongs to that fact sheet. PlantGeo did not run or validate the tests.",
      },
      {
        type: "heading",
        text: "A shared record, applied here",
      },
      {
        type: "paragraph",
        text: "**Read the official WUI class and name the map vintage.** Interface, intermix, or neither, from the 1990–2020 / 4th edition 2023 dataset. A national percentage is context, not a parcel score.",
      },
      {
        type: "paragraph",
        text: "**Walk the 0–5 / 5–30 / 30–100 foot bands on the parcel you actually own,** including the property-line limit in the Immediate Zone fact sheet. The diagram is a measuring tape, not a certificate.",
      },
      {
        type: "paragraph",
        text: "**Talk to the local fire department or forestry agency,** as the Firewise sheet itself tells the reader to do, for the risk where they live — not to PlantGeo for a forecast.",
      },
      {
        type: "heading",
        text: "What PlantGeo is, and is not, on this page",
      },
      {
        type: "paragraph",
        text: "The tool shows today’s public layers. It publishes no forecasts and no validated strategy recommendations, and it claims no agency deployments. The Ada County plan is a sample, not a customer. The house limit is the one already written in [Not forecast beyond today](https://plantgeo.aevani.com/blog/not-forecast-beyond-today): the map class is from 2020, the standard is public, and tomorrow’s fire is not on the page.",
      },
      {
        type: "paragraph",
        text: "If tools come after the plan, they come from [Aevani’s catalogue](https://aevani.com), which is not for sale yet. The [affiliate page](https://plantgeo.aevani.com/affiliate) is a hold, and no referral share is quoted because the share has not been set.",
      },
      {
        type: "heading",
        text: "Sources",
      },
      {
        type: "paragraph",
        text: "Every stat on this page is cited.",
      },
      {
        type: "sources",
      },
      {
        type: "paragraph",
        text: "USGS National Land Cover Data is named as an input on the official USFS metadata page. No NLCD figure that was not opened on an NLCD page is used here.",
      },
      {
        type: "heading",
        text: "An illustrative scenario",
      },
      {
        type: "paragraph",
        text: "This is a worked example, not a client, and not a predicted fire. A foothills acre at the edge of Boise has a mapped intermix class and a five-foot gravel skirt. The map can say intermix, and the standard can name the first five feet. Neither one is a forecast of what will happen next summer.",
      },
      {
        type: "heading",
        text: "Also in this section",
      },
      {
        type: "list",
        items: [
          "[Riparian and watershed edges](/site-types/riparian-watershed-edges) — the blue line is a mapped feature, not a restoration plan.",
          "[Homestead, mapped soils](/site-types/homestead-mapped-soils) — a map unit is not a soil test.",
        ],
      },
      {
        type: "paragraph",
        text: "Related: [About](https://plantgeo.aevani.com/about), the [Ada County sample plan](https://plantgeo.aevani.com/plan/sample-ada-county), [Not forecast beyond today](https://plantgeo.aevani.com/blog/not-forecast-beyond-today), [Plan the land, then pick the tools](https://plantgeo.aevani.com/blog/plan-the-land-then-pick-the-tools), and Aevani’s [growing guides](https://aevani.com/guides), always with the catalogue not for sale yet.",
      },
      {
        type: "paragraph",
        text: "The useful next step is to [open the map](https://plantgeo.aevani.com), read [About](https://plantgeo.aevani.com/about), or walk the [sample plan](https://plantgeo.aevani.com/plan/sample-ada-county). PlantGeo will not turn the overlay into a parcel score or a forecast.",
      },
      {
        type: "paragraph",
        text: "Author: Escherbridge. Last reviewed: 2026-11-10, America/Denver.",
      },
    ],
    sources: [
      {
        text: "USDA Forest Service, Northern Research Station, “Wildland-Urban Interface Growth in the U.S.” https://research.fs.usda.gov/nrs/projects/wuigrowth",
        name: "USDA Forest Service, Northern Research Station, “Wildland-Urban Interface Growth in the U.S.”",
        url: "https://research.fs.usda.gov/nrs/projects/wuigrowth",
      },
      {
        text: "Radeloff, V.C.; Helmers, D.P.; Mockrin, M.H.; Carlson, A.R.; Hawbaker, T.J.; Martinuzzi, S. 2023. “The 1990-2020 wildland-urban interface of the conterminous United States” (4th edition), Forest Service Research Data Archive. Published 9 August 2023. https://www.fs.usda.gov/rds/archive/catalog/RDS-2015-0012-4 — DOI: https://doi.org/10.2737/RDS-2015-0012-4",
        name: "Radeloff, V.C.; Helmers, D.P.; Mockrin, M.H.; Carlson, A.R.; Hawbaker, T.J.; Martinuzzi, S. 2023. “The 1990-2020 wildland-urban interface of the conterminous United States” (4th edition), Forest Service Research Data Archive. Published 9 August 2023.",
        url: "https://www.fs.usda.gov/rds/archive/catalog/RDS-2015-0012-4",
        sameAs: [
          "https://doi.org/10.2737/RDS-2015-0012-4",
        ],
        datePublished: "2023-08-09",
      },
      {
        text: "Same dataset, official metadata (published 9 August 2023; metadata updates 16 September 2024). https://www.fs.usda.gov/rds/archive/products/RDS-2015-0012-4/_metadata_RDS-2015-0012-4.html",
        name: "Same dataset, official metadata (published 9 August 2023; metadata updates 16 September 2024).",
        url: "https://www.fs.usda.gov/rds/archive/products/RDS-2015-0012-4/_metadata_RDS-2015-0012-4.html",
        datePublished: "2023-08-09",
        dateModified: "2024-09-16",
      },
      {
        text: "66 Fed. Reg. 751 (January 4, 2001), official govinfo PDF. https://www.govinfo.gov/content/pkg/FR-2001-01-04/pdf/01-52.pdf",
        name: "66 Fed. Reg. 751 (January 4, 2001), official govinfo PDF.",
        url: "https://www.govinfo.gov/content/pkg/FR-2001-01-04/pdf/01-52.pdf",
        datePublished: "2001-01-04",
      },
      {
        text: "National Fire Protection Association, Firewise, “How to Prepare Your Home for Wildfires” (PDF metadata June 2020). https://www.nfpa.org/-/media/Project/Storefront/Catalog/Files/Firewise/Fact-sheets/FirewiseHowToPrepareYourHomeForWildfires.pdf",
        name: "National Fire Protection Association, Firewise, “How to Prepare Your Home for Wildfires” (PDF metadata June 2020).",
        url: "https://www.nfpa.org/-/media/Project/Storefront/Catalog/Files/Firewise/Fact-sheets/FirewiseHowToPrepareYourHomeForWildfires.pdf",
        datePublished: "2020-06",
      },
      {
        text: "NFPA Firewise / Insurance Institute for Business & Home Safety, “Immediate (Noncombustible) Zone” (PDF metadata 26 September 2019). https://www.nfpa.org/-/media/Project/Storefront/Catalog/Files/Firewise/Fact-sheets/FirewiseFactSheetsImmediateNoncombustibleZone.pdf",
        name: "NFPA Firewise / Insurance Institute for Business & Home Safety, “Immediate (Noncombustible) Zone” (PDF metadata 26 September 2019).",
        url: "https://www.nfpa.org/-/media/Project/Storefront/Catalog/Files/Firewise/Fact-sheets/FirewiseFactSheetsImmediateNoncombustibleZone.pdf",
        datePublished: "2019-09-26",
      },
      {
        text: "NFPA, “Learn About the NFPA Firewise USA Program” — the program home, not a source of extracted body metrics on this page. https://www.nfpa.org/education-and-research/wildfire/firewise-usa",
        name: "NFPA, “Learn About the NFPA Firewise USA Program” — the program home, not a source of extracted body metrics on this page.",
        url: "https://www.nfpa.org/education-and-research/wildfire/firewise-usa",
      },
    ],
  },
  {
    h1: "The blue line is a mapped feature, not a restoration plan",
    seoTitle: "Riparian edges — Site types | PlantGeo",
    metaDescription: "A hydrography layer shows streams, canals, and lakes at a stated scale. It cannot tell you what to plant, how wide a buffer should be, or if a reach is healthy.",
    eyebrow: "Riparian and watershed edges",
    slug: "riparian-watershed-edges",
    crumb: "Riparian and watershed edges",
    publishedAt: "2026-11-10",
    lastReviewed: "2026-11-10",
    blocks: [
      {
        type: "paragraph",
        text: "Blue lines on a national dataset are features with a scale and a vintage. A hydrography layer can show where streams, canals, lakes, and watershed features have been mapped, and at what scale. It cannot tell you what to plant, how wide a buffer should be, or whether a reach is healthy. In irrigation country, a canal or ditch is a first-class National Hydrography Dataset feature, not a scenic creek by default. The NHD’s retirement on 1 October 2023 belongs on the first screen, not in a footnote.",
      },
      {
        type: "paragraph",
        text: "The figures on this page are other people’s, with the links left on.",
      },
      {
        type: "heading",
        text: "Hero figures",
      },
      {
        type: "list",
        items: [
          "**High-resolution NHD was mapped at 1:24,000 or larger scale** (1:63,360 or larger scale in Alaska) (USGS, National Hydrography Dataset program page).",
          "**Legacy medium-resolution NHD**, compiled at 1:100,000 for the conterminous United States: at that scale, **ninety percent of well-defined features are within 167 feet** (0.02 inches at map scale) of their true geographic position (USGS National Hydrography FAQs). That foot-figure is the medium-resolution figure only. A 1:24,000 foot-figure was not given on the opened pages, and is not invented here.",
          "**As of 1 October 2023, the NHD was retired.** “NHD data will continue to be available, but no longer maintained. The most current data will be available through the 3D Hydrography Program (3DHP).”",
        ],
      },
      {
        type: "heading",
        text: "The bill for a split system",
      },
      {
        type: "paragraph",
        text: "**A line is not a buffer, a setback, or a restoration recipe.** EPA’s *Handbook for Developing Watershed Plans to Restore and Protect Our Waters* (EPA 841-B-08-002, March 2008) is guidance, not a regulation. The official disclaimer states that the document “does not impose legally binding requirements on EPA, states, territories, authorized tribes, local governments, watershed organizations, or the public,” and that words such as “should,” “recommend,” and “can” are suggestions, not legal duties. One of the handbook’s minimum elements for certain Section 319-funded plans says the description of management measures and critical areas “can be greatly enhanced by identifying on a map priority areas and practices.” That is the right relationship: a map can support a plan; a map is not a plan, and this page will not write one, or invent a buffer width. The 2008 handbook’s older impairment statistics are not reused here as if they were current.",
      },
      {
        type: "paragraph",
        text: "**Not every water feature is a natural stream.** The NHD “represents the water drainage network of the United States with features such as rivers, streams, canals, lakes, ponds, coastline, dams, and streamgages.” NHDFlowline is the fundamental flow network (predominantly stream/river and artificial path features) and carries linear referencing for events on the network; additional flowline types include canal/ditch, pipeline, connector, underground conduit, and coastline. NHDWaterbody represents lake/pond and also swamp/marsh, reservoir, playa, estuary, and ice mass. Wide rivers may appear as area features with an artificial path through them. That inventory is what a map can show. In the Treasure Valley, a canal drawn as if it were a creek is a basemap habit, not a field diagnosis.",
      },
      {
        type: "paragraph",
        text: "**Retired is not current.** If the product still draws NHD, the data are available but no longer maintained. USGS describes 3DHP as “the first systematic remapping of the Nation’s hydrography since the original USGS 1:24,000-scale topographic mapping program was active,” and elsewhere as the first systematic remapping of the Nation’s surface waters since that topographic program “was active from 1947 to 1992.” This page does not claim that a local watershed has been remapped, or that PlantGeo’s layer is the maintained 3DHP dataset.",
      },
      {
        type: "paragraph",
        text: "The official FAQ also says that original hydrographic data were compiled to meet National Map Accuracy standards, and that when higher-resolution data are created and submitted, “the positional accuracy may vary.” Watershed boundaries are mentioned here only at the level the opened NHD pages support: NHD downloads include the Watershed Boundary Dataset in a second feature dataset, and a reach code’s first eight digits are the WBD HUC8. No WBD-only coverage percentages are added; the dedicated WBD program page was not opened.",
      },
      {
        type: "heading",
        text: "A shared record, applied here",
      },
      {
        type: "paragraph",
        text: "**Identify which mapped features actually touch the parcel** — stream/river, canal/ditch, lake/pond, swamp/marsh — and at which resolution the data were compiled.",
      },
      {
        type: "paragraph",
        text: "**Use the flow network to see upstream and downstream context, not to declare water quality.** NHD is not a chemistry layer, and this page will not make a beneficial-use determination.",
      },
      {
        type: "paragraph",
        text: "**If someone wants a watershed plan, point them at EPA’s handbook and Watershed Academy as public guidance — and at their state or district — not at a PlantGeo prescription.** EPA’s Healthy Watersheds Protection page describes a landscape approach to aquatic ecological integrity; it is a public pointer, not a PlantGeo method.",
      },
      {
        type: "heading",
        text: "What PlantGeo is, and is not, on this page",
      },
      {
        type: "paragraph",
        text: "The tool can explain the layer, and it will not prescribe restoration, a planting list, or a setback. The [Ada County sample plan](https://plantgeo.aevani.com/plan/sample-ada-county) is not a customer. The voice limit is the one in [Not forecast beyond today](https://plantgeo.aevani.com/blog/not-forecast-beyond-today): this is a map of features, not of next spring’s high water. If the [Aevani catalogue](https://aevani.com) is mentioned, it is not for sale yet.",
      },
      {
        type: "heading",
        text: "Sources",
      },
      {
        type: "paragraph",
        text: "Every stat on this page is cited.",
      },
      {
        type: "sources",
      },
      {
        type: "heading",
        text: "An illustrative scenario",
      },
      {
        type: "paragraph",
        text: "This is a worked example, not a client, and not a planting plan. A small parcel along an irrigation lateral looks like a creek on a basemap and is coded canal/ditch in NHD. The useful surprise is the feature type, not a restoration appeal.",
      },
      {
        type: "heading",
        text: "Also in this section",
      },
      {
        type: "list",
        items: [
          "[WUI parcels](/site-types/wui-parcel) — the interface is a map class, not a forecast.",
          "[Homestead, mapped soils](/site-types/homestead-mapped-soils) — a map unit is not a soil test.",
        ],
      },
      {
        type: "paragraph",
        text: "Related: [About](https://plantgeo.aevani.com/about), the [Ada County sample plan](https://plantgeo.aevani.com/plan/sample-ada-county), [Not forecast beyond today](https://plantgeo.aevani.com/blog/not-forecast-beyond-today), [Plan the land, then pick the tools](https://plantgeo.aevani.com/blog/plan-the-land-then-pick-the-tools), and Aevani’s [Understanding soil health](https://aevani.com/guides/understanding-soil-health), with the catalogue not for sale yet.",
      },
      {
        type: "paragraph",
        text: "The useful next step is to [open the map](https://plantgeo.aevani.com) or walk the [sample plan](https://plantgeo.aevani.com/plan/sample-ada-county). This page will not invent a buffer width or a planting plan.",
      },
      {
        type: "paragraph",
        text: "Author: Escherbridge. Last reviewed: 2026-11-10, America/Denver.",
      },
    ],
    sources: [
      {
        text: "U.S. Geological Survey, National Hydrography Dataset program page (features, scale, 1 October 2023 retirement). https://www.usgs.gov/national-hydrography/national-hydrography-dataset",
        name: "U.S. Geological Survey, National Hydrography Dataset program page (features, scale, 1 October 2023 retirement).",
        url: "https://www.usgs.gov/national-hydrography/national-hydrography-dataset",
      },
      {
        text: "USGS, National Hydrography FAQs (1:100,000 positional accuracy of 167 feet for 90% of well-defined features). https://www.usgs.gov/national-hydrography/science/faqs",
        name: "USGS, National Hydrography FAQs (1:100,000 positional accuracy of 167 feet for 90% of well-defined features).",
        url: "https://www.usgs.gov/national-hydrography/science/faqs",
      },
      {
        text: "USGS, NHD Data Dictionary — Feature Classes (NHDFlowline, reach codes, resolution domain). https://www.usgs.gov/ngp-standards-and-specifications/national-hydrography-dataset-nhd-data-dictionary-feature-classes",
        name: "USGS, NHD Data Dictionary — Feature Classes (NHDFlowline, reach codes, resolution domain).",
        url: "https://www.usgs.gov/ngp-standards-and-specifications/national-hydrography-dataset-nhd-data-dictionary-feature-classes",
      },
      {
        text: "USGS, 3D Hydrography Program (current maintained path; remapping context 1947–1992). https://www.usgs.gov/3d-hydrography-program",
        name: "USGS, 3D Hydrography Program (current maintained path; remapping context 1947–1992).",
        url: "https://www.usgs.gov/3d-hydrography-program",
      },
      {
        text: "U.S. Environmental Protection Agency, *Handbook for Developing Watershed Plans to Restore and Protect Our Waters,* March 2008, EPA 841-B-08-002 (full PDF). https://www.epa.gov/sites/default/files/2015-09/documents/2008_04_18_nps_watershed_handbook_handbook-2.pdf",
        name: "U.S. Environmental Protection Agency, Handbook for Developing Watershed Plans to Restore and Protect Our Waters, March 2008, EPA 841-B-08-002 (full PDF).",
        url: "https://www.epa.gov/sites/default/files/2015-09/documents/2008_04_18_nps_watershed_handbook_handbook-2.pdf",
        datePublished: "2008-03",
      },
      {
        text: "Same handbook, official landing page. https://www.epa.gov/nps/handbook-developing-watershed-plans-restore-and-protect-our-waters",
        name: "Same handbook, official landing page.",
        url: "https://www.epa.gov/nps/handbook-developing-watershed-plans-restore-and-protect-our-waters",
        datePublished: "2008-03",
      },
      {
        text: "EPA, Healthy Watersheds Protection (last updated on the opened page 19 October 2026). https://www.epa.gov/hwp",
        name: "EPA, Healthy Watersheds Protection (last updated on the opened page 19 October 2026).",
        url: "https://www.epa.gov/hwp",
        dateModified: "2026-10-19",
      },
      {
        text: "EPA, Watershed Academy (last updated on the opened page 26 October 2026). https://www.epa.gov/watershedacademy",
        name: "EPA, Watershed Academy (last updated on the opened page 26 October 2026).",
        url: "https://www.epa.gov/watershedacademy",
        dateModified: "2026-10-26",
      },
    ],
  },
  {
    h1: "A map unit is not a soil test",
    seoTitle: "Homestead, mapped soils — Site types | PlantGeo",
    metaDescription: "Web Soil Survey shows field-verified map units, often mixed, at the survey’s own scale. It cannot replace an on-site investigation. Ada County is a sample.",
    eyebrow: "Homestead / small parcel",
    slug: "homestead-mapped-soils",
    crumb: "Homestead, mapped soils",
    publishedAt: "2026-11-10",
    lastReviewed: "2026-11-10",
    blocks: [
      {
        type: "paragraph",
        text: "Web Soil Survey can show the field-verified soil map units that cover a small parcel, at the scale of that survey and with mixed components inside each unit. It cannot replace an on-site investigation. The agency that published the map already says so, and that sentence belongs in the hero, not in a footnote. The [Ada County walkthrough](https://plantgeo.aevani.com/plan/sample-ada-county) is a sample on live public data, not a client.",
      },
      {
        type: "paragraph",
        text: "The figures on this page are other people’s, with the links left on. No Ada County soil-series names, acreages, or yields are invented here; those require an opened survey for a stated area of interest.",
      },
      {
        type: "heading",
        text: "Hero figures",
      },
      {
        type: "list",
        items: [
          "**“NRCS has soil maps and data available online for more than 95 percent of the nation’s counties** and anticipates having 100 percent in the near future.” Web Soil Survey “is updated and maintained online as the single authoritative source of soil survey information” (USDA NRCS, Web Soil Survey home).",
          "**“Onsite investigation is needed in some cases, such as soil quality assessments and certain conservation and engineering applications.”** Same official home page. That limit is the point of this guide.",
          "**Map detail varies by survey area.** Official ISO metadata for SSURGO (revision 2 July 2026) records many representative-fraction denominators, including 8,000; 12,000; 15,000; 15,840; 20,000; 24,000; 25,000; 31,680; 42,240; 48,000; 50,000; 62,500; 63,000; 63,360; 125,000; and 250,000. Do not assume a homestead is mapped at 1:24,000 unless that survey’s own scale has been opened. Ada County’s map-unit list and scale were not pulled from Web Soil Survey for this page, and are not invented.",
        ],
      },
      {
        type: "heading",
        text: "The bill for a split system",
      },
      {
        type: "paragraph",
        text: "**The polygon is not a point.** SSURGO “is a digital soil survey and generally is the most detailed level of soil geographic data developed by the National Cooperative Soil Survey.” Official data.gov metadata (published by NRCS; dataset last updated 2 July 2026) states that the map data “include a detailed, field verified inventory of soils and miscellaneous areas that normally occur in a repeatable pattern on the landscape and that can be cartographically shown at the scale mapped.” Map units “are linked to attributes in the National Soil Information System relational database, which gives the proportionate extent of the component soils and their properties.” In plain language: a unit is often more than one soil, in stated proportions. The soil under a garden bed may be the inclusion, not the named unit.",
      },
      {
        type: "paragraph",
        text: "**Scale is not one number.** An optional special-features layer “displays the location of features too small to delineate at the mapping scale, but they are large enough and contrasting enough to significantly influence use and management.” Those bits are in the official product for a reason. A 2013 Forest Service general technical report (NRS-122) describes SSURGO as “1:12,000 or 1:24,000” and STATSGO as 1:250,000; that is a dated USFS description, useful as context, not a substitute for the official metadata list or for the local survey. “More than 95 percent of the nation’s counties” is also repeated in the SSURGO data.gov record, matching the WSS home page. That is a national coverage statement, not a claim that every acre in a covered county is equally detailed.",
      },
      {
        type: "paragraph",
        text: "**A sample plan is not a client, and a map is not a test.** Soil surveys “can be used for general farm, local, and wider area planning.” They are not a septic approval, an engineering sign-off, or a lab report. When the question is soil quality or certain engineering uses, Web Soil Survey itself tells you to walk the ground, and, as the same official product directs, to a local USDA Service Center or the NRCS State Soil Scientist when the use demands it.",
      },
      {
        type: "heading",
        text: "A shared record, applied here",
      },
      {
        type: "paragraph",
        text: "**Draw an area of interest in Web Soil Survey, or in PlantGeo on the same public data, list the map units, and read component percentages before choosing a use.** A single “soil type” label at a map-unit boundary is often the wrong lesson.",
      },
      {
        type: "paragraph",
        text: "**Look for special features too small to delineate,** which are in the product because they can change management.",
      },
      {
        type: "paragraph",
        text: "**Plan the land first.** If tools come next, they come from [Aevani’s catalogue](https://aevani.com), which is not for sale yet. [Plan the land, then pick the tools](https://plantgeo.aevani.com/blog/plan-the-land-then-pick-the-tools) is the house version of that sequence.",
      },
      {
        type: "heading",
        text: "What PlantGeo is, and is not, on this page",
      },
      {
        type: "paragraph",
        text: "PlantGeo can show the official soils layer. It does not replace a soil test, and it does not forecast what the soil will do next year — the same limit as [Not forecast beyond today](https://plantgeo.aevani.com/blog/not-forecast-beyond-today). The Ada County sample is labeled a sample every time it appears. The [affiliate page](https://plantgeo.aevani.com/affiliate) is a hold, and no referral share is quoted.",
      },
      {
        type: "heading",
        text: "Sources",
      },
      {
        type: "paragraph",
        text: "Every stat on this page is cited.",
      },
      {
        type: "sources",
      },
      {
        type: "paragraph",
        text: "The “How to Use Web Soil Survey” PDF and the Soil Survey Manual PDF timed out in the research pass and are not cited for figures.",
      },
      {
        type: "heading",
        text: "An illustrative scenario",
      },
      {
        type: "paragraph",
        text: "This is a worked example, not a client, and it uses no invented series names. A two-acre parcel has a house near a map-unit boundary. Treating the colorful polygon under the porch as a lab report would have been the wrong lesson. The unit is a pattern at a stated scale, often a mixture. The test, when you need one, is still a test.",
      },
      {
        type: "heading",
        text: "Also in this section",
      },
      {
        type: "list",
        items: [
          "[WUI parcels](/site-types/wui-parcel) — the interface is a map class, not a forecast.",
          "[Riparian and watershed edges](/site-types/riparian-watershed-edges) — the blue line is a mapped feature, not a restoration plan.",
        ],
      },
      {
        type: "paragraph",
        text: "Related: [About](https://plantgeo.aevani.com/about), the [Ada County sample plan](https://plantgeo.aevani.com/plan/sample-ada-county), [Not forecast beyond today](https://plantgeo.aevani.com/blog/not-forecast-beyond-today), [Plan the land, then pick the tools](https://plantgeo.aevani.com/blog/plan-the-land-then-pick-the-tools), Aevani’s [Understanding soil health](https://aevani.com/guides/understanding-soil-health), and the [growing guides](https://aevani.com/guides).",
      },
      {
        type: "paragraph",
        text: "The useful next step is to [open the map](https://plantgeo.aevani.com) or walk the [sample plan](https://plantgeo.aevani.com/plan/sample-ada-county). A soil test, when you need one, still happens on the ground.",
      },
      {
        type: "paragraph",
        text: "Author: Escherbridge. Last reviewed: 2026-11-10, America/Denver.",
      },
    ],
    sources: [
      {
        text: "USDA NRCS, Web Soil Survey home (coverage, authoritative-source language, on-site investigation limit). https://websoilsurvey.nrcs.usda.gov/app/",
        name: "USDA NRCS, Web Soil Survey home (coverage, authoritative-source language, on-site investigation limit).",
        url: "https://websoilsurvey.nrcs.usda.gov/app/",
      },
      {
        text: "data.gov, Soil Survey Geographic Database (SSURGO), official NRCS / USDA catalog record (updated 2 July 2026). https://catalog.data.gov/dataset/soil-survey-geographic-database-ssurgo",
        name: "data.gov, Soil Survey Geographic Database (SSURGO), official NRCS / USDA catalog record (updated 2 July 2026).",
        url: "https://catalog.data.gov/dataset/soil-survey-geographic-database-ssurgo",
        dateModified: "2026-07-02",
      },
      {
        text: "Official ISO 19115-3 SSURGO metadata XML (revision 2026-07-02; representative-fraction denominators; field-verified inventory language), downloaded from the data.gov record above.",
        name: "Official ISO 19115-3 SSURGO metadata XML (revision 2026-07-02; representative-fraction denominators; field-verified inventory language), downloaded from the data.gov record above.",
        url: "https://catalog.data.gov/dataset/soil-survey-geographic-database-ssurgo",
        dateModified: "2026-07-02",
      },
      {
        text: "Prasad, A.M.; Iverson, L.R.; Matthews, S.; Peters, M. 2013. *Integrating Fine-scale Soil Data into Species Distribution Models: Preparing Soil Survey Geographic (SSURGO) Data from Multiple Counties.* USDA Forest Service, Northern Research Station, GTR NRS-122. November 2013. https://www.fs.usda.gov/nrs/pubs/gtr/gtr_nrs122.pdf",
        name: "Prasad, A.M.; Iverson, L.R.; Matthews, S.; Peters, M. 2013. Integrating Fine-scale Soil Data into Species Distribution Models: Preparing Soil Survey Geographic (SSURGO) Data from Multiple Counties. USDA Forest Service, Northern Research Station, GTR NRS-122. November 2013.",
        url: "https://www.fs.usda.gov/nrs/pubs/gtr/gtr_nrs122.pdf",
        datePublished: "2013-11",
      },
      {
        text: "USDA NRCS SSURGO program landing — official URL for the dataset; retrieval timed out in the research pass, so no figures were copied from that page. https://www.nrcs.usda.gov/resources/data-and-reports/soil-survey-geographic-database-ssurgo",
        name: "USDA NRCS SSURGO program landing — official URL for the dataset; retrieval timed out in the research pass, so no figures were copied from that page.",
        url: "https://www.nrcs.usda.gov/resources/data-and-reports/soil-survey-geographic-database-ssurgo",
      },
      {
        text: "PlantGeo sample plan on live map data (internal, not a soil-science source): https://plantgeo.aevani.com/plan/sample-ada-county",
        name: "PlantGeo sample plan on live map data (internal, not a soil-science source):",
        url: "https://plantgeo.aevani.com/plan/sample-ada-county",
      },
      {
        text: "Aevani, “Understanding Soil Health,” as a sibling reading, not as a source of soil-survey figures: https://aevani.com/guides/understanding-soil-health",
        name: "Aevani, “Understanding Soil Health,” as a sibling reading, not as a source of soil-survey figures:",
        url: "https://aevani.com/guides/understanding-soil-health",
      },
    ],
  },
];

export function siteTypeBySlug(slug: string): SiteTypeArticle | undefined {
  return SITE_TYPES.find((page) => page.slug === slug);
}

export function siteTypePath(page: Pick<SiteTypeArticle, "slug">): string {
  return `/site-types/${page.slug}`;
}

export const SITE_TYPE_PATHS = SITE_TYPES.map((page) => siteTypePath(page));
