/**
 * Practical field-walk guides connecting public FEMA, Ready.gov, USFWS, and USGS
 * map layers to boots-on-the-ground site planning. Primary-source figures and
 * dates remain attributed strictly to their issuing agencies; legal/scope notes
 * live uniformly at the bottom of guide views via MapLegalNote.
 */

import type { SiteTypeBlock, SiteTypeCitation } from "@/lib/content/site-types";

export const GUIDE_AUTHOR = "Escherbridge";

export interface GuideIndex {
  h1: string;
  seoTitle: string;
  metaDescription: string;
  eyebrow: string;
  publishedAt: string;
  lastReviewed: string;
  blocks: readonly SiteTypeBlock[];
  sources: readonly SiteTypeCitation[];
}

export interface GuideArticle extends GuideIndex {
  slug: string;
  crumb: string;
}

export const GUIDE_INDEX: GuideIndex = {
  h1: "Field guides for reading water, terrain, and risk before you dig",
  seoTitle: "Field Guides for Land Planning | PlantGeo",
  metaDescription:
    "Ten practical guides showing how homesteaders, growers, and site designers pair public FEMA, Ready.gov, FWS wetland, and USGS elevation maps with a real walk around the land.",
  eyebrow: "Field guides · sun, slope, water, wetlands & wildfire buffers",
  publishedAt: "2026-11-10",
  lastReviewed: "2026-11-15",
  blocks: [
    {
      type: "paragraph",
      text: "When you walk a backyard slope or a five-acre pasture with rubber boots and a notebook, public maps give you a faster second pair of eyes. Before trenching irrigation, siting an orchard row, or clearing brush near a barn, ten public datasets tell you where water drains, how steep the contours drop, which low corners stay soggy, and how close high-water lines run.",
    },
    {
      type: "paragraph",
      text: "The trick on any parcel is knowing what each layer actually shows—and where screen lines end and ground truthing starts. Pair a [muddy-boots parcel walk](/blog/walking-a-five-acre-parcel-after-rain) with the ten plain-English field guides below, then test the same layers on the [Ada County sample plan](/plan/sample-ada-county) before staking your own site.",
    },
    {
      type: "heading",
      text: "The ten guides",
    },
    {
      type: "list",
      items: [
        "**[FEMA flood maps](/guides/fema-flood-maps)** — How to look up your community's official Flood Insurance Rate Map on the FEMA Flood Map Service Center, read Special Flood Hazard Area zone codes, and log the panel date in your site plan.",
        "**[Flooding doesn't follow lines on a map](/guides/ready-floods)** — Ready.gov notes that floods are the most common disaster in the United States and reminds every landowner: where it rains it can flood, even outside a printed map boundary.",
        "**[National Wetlands Inventory maps](/guides/nwi-wetlands)** — How the U.S. Fish and Wildlife Service tracks wetland and deepwater habitats across the country, and how growers use those polygons to spot wet swales early without treating a public layer as a permit decision.",
        "**[USGS 3D Elevation Program](/guides/usgs-3dep)** — Bare-earth 3DEP elevation surfaces and 3D point clouds (shown on the USGS baseline map caption as of September 30, 2026) beat flat hillshade screenshots when you are tracing subtle drainage channels, swales, and cold-air pockets.",
        "**[The National Map](/guides/national-map)** — How The National Geospatial Program and its partners knit topography, hydrography, and structures into an authoritative base map you can layer under your field notes.",
        "**[What is a base flood elevation](/guides/base-flood-elevation)** — FEMA's glossary defines the BFE as surface-water elevation for a flood with a 1% chance of equaling or exceeding that level in any given year on named zones—vital context before siting sheds, chicken coops, or root cellars.",
        "**[What is a FIRMette](/guides/firmette)** — How to export a full-scale, home-printable piece of an effective flood map from FEMA rather than guessing off an undated screenshot, and why preliminary panels cannot generate a FIRMette.",
        "**[Effective and preliminary flood maps](/guides/nfhl-preliminary-vs-effective)** — The National Flood Hazard Layer holds current effective flood data covering over 90% of the U.S. population (a population figure rather than land area), while preliminary maps give communities an early look before formal review.",
        "**[Ready.gov wildfire preparedness](/guides/ready-wildfires)** — How Ready.gov's baseline advice—keeping a fire-resistant zone free of leaves, debris, or flammable materials for at least 30 feet from the home—fits into shelterbelt and outbuilding placement while keeping Firewise and IBHS zone widths distinct.",
        "**[US Topo and historical topographic maps](/guides/us-topo)** — How USGS 1:24,000-scale 7.5-minute sheets compare between modern digital US Topos (since 2009) and the 1884 to 2006 historical archive when checking old ditches, springs, and road fills.",
      ],
    },
    {
      type: "heading",
      text: "Walk the map alongside your parcel notes",
    },
    {
      type: "paragraph",
      text: "Once you pick the layer that fits your immediate question—drainage swales, winter orchard slopes, low wetland edges, or home wildfire clearance—pair it with our [field blog stories](/blog) and land-kind comparisons in [site types](/site-types). Check release updates in [News](/news), or browse our technical architecture in [About](https://plantgeo.aevani.com/about) and [Not forecast beyond today](https://plantgeo.aevani.com/blog/not-forecast-beyond-today).",
    },
    {
      type: "paragraph",
      text: "Only after walking the ground and writing down your layer notes should you start sizing tanks, drip headers, or windbreak fencing—see [Plan the land, then pick the tools](https://plantgeo.aevani.com/blog/plan-the-land-then-pick-the-tools) or browse [Aevani’s catalogue](https://aevani.com) (currently a preview with items not for sale yet).",
    },
    {
      type: "heading",
      text: "A situation like this",
    },
    {
      type: "paragraph",
      text: "Every guide below ends with a short field scenario—an everyday mistake from the driveway or garden fence, treated strictly as a worked example rather than a client record, showing how checking the exact layer name and date saves costly rework.",
    },
    {
      type: "heading",
      text: "Sources",
    },
    {
      type: "paragraph",
      text: "Every public figure and definition in these ten guides links back to the issuing agency page below.",
    },
    {
      type: "sources",
    },
  ],
  sources: [
    {
      text: "FEMA, \"Flood Maps.\" https://www.fema.gov/flood-maps",
      name: "FEMA, \"Flood Maps.\"",
      url: "https://www.fema.gov/flood-maps",
    },
    {
      text: "Ready.gov, \"Floods.\" https://www.ready.gov/floods",
      name: "Ready.gov, \"Floods.\"",
      url: "https://www.ready.gov/floods",
    },
    {
      text: "U.S. Fish and Wildlife Service, National Wetlands Inventory. https://www.fws.gov/program/national-wetlands-inventory",
      name: "U.S. Fish and Wildlife Service, National Wetlands Inventory.",
      url: "https://www.fws.gov/program/national-wetlands-inventory",
    },
    {
      text: "USGS, 3D Elevation Program. https://www.usgs.gov/3d-elevation-program",
      name: "USGS, 3D Elevation Program.",
      url: "https://www.usgs.gov/3d-elevation-program",
    },
    {
      text: "USGS, \"The National Map.\" https://www.usgs.gov/programs/national-geospatial-program/national-map",
      name: "USGS, \"The National Map.\"",
      url: "https://www.usgs.gov/programs/national-geospatial-program/national-map",
    },
    {
      text: "FEMA glossary, \"Base Flood Elevation (BFE),\" last updated 5 March 2020. https://www.fema.gov/glossary/base-flood-elevation-bfe",
      name: "FEMA glossary, \"Base Flood Elevation (BFE),\" last updated 5 March 2020.",
      url: "https://www.fema.gov/glossary/base-flood-elevation-bfe",
      dateModified: "2020-03-05",
    },
    {
      text: "FEMA Flood Map Service Center FAQs. https://msc.fema.gov/portal/howto",
      name: "FEMA Flood Map Service Center FAQs.",
      url: "https://msc.fema.gov/portal/howto",
    },
    {
      text: "FEMA, \"The National Flood Hazard Layer.\" https://www.fema.gov/flood-maps/national-flood-hazard-layer",
      name: "FEMA, \"The National Flood Hazard Layer.\"",
      url: "https://www.fema.gov/flood-maps/national-flood-hazard-layer",
    },
    {
      text: "Ready.gov, \"Wildfires.\" https://www.ready.gov/wildfires",
      name: "Ready.gov, \"Wildfires.\"",
      url: "https://www.ready.gov/wildfires",
    },
    {
      text: "USGS, \"Topographic Maps.\" https://www.usgs.gov/programs/national-geospatial-program/topographic-maps",
      name: "USGS, \"Topographic Maps.\"",
      url: "https://www.usgs.gov/programs/national-geospatial-program/topographic-maps",
    },
  ],
};

export const GUIDES: readonly GuideArticle[] = [
  {
    h1: "FEMA flood maps: how to find a FIRM",
    seoTitle: "FEMA flood maps: how to find a FIRM — Guides | PlantGeo",
    metaDescription:
      "Use FEMA's Flood Map Service Center to check community flood zones, Special Flood Hazard Areas, and effective dates before siting sheds, gardens, or fencing.",
    eyebrow: "FEMA flood maps",
    slug: "fema-flood-maps",
    crumb: "FEMA flood maps",
    publishedAt: "2026-11-10",
    lastReviewed: "2026-11-15",
    blocks: [
      {
        type: "paragraph",
        text: "Before placing a greenhouse, chicken coop, or tool shed on low bottomland near a creek or irrigation canal, pull up FEMA's Flood Map Service Center (MSC). It is the official online home for all flood-hazard mapping products created under the National Flood Insurance Program, giving site planners a clear community baseline before stepping out to check ditch crowns and high-water trash lines on foot. Pair it with [Flooding doesn't follow lines on a map](/guides/ready-floods).",
      },
      {
        type: "paragraph",
        text: "FEMA, \"Flood Maps\": \"The FEMA Flood Map Service Center (MSC) is the official online location to find all flood hazard mapping products created under the National Flood Insurance Program, including your community's flood map.\" FEMA notes that flood maps help lenders determine insurance requirements and help communities plan ways to reduce risk.",
      },
      {
        type: "heading",
        text: "What FIRM and SFHA mean when looking at a parcel",
      },
      {
        type: "paragraph",
        text: "FEMA's glossary entry for Flood Insurance Rate Map (FIRM), last updated 8 July 2020, defines it as the \"Official map of a community on which FEMA has delineated the Special Flood Hazard Areas (SFHAs), the Base Flood Elevations (BFEs) and the risk premium zones applicable to the community.\"",
      },
      {
        type: "paragraph",
        text: "In FEMA's Special Flood Hazard Area (SFHA) glossary entry, last updated 7 July 2020, SFHAs appear on FHBMs or FIRMs in named zones including \"Zone A, AO, A1-A30, AE, A99, AH, AR, AR/A, AR/AE, AR/AH, AR/AO, AR/A1-A30, V1-V30, VE or V.\" As FEMA explains, \"The SFHA is the area where the National Flood Insurance Program's (NFIP's) floodplain management regulations must be enforced and the area where the mandatory purchase of flood insurance applies,\" while for Community Rating System premium discounts, \"all AR and A99 zones are treated as non-SFHAs.\"",
      },
      {
        type: "heading",
        text: "Pulling a clean section and writing down your field notes",
      },
      {
        type: "paragraph",
        text: "Once you locate your community's effective flood map in the MSC, you can open the View button associated with that map to generate a FIRMette—\"a full-scale section of a FIRM that you create yourself and is formatted to print on most home/office printers\"—so you have a readable panel slice with dates intact instead of guessing off an undated web crop.",
      },
      {
        type: "paragraph",
        text: "In your PlantGeo site notes, write down three things together: the effective map panel number, the exact zone letter printed over the low half of your parcel, and the date you checked the MSC. When tracing streamside edges or swales, keep [The National Map](/guides/national-map), the [National Wetlands Inventory](/guides/nwi-wetlands), and our [riparian and watershed site type](/site-types/riparian-watershed-edges) handy—and always inspect culvert sizes and snowmelt bottlenecks outdoors on foot, because runoff ignores smooth lines drawn on paper.",
      },
      {
        type: "heading",
        text: "A situation like this",
      },
      {
        type: "paragraph",
        text: "A seller hands over an undated printout showing a blue flood line ending across the road and claims the lower pasture sits safely in upland dirt. Five minutes on the MSC turns up an updated effective panel date and the true zone boundary—saving a timber workshop from being framed into seasonal backwater.",
      },
      {
        type: "heading",
        text: "Sources",
      },
      {
        type: "sources",
      },
      {
        type: "heading",
        text: "Also in this section",
      },
      {
        type: "list",
        items: [
          "[Flooding doesn't follow lines on a map](/guides/ready-floods) — Ready.gov's practical reminder about rainfall and runoff beyond mapped lines.",
          "[What is a base flood elevation (BFE)](/guides/base-flood-elevation) — how surface-water elevation differs from your finished floor or raised-bed height.",
          "[What is a FIRMette and how to print one](/guides/firmette) — exporting a true full-scale piece of an effective FIRM.",
          "[Effective and preliminary flood maps](/guides/nfhl-preliminary-vs-effective) — checking whether the layer in front of you is current effective data or an early review draft.",
          "[National Wetlands Inventory maps](/guides/nwi-wetlands) — reading habitat inventory polygons separately from regulatory flood lines.",
          "[USGS 3D Elevation Program](/guides/usgs-3dep) — bare-earth contours that show which roadside swale spills toward your beds.",
        ],
      },
    ],
    sources: [
      {
        text: "FEMA, \"Flood Maps.\" https://www.fema.gov/flood-maps",
        name: "FEMA, \"Flood Maps.\"",
        url: "https://www.fema.gov/flood-maps",
      },
      {
        text: "FEMA glossary, \"Flood Insurance Rate Map (FIRM),\" last updated 8 July 2020. https://www.fema.gov/glossary/flood-insurance-rate-map-firm",
        name: "FEMA glossary, \"Flood Insurance Rate Map (FIRM),\" last updated 8 July 2020.",
        url: "https://www.fema.gov/glossary/flood-insurance-rate-map-firm",
        dateModified: "2020-07-08",
      },
      {
        text: "FEMA glossary, \"Special Flood Hazard Area (SFHA),\" last updated 7 July 2020. https://www.fema.gov/glossary/special-flood-hazard-area-sfha",
        name: "FEMA glossary, \"Special Flood Hazard Area (SFHA),\" last updated 7 July 2020.",
        url: "https://www.fema.gov/glossary/special-flood-hazard-area-sfha",
        dateModified: "2020-07-07",
      },
      {
        text: "FEMA Flood Map Service Center FAQs. https://msc.fema.gov/portal/howto",
        name: "FEMA Flood Map Service Center FAQs.",
        url: "https://msc.fema.gov/portal/howto",
      },
    ],
  },
  {
    h1: "Flooding doesn't follow lines on a map",
    seoTitle: "Flooding doesn't follow lines on a map — Guides | PlantGeo",
    metaDescription:
      "Ready.gov advises checking FEMA flood maps first—and remembering that intense rain, snowmelt, and blocked ditches put water across driveways outside printed zones.",
    eyebrow: "Ready.gov on floods",
    slug: "ready-floods",
    crumb: "Flooding doesn't follow lines on a map",
    publishedAt: "2026-11-10",
    lastReviewed: "2026-11-15",
    blocks: [
      {
        type: "paragraph",
        text: "Ask any grower who has watched an August dry wash turn brown and fast in February: a clean white space outside the FEMA blue zone is not a waterproof promise. Ready.gov's flood guidance tells homeowners to start with [FEMA's Flood Map Service Center](/guides/fema-flood-maps), then puts the practical field truth right underneath it: flooding doesn't follow lines on a map.",
      },
      {
        type: "heading",
        text: "What Ready.gov highlights before rain hits",
      },
      {
        type: "paragraph",
        text: "Ready.gov states plainly: \"Floods are the most common disaster in the United States.\" Floodwaters \"may: Result from rain, snow, coastal storms, storm surges and overflows of dams and other water systems. Develop slowly or quickly. Flash floods can come without warning. Cause outages, disrupt transportation, damage buildings and create landslides.\" Under a flood warning, Ready.gov warns: \"Find safe shelter right away. Do not walk, swim or drive through flood waters.\"",
      },
      {
        type: "paragraph",
        text: "In its pre-flood checklist, Ready.gov pairs map checks with local storm sense: \"Know your risk for floods, visit FEMA's Flood Map Service Center to know types of flood risks in your area. However, remember that flooding doesn't follow lines on a map.\" The next point on the same page reinforces why every site walk matters: \"Where it rains it can flood.\" Ready.gov also points landowners toward community alert systems, the Emergency Alert System, and NOAA Weather Radio.",
      },
      {
        type: "paragraph",
        text: "On coverage timing, Ready.gov adds a factual administrative point worth knowing long before wet weather arrives: \"Get flood coverage under FEMA's National Flood Insurance Program (NFIP). Homeowner's insurance policies do not cover flooding. It typically takes up to 30 days for a policy to go into effect, so the time to buy is well before a disaster.\"",
      },
      {
        type: "heading",
        text: "Walking your own slope for sheet flow and culvert backups",
      },
      {
        type: "paragraph",
        text: "On a working yard or five-acre homestead, most surprise ponding outside FEMA-mapped stream corridors comes from local grading: a packed gravel driveway acting like a chute, an uphill neighbor's roof leaders pointing toward your fence, or leaves clogging a 12-inch roadside pipe during rapid snowmelt.",
      },
      {
        type: "paragraph",
        text: "Write down your effective FEMA panel and zone first, then walk the high edge of the property during steady rain. Trace where water actually sheets across compacted paths, keep low sheds and electrical boxes off natural troughs using [USGS 3DEP elevation contours](/guides/usgs-3dep), and compare [riparian and watershed edges](/site-types/riparian-watershed-edges) before turning over new beds.",
      },
      {
        type: "heading",
        text: "A situation like this",
      },
      {
        type: "paragraph",
        text: "A homestead garage sits forty yards outside a mapped Special Flood Hazard Area line on high bench ground. Nobody noticed that an uphill pasture swale funnels spring snowmelt directly across the drive—until an intense storm left three inches of muddy runoff pooling against the sill plate. Checking the map plus a five-minute rain walk spots that path before pouring concrete.",
      },
      {
        type: "heading",
        text: "Sources",
      },
      {
        type: "sources",
      },
      {
        type: "heading",
        text: "Also in this section",
      },
      {
        type: "list",
        items: [
          "[FEMA flood maps: how to find a FIRM](/guides/fema-flood-maps) — finding your effective community map and zone codes.",
          "[What is a base flood elevation (BFE)](/guides/base-flood-elevation) — understanding surface-water elevations on mapped zones.",
          "[USGS 3D Elevation Program](/guides/usgs-3dep) — inspecting bare-earth slope breaks where sheet flow concentrates.",
          "[National Wetlands Inventory maps](/guides/nwi-wetlands) — seeing mapped wetland habitats separately from stormwater lines.",
          "[Ready.gov wildfire preparedness](/guides/ready-wildfires) — the companion household hazard guide for clearing around structures.",
        ],
      },
    ],
    sources: [
      {
        text: "Ready.gov, \"Floods.\" https://www.ready.gov/floods",
        name: "Ready.gov, \"Floods.\"",
        url: "https://www.ready.gov/floods",
      },
      {
        text: "The official map itself: [FEMA flood maps](/guides/fema-flood-maps), citing FEMA MSC, FIRM, and SFHA pages.",
        name: "The official map itself: FEMA flood maps, citing FEMA MSC, FIRM, and SFHA pages.",
        url: "https://plantgeo.aevani.com/guides/fema-flood-maps",
      },
    ],
  },
  {
    h1: "National Wetlands Inventory maps",
    seoTitle: "National Wetlands Inventory maps — Guides | PlantGeo",
    metaDescription:
      "How growers and land planners use the U.S. Fish and Wildlife Service National Wetlands Inventory to spot seasonal wet swales early—without mistaking a public inventory for a local permit.",
    eyebrow: "National Wetlands Inventory",
    slug: "nwi-wetlands",
    crumb: "National Wetlands Inventory maps",
    publishedAt: "2026-11-10",
    lastReviewed: "2026-11-15",
    blocks: [
      {
        type: "paragraph",
        text: "Before digging a farm pond, burying waterline frost hydrants, or cutting windbreak trenches along a damp bottom, check the U.S. Fish and Wildlife Service's National Wetlands Inventory (NWI). A green or blue polygon crossing your corner on the inventory tells you to lace up your boots and probe that area with a shovel first—it is an essential early signal about wetland habitat extent, even though a public inventory polygon never settles a permit or property dispute by itself.",
      },
      {
        type: "heading",
        text: "What the U.S. Fish and Wildlife Service publishes",
      },
      {
        type: "paragraph",
        text: "On its official NWI program landing page, the U.S. Fish and Wildlife Service states: \"The U.S. Fish and Wildlife Service is the principal federal agency tasked with providing information to the public on the extent and status of the nation's wetland and deepwater habitats, as well as changes to these habitats over time.\" The Service notes: \"We produce and distribute maps and other geospatial data on American wetland and deepwater habitats, as well as monitor changes in these habitats through time.\"",
      },
      {
        type: "paragraph",
        text: "The program landing points landowners and practitioners directly toward the Wetlands Mapper, downloadable wetlands data, and mapping status and trends. Those resources show where public inventory mapping sees wetland or deepwater habitats—and help keep dry-season site walks from overlooking seasonal springs, sedge depressions, or old oxbows.",
      },
      {
        type: "heading",
        text: "Putting NWI to practical use on a parcel walk",
      },
      {
        type: "paragraph",
        text: "On real ground, a mapped NWI feature sits alongside two distinct companions: county soil-survey hydric clues (explored in [homestead mapped soils](/site-types/homestead-mapped-soils)) and community flood boundaries ([FEMA flood maps](/guides/fema-flood-maps)). A spot can hold saturated clay soils in spring without ever sitting inside a FEMA floodplain, or lie inside an NWI polygon without matching local county setback rules.",
      },
      {
        type: "paragraph",
        text: "Whenever you inspect a low swale on [The National Map](/guides/national-map) or in your [PlantGeo site plan](/plan/new), jot down \"NWI checked\" along with the date and whether mapped features edge near planned beds or trails. Walk the boundary during late spring to see where rushes, cattails, or gray-mottled subsoil actually start on the ground—because wet corners thrive with willow, dogwood, and pollinator buffer plantings instead of fighting drainage ditches forever.",
      },
      {
        type: "heading",
        text: "A situation like this",
      },
      {
        type: "paragraph",
        text: "A buyer inspects a dry meadow in September and writes \"no wet areas on parcel\" on an orchard layout without ever pulling up NWI. Come April, groundwater seeps across the lower three planting rows and rots newly planted stone fruit. Checking NWI before ordering trees flags that damp bottom early so orchard rows stay on the dry bench uphill.",
      },
      {
        type: "heading",
        text: "Sources",
      },
      {
        type: "sources",
      },
      {
        type: "heading",
        text: "Also in this section",
      },
      {
        type: "list",
        items: [
          "[Homestead mapped soils](/site-types/homestead-mapped-soils) — reading county soil survey units alongside wet corners.",
          "[Riparian and watershed edges](/site-types/riparian-watershed-edges) — site-planning guidance for stream corridors and wet draws.",
          "[FEMA flood maps: how to find a FIRM](/guides/fema-flood-maps) — flood hazard zones vs wetland habitat layers.",
          "[USGS 3D Elevation Program](/guides/usgs-3dep) — spotting shallow depressions where seasonal water gathers.",
        ],
      },
    ],
    sources: [
      {
        text: "U.S. Fish and Wildlife Service, National Wetlands Inventory. https://www.fws.gov/program/national-wetlands-inventory",
        name: "U.S. Fish and Wildlife Service, National Wetlands Inventory.",
        url: "https://www.fws.gov/program/national-wetlands-inventory",
      },
    ],
  },
  {
    h1: "USGS 3D Elevation Program",
    seoTitle: "USGS 3D Elevation Program — Guides | PlantGeo",
    metaDescription:
      "How bare-earth 3DEP elevation surfaces and 3D point clouds help growers trace hidden drainage swales, terrace benches, and frost pockets under tree canopy.",
    eyebrow: "3D Elevation Program",
    slug: "usgs-3dep",
    crumb: "USGS 3D Elevation Program",
    publishedAt: "2026-11-10",
    lastReviewed: "2026-11-15",
    blocks: [
      {
        type: "paragraph",
        text: "Overhead aerial photos look flat once oak canopy, sagebrush, or tall pasture grass fills the frame. That is where the U.S. Geological Survey's 3D Elevation Program (3DEP) shines for land planners: it is the national path to consistent high-resolution elevation data—bare-earth surfaces and 3D point clouds—letting you inspect ground contours underneath trees so water lines, ridges, and frost pockets jump out before you unroll a tape measure outdoors.",
      },
      {
        type: "heading",
        text: "What 3DEP publishes and how to read the work-unit details",
      },
      {
        type: "paragraph",
        text: "The USGS 3DEP page presents 3DEP data products alongside documentation on lidar quality levels (including links explaining \"What is QL0?\" and \"How are Lidar QLs defined?\"), spatial metadata, partner data acquisition, and program standards. A caption accompanying the national map graphic on the program page notes that it \"shows the completion of the first-ever national baseline of consistent high-resolution elevation data - both bare earth and 3D point clouds - including baseline and next generation coverage as of September 30, 2026.\"",
      },
      {
        type: "paragraph",
        text: "For broader context on public demand, the USGS 3DEP page references the 3D Nation Study, noting: \"Study documents $13.5 billion of benefits for 1,352 mission critical requirements for topographic and inland, nearshore, and offshore bathymetric 3D elevation data.\" That national figure applies to mission-critical requirements nationwide, not to a single backyard parcel—and individual lidar quality still depends on the specific work unit and spatial metadata covering your county.",
      },
      {
        type: "heading",
        text: "Tracing swales, terraces, and cold-air drainage on foot",
      },
      {
        type: "paragraph",
        text: "When you look at a shaded terrain hillshade on screen, check the 3DEP source and work-unit date rather than treating a pretty shadow render as an engineered grade. On small acreages, bare-earth elevation shines for finding subtle landforms you might walk straight across in dry grass:",
      },
      {
        type: "list",
        items: [
          "**Headwater saddles:** Pinpoint where a ridge softly dips toward your orchard beds, steering cold nighttime air downslope away from stone-fruit blossoms.",
          "**Old agricultural furrows and ditches:** See historic berms or cutbacks hidden inside overgrown pasture before deciding where to place keyline swales or rainwater earthworks.",
          "**Aspect shifts:** Read where a hillside turns smoothly from warm southeast morning sun into cooler northeast shade—paired with [The National Map](/guides/national-map) and [US Topo sheets](/guides/us-topo).",
        ],
      },
      {
        type: "heading",
        text: "A situation like this",
      },
      {
        type: "paragraph",
        text: "A site designer receives a contractor's undated hillshade screenshot with zero layer name or contour source and assumes a low saddle has enough drop to drain a rain garden freely. Pulling the underlying 3DEP work-unit metadata alongside a level walk with stakes and string shows a two-foot dead pocket downslope—avoiding standing mosquito water beside the garden path.",
      },
      {
        type: "heading",
        text: "Sources",
      },
      {
        type: "sources",
      },
      {
        type: "heading",
        text: "Also in this section",
      },
      {
        type: "list",
        items: [
          "[The National Map](/guides/national-map) — USGS's base mapping suite built with 3DEP elevation and hydrography.",
          "[US Topo and historical topographic maps](/guides/us-topo) — reading contour sheets and 1884 to 2006 historical changes.",
          "[Flooding doesn't follow lines on a map](/guides/ready-floods) — watching how shallow ground swales carry storm rain beyond mapped zones.",
          "[FEMA flood maps: how to find a FIRM](/guides/fema-flood-maps) — pairing elevation cues with community flood boundaries.",
        ],
      },
    ],
    sources: [
      {
        text: "USGS, 3D Elevation Program. https://www.usgs.gov/3d-elevation-program",
        name: "USGS, 3D Elevation Program.",
        url: "https://www.usgs.gov/3d-elevation-program",
      },
    ],
  },
  {
    h1: "The National Map: a base is not a site plan",
    seoTitle: "The National Map — Guides | PlantGeo",
    metaDescription:
      "How growers and landscape planners use USGS The National Map as an authoritative base layer for roads, hydrography, and contours before drawing farm or yard layouts.",
    eyebrow: "The National Map",
    slug: "national-map",
    crumb: "The National Map",
    publishedAt: "2026-11-10",
    lastReviewed: "2026-11-15",
    blocks: [
      {
        type: "paragraph",
        text: "Before sketching fence lines, access roads, and water storage tanks, every land planner needs an honest base map showing real topography, streams, and roads. USGS's The National Map gives homeowners and designers that shared starting grid—connecting authoritative national layers so your own parcel sketches rest on solid public geography instead of distorted phone screenshots.",
      },
      {
        type: "heading",
        text: "What The National Map puts in reach",
      },
      {
        type: "paragraph",
        text: "USGS defines its suite cleanly: \"The National Map is a suite of products, services, and applications that provide access to base geospatial information representing the topography, natural landscape, and built environment of the United States and its territories.\" It \"supports digital and print versions of topographic maps, data download, web map services, and online viewing of maps and data.\"",
      },
      {
        type: "paragraph",
        text: "As USGS notes, \"The National Geospatial Program and its partners acquire and integrate authoritative geospatial data from a variety of sources and makes the resulting nationally consistent geospatial data layers available to all through The National Map.\" Those consistent layers feed cartographic products \"such as US Topo and OnDemand Topo maps, and provides access to digital historical topographic maps,\" alongside standards for elevation, hydrography, The National Map Viewer, and public training materials.",
      },
      {
        type: "heading",
        text: "Keeping base layers, flood lines, and growing plans in their lanes",
      },
      {
        type: "paragraph",
        text: "Even the cleanest USGS base is a foundation—a base is not a site plan, and it is not a property survey. On a real homestead or permaculture site, pair The National Map's base contours and streams with specialized overlays that do different jobs: [USGS 3DEP](/guides/usgs-3dep) for bare-earth micro-topography, [FEMA flood maps](/guides/fema-flood-maps) for regulated flood zones, and [National Wetlands Inventory maps](/guides/nwi-wetlands) for wetland habitat edges.",
      },
      {
        type: "paragraph",
        text: "Once your site layout matches what the ground actually does, [Aevani's growing guides](https://aevani.com/guides) pick up the plant and raised-bed side (noting Aevani's catalogue itself remains a preview not for sale yet), while [homestead mapped soils](/site-types/homestead-mapped-soils) and [WUI parcels](/site-types/wui-parcel) translate base layers into parcel checks.",
      },
      {
        type: "heading",
        text: "A situation like this",
      },
      {
        type: "paragraph",
        text: "Someone attaches a blurry, undated topographic screenshot inside a contractor folder labeled \"site plan,\" with no product name or scale. Naming the exact National Map product and inspection date—and separating base maps from your measured ground stakes—keeps contractors, growers, and inspectors speaking the same language.",
      },
      {
        type: "heading",
        text: "Sources",
      },
      {
        type: "sources",
      },
      {
        type: "heading",
        text: "Also in this section",
      },
      {
        type: "list",
        items: [
          "[US Topo and historical topographic maps](/guides/us-topo) — picking between current US Topos, OnDemand sheets, and historical quadrangles.",
          "[USGS 3D Elevation Program](/guides/usgs-3dep) — bare-earth lidar elevation feeding base contours.",
          "[FEMA flood maps: how to find a FIRM](/guides/fema-flood-maps) — community flood boundaries alongside topographic streams.",
          "[National Wetlands Inventory maps](/guides/nwi-wetlands) — habitat mapping where streams widen into marshes or meadows.",
        ],
      },
    ],
    sources: [
      {
        text: "USGS, \"The National Map.\" https://www.usgs.gov/programs/national-geospatial-program/national-map",
        name: "USGS, \"The National Map.\"",
        url: "https://www.usgs.gov/programs/national-geospatial-program/national-map",
      },
    ],
  },
  {
    h1: "What is a base flood elevation (BFE)",
    seoTitle: "What is a base flood elevation (BFE) — Guides | PlantGeo",
    metaDescription:
      "What a FEMA Base Flood Elevation (BFE) number really tells growers and homesteaders—1% annual-chance floodwater height on named zones, not an automatic finished floor or Zone A rule.",
    eyebrow: "Base flood elevation",
    slug: "base-flood-elevation",
    crumb: "What is a base flood elevation",
    publishedAt: "2026-11-10",
    lastReviewed: "2026-11-15",
    blocks: [
      {
        type: "paragraph",
        text: "When you inspect a creekside parcel on a FEMA map and see a dashed wave line with a number like 482 or 2714, that number is a Base Flood Elevation (BFE). Before pouring footings for a wash-pack shed, raised greenhouse, or solar battery rack near bottomland, knowing what that number actually measures—and which zones leave it blank—prevents expensive elevation guesswork.",
      },
      {
        type: "heading",
        text: "FEMA's exact definition of a BFE",
      },
      {
        type: "paragraph",
        text: "FEMA's glossary entry for Base Flood Elevation (BFE), last updated 5 March 2020, defines it as: \"The elevation of surface water resulting from a flood that has a 1% chance of equaling or exceeding that level in any given year. The BFE is shown on the Flood Insurance Rate Map (FIRM) for zones AE, AH, A1–A30, AR, AR/A, AR/AE, AR/A1–A30, AR/AH, AR/AO, V1–V30 and VE.\"",
      },
      {
        type: "paragraph",
        text: "Notice two plain rules hidden inside that single glossary sentence. First, a BFE refers to a **1% chance of equaling or exceeding that level in any given year**—meaning a flood reaching or topping that elevation can happen two years in a row rather than waiting a polite hundred years between visits. Second, plain Zone A maps unstudied floodplains where FEMA's glossary does not list a published BFE line, so nobody should make up a BFE off the back of an envelope.",
      },
      {
        type: "heading",
        text: "How BFE informs homestead and garden layout",
      },
      {
        type: "paragraph",
        text: "A printed BFE is not a finished-floor code requirement, a vertical datum stamp, or a substitute for a licensed surveyor's elevation certificate. Every county or flood district applies its own freeboard rules and benchmark datums on top of FEMA's surface-water number.",
      },
      {
        type: "paragraph",
        text: "In your [PlantGeo site plan](/plan/new), log the effective panel from [FEMA flood maps](/guides/fema-flood-maps), whether the dataset is [effective or preliminary](/guides/nfhl-preliminary-vs-effective), the printed zone and BFE if shown, and the date checked. Then use common field sense: keep beehives, compost bays, fuel drums, and tool sheds well uphill of seasonal high water, and remember [Ready.gov's reminder](/guides/ready-floods) that flash runoff can overtop small creek banks faster than paper lines shift.",
      },
      {
        type: "heading",
        text: "A situation like this",
      },
      {
        type: "paragraph",
        text: "Someone reads a nearby creek's BFE off a web viewer, eyeballs ground height from the back porch, and stakes a permanent timber shed \"safe at the BFE\" with zero datum check or local floodplain inquiry. Walking the site with an official [FIRMette](/guides/firmette) and consulting local officials before pouring concrete avoids sitting right inside local freeboard rules.",
      },
      {
        type: "heading",
        text: "Sources",
      },
      {
        type: "sources",
      },
      {
        type: "heading",
        text: "Also in this section",
      },
      {
        type: "list",
        items: [
          "[FEMA flood maps: how to find a FIRM](/guides/fema-flood-maps) — looking up your parcel's community panel and zone.",
          "[What is a FIRMette and how to print one](/guides/firmette) — printing an official full-scale slice of an effective FIRM.",
          "[Effective and preliminary flood maps](/guides/nfhl-preliminary-vs-effective) — confirming whether you are reading current effective data.",
          "[Flooding doesn't follow lines on a map](/guides/ready-floods) — keeping overland runoff in view outside mapped channels.",
        ],
      },
    ],
    sources: [
      {
        text: "FEMA glossary, \"Base Flood Elevation (BFE),\" last updated 5 March 2020. https://www.fema.gov/glossary/base-flood-elevation-bfe",
        name: "FEMA glossary, \"Base Flood Elevation (BFE),\" last updated 5 March 2020.",
        url: "https://www.fema.gov/glossary/base-flood-elevation-bfe",
        dateModified: "2020-03-05",
      },
      {
        text: "Sibling context: [FEMA flood maps](/guides/fema-flood-maps) and [Flooding doesn't follow lines on a map](/guides/ready-floods)",
        name: "Sibling context: FEMA flood maps and Flooding doesn't follow lines on a map",
        url: "https://plantgeo.aevani.com/guides/fema-flood-maps",
        sameAs: ["https://plantgeo.aevani.com/guides/ready-floods"],
      },
    ],
  },
  {
    h1: "What is a FIRMette and how to print one",
    seoTitle: "What is a FIRMette and how to print one — Guides | PlantGeo",
    metaDescription:
      "How to generate a full-scale, home-printable FEMA FIRMette of an effective flood map for your binder or clipboard walk—and why preliminary panels cannot produce one.",
    eyebrow: "FIRMette",
    slug: "firmette",
    crumb: "What is a FIRMette",
    publishedAt: "2026-11-10",
    lastReviewed: "2026-11-15",
    blocks: [
      {
        type: "paragraph",
        text: "Full FEMA flood panels are giant wall sheets—roughly two feet by three feet—nearly impossible to print cleanly on a regular kitchen printer without shrinking the scale or chopping off the legend. A FIRMette solves that problem: it pulls a crisp, full-scale slice of an effective map directly onto letter paper so you can slip it onto a clipboard alongside your parcel sketch.",
      },
      {
        type: "heading",
        text: "What FEMA says a FIRMette is—and when it will not work",
      },
      {
        type: "paragraph",
        text: "Per FEMA's Flood Map Service Center FAQs: \"FEMA’s Flood Insurance Rate Maps (FIRMs) are approximately two feet by three feet in size when printed to full-scale. A FIRMette is a full-scale section of a FIRM that you create yourself and is formatted to print on most home/office printers.\" To pull one, \"You can create a FIRMette of most effective maps in FEMA’s online inventory using the FIRMette Web tool. To create a FIRMette, you must first find a flood map, and then click the View button associated with that flood map.\"",
      },
      {
        type: "paragraph",
        text: "FEMA's National Flood Hazard Layer page also notes you can use the NFHL Print Tool inside the NFHL Viewer \"to download and print a full Flood Insurance Rate Map (FIRM) or FIRMette (a smaller, printable version of a FIRM) where NFHL data exists.\"",
      },
      {
        type: "paragraph",
        text: "One critical boundary trips many people up: preliminary maps cannot generate a FIRMette. As FEMA's FAQ states directly: \"Preliminary Flood Insurance Rate Maps are not final regulatory products and should not be used for the purposing of making flood determinations. Preliminary FIRMs cannot be viewed with the FIRMette tool, nor accessed with the FIRMette Desktop application. Therefore, a FIRMette cannot be created for preliminary maps.\" Read [Effective and preliminary flood maps](/guides/nfhl-preliminary-vs-effective) if your county has an upcoming draft update under review.",
      },
      {
        type: "heading",
        text: "Taking your FIRMette outside",
      },
      {
        type: "paragraph",
        text: "Once downloaded from FEMA MSC after checking [FEMA flood maps](/guides/fema-flood-maps), keep the FIRMette header, panel number, and effective date intact with your site notes. Walk the fence line with that paper sheet in hand and compare it with on-ground ditch banks, culvert inlets, and [Base Flood Elevation](/guides/base-flood-elevation) references—knowing a printed slice helps orient your walk, never replaces a licensed land survey.",
      },
      {
        type: "heading",
        text: "A situation like this",
      },
      {
        type: "paragraph",
        text: "Someone screenshots a preliminary draft map off a county blog, prints it onto letter paper, and labels the file \"Official FIRMette\" in a shed permit packet. Checking the real MSC View tool shows immediately that preliminary drafts cannot produce a FIRMette—catching the mix-up before any site work begins.",
      },
      {
        type: "heading",
        text: "Sources",
      },
      {
        type: "sources",
      },
      {
        type: "heading",
        text: "Also in this section",
      },
      {
        type: "list",
        items: [
          "[FEMA flood maps: how to find a FIRM](/guides/fema-flood-maps) — looking up your effective panel first.",
          "[Effective and preliminary flood maps](/guides/nfhl-preliminary-vs-effective) — keeping current effective maps separate from preliminary drafts.",
          "[What is a base flood elevation (BFE)](/guides/base-flood-elevation) — reading the elevation marks printed on named FIRM zones.",
        ],
      },
    ],
    sources: [
      {
        text: "FEMA Flood Map Service Center FAQs. https://msc.fema.gov/portal/howto",
        name: "FEMA Flood Map Service Center FAQs.",
        url: "https://msc.fema.gov/portal/howto",
      },
      {
        text: "FEMA, \"The National Flood Hazard Layer\" (print tool mention). https://www.fema.gov/flood-maps/national-flood-hazard-layer",
        name: "FEMA, \"The National Flood Hazard Layer\" (print tool mention).",
        url: "https://www.fema.gov/flood-maps/national-flood-hazard-layer",
      },
    ],
  },
  {
    h1: "Effective and preliminary flood maps, and the NFHL",
    seoTitle: "Effective and preliminary flood maps, and the NFHL — Guides | PlantGeo",
    metaDescription:
      "How FEMA's National Flood Hazard Layer (current effective data) compares with preliminary flood hazard drafts during community review—and why date-stamping your site plan matters.",
    eyebrow: "Effective and preliminary flood maps",
    slug: "nfhl-preliminary-vs-effective",
    crumb: "Effective and preliminary flood maps",
    publishedAt: "2026-11-10",
    lastReviewed: "2026-11-15",
    blocks: [
      {
        type: "paragraph",
        text: "Counties revise watershed studies as bridges widen, subdivisions add pavement, and lidar refines stream channels. That means two different FEMA maps can sometimes appear for the exact same creek: the **current effective** flood hazard layer in force today, and **preliminary flood hazard data** posted for public inspection before formal adoption. Knowing which dataset sits open on your screen prevents framing your site layout around the wrong boundary.",
      },
      {
        type: "heading",
        text: "What the National Flood Hazard Layer covers",
      },
      {
        type: "paragraph",
        text: "FEMA explains the layer cleanly: \"The National Flood Hazard Layer (NFHL) is a geospatial database that contains current effective flood hazard data. FEMA provides the flood hazard data to support the National Flood Insurance Program.\" Built from effective flood maps and Letters of Map Change (LOMC) delivered to communities, \"The NFHL can also be used in place of the FIRM for NFIP purposes with appropriate care.\"",
      },
      {
        type: "paragraph",
        text: "FEMA also notes: \"NFHL digital data covers over 90% of the U.S. population. New and revised data is being added continuously. If you need information for areas not covered by the NFHL data, there may be other FEMA products which provide coverage for those areas.\" Bear in mind that \"over 90% of the U.S. population\" describes population coverage nationwide, not \"90% of land area.\" Rural valleys and mountain headwaters still rely on panel lookups in the MSC.",
      },
      {
        type: "heading",
        text: "How preliminary maps differ from effective maps",
      },
      {
        type: "paragraph",
        text: "On the same National Flood Hazard Layer page, under Preliminary Flood Hazard Data, FEMA writes: \"Preliminary flood hazard data provides the public an early look at their home or community’s projected risk to flood hazards. Before they become effective, the preliminary products go through a formal review period that provides the opportunity to appeal the preliminary determinations with better data.\" They help community officials and landowners review upcoming FIRMs, Flood Insurance Study (FIS) Reports, and FIRM Databases for accuracy—paired with FEMA's Flood Map Changes Viewer and a Pending Flood Hazard Data section heading.",
      },
      {
        type: "paragraph",
        text: "At the same time, FEMA MSC FAQs remain unambiguous: preliminary FIRMs \"are not final regulatory products\" and should not be used to make final flood determinations, and you cannot generate a [FIRMette](/guides/firmette) from a preliminary panel.",
      },
      {
        type: "heading",
        text: "How to keep both clear in your site notes",
      },
      {
        type: "paragraph",
        text: "If your watershed has a preliminary update on the horizon, log **both** in your [PlantGeo site plan](/plan/new): write down the current effective zone and date from [FEMA flood maps](/guides/fema-flood-maps), and note separately whether a preliminary revision shifts the creek bank uphill or closer to your orchard. Watching early revisions helps homesteaders avoid putting barns right into an expanding floodway without mistaking an early draft for today's legal map.",
      },
      {
        type: "heading",
        text: "A situation like this",
      },
      {
        type: "paragraph",
        text: "Two neighbors pull different flood lines along the same lane: one downloaded a draft county map labeled preliminary, while the other checked the effective NFHL panel. Writing \"Effective NFHL\" or \"Preliminary draft\" plus the inspection date beside every sketch eliminates confusion immediately.",
      },
      {
        type: "heading",
        text: "Sources",
      },
      {
        type: "sources",
      },
      {
        type: "heading",
        text: "Also in this section",
      },
      {
        type: "list",
        items: [
          "[FEMA flood maps: how to find a FIRM](/guides/fema-flood-maps) — starting at FEMA's Flood Map Service Center.",
          "[What is a FIRMette and how to print one](/guides/firmette) — printing a full-scale piece of an effective FIRM.",
          "[What is a base flood elevation (BFE)](/guides/base-flood-elevation) — understanding surface-water numbers on named zones.",
        ],
      },
    ],
    sources: [
      {
        text: "FEMA, \"The National Flood Hazard Layer.\" https://www.fema.gov/flood-maps/national-flood-hazard-layer",
        name: "FEMA, \"The National Flood Hazard Layer.\"",
        url: "https://www.fema.gov/flood-maps/national-flood-hazard-layer",
      },
      {
        text: "FEMA Flood Map Service Center FAQs (preliminary products; FIRMette limit). https://msc.fema.gov/portal/howto",
        name: "FEMA Flood Map Service Center FAQs (preliminary products; FIRMette limit).",
        url: "https://msc.fema.gov/portal/howto",
      },
    ],
  },
  {
    h1: "Ready.gov wildfire preparedness",
    seoTitle: "Ready.gov wildfire preparedness — Guides | PlantGeo",
    metaDescription:
      "How Ready.gov's wildfire home-hardening steps—alerts, hose reach, and a 30-foot leaf- and debris-free fire-resistant zone—fit into placing windbreaks and plantings near rural buildings.",
    eyebrow: "Ready.gov wildfires",
    slug: "ready-wildfires",
    crumb: "Ready.gov wildfire preparedness",
    publishedAt: "2026-11-10",
    lastReviewed: "2026-11-15",
    blocks: [
      {
        type: "paragraph",
        text: "Planning windbreaks, mulch beds, and woodpiles near a house or equipment barn in dry western valleys means keeping clearance around structures top of mind before the first seedling goes in the ground. Ready.gov gives households a clear baseline checklist—covering emergency alerts, outdoor water reach, and a fire-resistant zone around the home—that fits cleanly into parcel planning alongside our detailed [WUI parcel notes](/site-types/wui-parcel).",
      },
      {
        type: "heading",
        text: "What Ready.gov recommends for homes and outbuildings",
      },
      {
        type: "paragraph",
        text: "Ready.gov defines wildfires as \"unplanned fires that burn out of control in natural areas like forests, grasslands or prairies. These fires spread quickly and can devastate wildlife, natural areas and communities.\" Before fire season arrives, the page recommends layering multiple alert channels: the FEMA app for National Weather Service alerts \"for up to five locations nationwide,\" local community alerts, the Emergency Alert System, and Wireless Emergency Alerts, \"which requires no-sign up,\" while advising households to \"Pay attention to air quality alerts\" and keep an evacuation plan and current document copies ready.",
      },
      {
        type: "paragraph",
        text: "Under \"Strengthen your Home,\" Ready.gov pairs building upkeep with surrounding yard maintenance: \"Use fire-resistant materials to build, renovate or make repairs. Find an outdoor water source with a hose that can reach any area of your property. Create a fire-resistant zone that is free of leaves, debris or flammable materials for at least 30 feet from your home.\" For smoky days, it also advises picking a room that closes tightly from outside air and using a portable air cleaner \"to keep indoor pollution levels low when smoky conditions exist.\"",
      },
      {
        type: "heading",
        text: "Keeping Ready.gov's 30 feet separate from Firewise zones",
      },
      {
        type: "paragraph",
        text: "Growers often hear overlapping zone numbers in community workshops. Ready.gov's baseline rule is simple and specific: clear leaves, debris, and flammable materials **for at least 30 feet from your home**. Separate NFPA Firewise USA and IBHS zone distances live on their own pages and are laid out distinctly on our [WUI parcel](/site-types/wui-parcel) guide rather than blurred into one hybrid rule.",
      },
      {
        type: "paragraph",
        text: "In practice, that boundary shapes where you plant trees and stack mulch: keep evergreen shelterbelts, deep straw stacks, and firewood racks well outside that cleared home perimeter, verify hosebib reach on your [PlantGeo site plan](/plan/new), and save leafy perennial shrubs for irrigated garden terraces away from siding and eaves.",
      },
      {
        type: "heading",
        text: "A situation like this",
      },
      {
        type: "paragraph",
        text: "A homestead layout shows dense pine windbreaks and uncomposted bark mulch running right up to the porch deck while referencing \"Ready.gov and Firewise\" as if both said the exact same thing. Keeping Ready.gov's \"at least 30 feet\" clear perimeter distinct—and pushing resinous windbreaks farther out across the pasture ridge—protects both winter shelter and porch safety.",
      },
      {
        type: "heading",
        text: "Sources",
      },
      {
        type: "sources",
      },
      {
        type: "heading",
        text: "Also in this section",
      },
      {
        type: "list",
        items: [
          "[WUI parcels](/site-types/wui-parcel) — Firewise and IBHS zone rules, kept distinct from Ready.gov's baseline checklist.",
          "[Reading winter sun, south slopes, and windbreaks](/blog/sun-arc-aspect-and-windbreaks-for-orchards-and-beds) — how to place windbreaks off eaves while avoiding frost dams.",
          "[Flooding doesn't follow lines on a map](/guides/ready-floods) — the companion Ready.gov guide for yard water and drainage.",
        ],
      },
    ],
    sources: [
      {
        text: "Ready.gov, \"Wildfires.\" https://www.ready.gov/wildfires",
        name: "Ready.gov, \"Wildfires.\"",
        url: "https://www.ready.gov/wildfires",
      },
      {
        text: "Zone-science sibling: [WUI parcels](/site-types/wui-parcel)",
        name: "Zone-science sibling: WUI parcels",
        url: "https://plantgeo.aevani.com/site-types/wui-parcel",
      },
    ],
  },
  {
    h1: "US Topo and historical topographic maps",
    seoTitle: "US Topo and historical topographic maps — Guides | PlantGeo",
    metaDescription:
      "How USGS current US Topo quadrangles (1:24,000-scale, digital since 2009) and historical maps (1884–2006) help homesteaders spot old springs, ditches, and land changes.",
    eyebrow: "US Topo and historical maps",
    slug: "us-topo",
    crumb: "US Topo and historical topographic maps",
    publishedAt: "2026-11-10",
    lastReviewed: "2026-11-15",
    blocks: [
      {
        type: "paragraph",
        text: "Walk an older rural parcel long enough and you start wondering why there is a sunken stone ditch behind the barn or why one corner of dry pasture stays greener in July than the field beside it. Comparing a modern digital **US Topo** quadrangle against a **historical topographic sheet** often reveals historic springs, abandoned irrigation canals, and gravel borrow pits that modern subdivision roads erased years ago.",
      },
      {
        type: "heading",
        text: "How US Topo compares with historical sheets",
      },
      {
        type: "paragraph",
        text: "As USGS summarizes on its Topographic Maps page: \"The U.S. Geological Survey (USGS) has been the primary civilian mapping agency of the United States since 1879. Some of the most well-known USGS maps are the 1:24,000-scale topographic maps, also called 7.5-minute quadrangles. In 2009, the USGS transitioned from our hand scribed historical topographic maps to US Topos, which are computer-generated on a regular schedule using national databases…\" Built over 140+ years of topographic work, \"the US Topo series delivers consistent map coverage of the American landscape using National Map data\" and is \"Available for free and immediate download.\" Alongside current sheets, USGS notes: \"The Historical Topographic Map Collection contains USGS topographic maps published 1884 to 2006,\" and points to topoBuilder for custom \"OnDemand Topos.\"",
      },
      {
        type: "paragraph",
        text: "Because [The National Map](/guides/national-map) serves as the shared database underneath current US Topo and OnDemand sheets, keeping the distinction clear matters in any parcel binder: always write down whether you looked at a current US Topo, an OnDemand sheet, or a dated historical quadrangle from 1884–2006.",
      },
      {
        type: "heading",
        text: "What to check on foot when old contours disagree with present ground",
      },
      {
        type: "paragraph",
        text: "When a historical 1:24,000 sheet marks a spring or seasonal creek crossing where your modern parcel looks level, bring both [USGS 3DEP bare-earth elevation](/guides/usgs-3dep) and a shovel outdoors. Old road fills, stock ponds, and tile drains frequently divert historic water slightly sideways across fence lines—vital clues before placing septic leach fields, orchards, or earthworks inside your [PlantGeo site plan](/plan/new). And once the land side is clear, [Aevani's growing guides](https://aevani.com/guides) carry planting-system references (with preview catalogue items not for sale yet).",
      },
      {
        type: "heading",
        text: "A situation like this",
      },
      {
        type: "paragraph",
        text: "A landowner stamps \"USGS topo\" across a planting layout after mixing an old 1958 scanned quadrangle showing an open spring with a modern US Topo showing a culverted road crossing—without writing down the edition date or series name. Checking both edition dates side by side explains immediately where that missing seep was piped underground.",
      },
      {
        type: "heading",
        text: "Sources",
      },
      {
        type: "sources",
      },
      {
        type: "heading",
        text: "Also in this section",
      },
      {
        type: "list",
        items: [
          "[The National Map: a base is not a site plan](/guides/national-map) — how national base layers drive current topo sheets.",
          "[USGS 3D Elevation Program](/guides/usgs-3dep) — bare-earth lidar surfaces that reveal micro-topography below tree cover.",
          "[Reading winter sun, south slopes, and windbreaks](/blog/sun-arc-aspect-and-windbreaks-for-orchards-and-beds) — turning contour aspect into practical orchard layout.",
        ],
      },
    ],
    sources: [
      {
        text: "USGS, \"Topographic Maps.\" https://www.usgs.gov/programs/national-geospatial-program/topographic-maps",
        name: "USGS, \"Topographic Maps.\"",
        url: "https://www.usgs.gov/programs/national-geospatial-program/topographic-maps",
      },
      {
        text: "Sibling: [The National Map: a base is not a site plan](/guides/national-map)",
        name: "Sibling: The National Map: a base is not a site plan",
        url: "https://plantgeo.aevani.com/guides/national-map",
      },
    ],
  },
];

export function guideBySlug(slug: string): GuideArticle | undefined {
  return GUIDES.find((page) => page.slug === slug);
}

export function guidePath(page: Pick<GuideArticle, "slug">): string {
  return `/guides/${page.slug}`;
}

export const GUIDE_PATHS = GUIDES.map((page) => guidePath(page));
