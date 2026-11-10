/**
 * Field-guide copy from the reviewed drafts. The sentences are the draft text.
 * The drafts' JSON-LD notes are structured data on the page, not a visible section.
 * The index follows the site-types research index. Its figures are the ones the
 * child pages cite, and it does not add a number the drafts do not carry.
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
  h1: "Write down the layer, and the date you read it.",
  seoTitle: "Guides | PlantGeo",
  metaDescription: "Field guides to FEMA flood maps, Ready.gov floods, National Wetlands Inventory, USGS 3DEP, and The National Map. No forecasts, and no insurance advice.",
  eyebrow: "Five field guides · an official layer is not a plan",
  publishedAt: "2026-11-10",
  lastReviewed: "2026-11-10",
  blocks: [
    {
      type: "paragraph",
      text: "These pages are how-tos for official layers a planner actually opens: a FEMA flood map, Ready.gov’s reading of that map, the National Wetlands Inventory, the USGS 3D Elevation Program, and The National Map. Each one stays inside the page that was opened. The figures are other people’s, with the links left on. This is not insurance advice, not a survey, and not a permit. PlantGeo publishes no forecasts and claims no agency deployments.",
    },
    {
      type: "paragraph",
      text: "The shared failure is reading a line on a map as a decision the map did not make: a community flood map treated as a lot survey, a printed flood line treated as a forecast, a wetlands polygon treated as a permit, a hillshade treated as the elevation source, or a topo screenshot treated as a site plan. The guides below keep the official name and the limit in the same paragraph.",
    },
    {
      type: "heading",
      text: "The five guides",
    },
    {
      type: "list",
      items: [
        "**[FEMA flood maps](/guides/fema-flood-maps)** — The FEMA Flood Map Service Center (MSC) is the official online location to find all flood hazard mapping products created under the National Flood Insurance Program (FEMA, “Flood Maps”). A FIRM is that community map, not a survey of one parcel and not a promise about where water will go.",
        "**[Flooding doesn't follow lines on a map](/guides/ready-floods)** — “Floods are the most common disaster in the United States” (Ready.gov). The same page says flooding doesn't follow lines on a map, and a following list item is “Where it rains it can flood.”",
        "**[National Wetlands Inventory maps](/guides/nwi-wetlands)** — The U.S. Fish and Wildlife Service produces and distributes maps and other geospatial data on American wetland and deepwater habitats, and monitors change through time (FWS). A polygon on that public inventory is not a permit, and no accuracy or acreage is stated here.",
        "**[USGS 3D Elevation Program](/guides/usgs-3dep)** — A USGS caption dates the national baseline of high-resolution elevation data, bare earth and 3D point clouds, as of September 30, 2026. That date belongs to the map shown on the program page. It is not an accuracy in feet, and it is not a claim that every acre is equally detailed.",
        "**[The National Map](/guides/national-map)** — “The National Geospatial Program and its partners acquire and integrate authoritative geospatial data” and make the resulting layers available through The National Map (USGS). A base is not a site plan.",
      ],
    },
    {
      type: "heading",
      text: "What PlantGeo is doing here",
    },
    {
      type: "paragraph",
      text: "The map is the product, and [About](https://plantgeo.aevani.com/about) already says how the layers are framed. These guides sit next to the [site types](/site-types). House notes: [Not forecast beyond today](https://plantgeo.aevani.com/blog/not-forecast-beyond-today) and [Plan the land, then pick the tools](https://plantgeo.aevani.com/blog/plan-the-land-then-pick-the-tools). If tools come after the plan, they come from [Aevani’s catalogue](https://aevani.com), which is a preview and not for sale yet. No referral share is quoted, because the share has not been set. Site notes, when there are any, live in [News](/news).",
    },
    {
      type: "heading",
      text: "An illustrative scenario",
    },
    {
      type: "paragraph",
      text: "The five guides each close with a worked example. The label on those pages is the limit: illustrative, not a customer, not a survey, and not a permit.",
    },
    {
      type: "heading",
      text: "Sources",
    },
    {
      type: "paragraph",
      text: "Every figure on this page is cited on the guide that uses it. The rows below are those official pages.",
    },
    {
      type: "sources",
    },
    {
      type: "paragraph",
      text: "The useful next step is to open the guide for the layer in front of you, or to start from the [site types](/site-types). If something on this page is wrong, [write to us](/about).",
    },
    {
      type: "paragraph",
      text: "Author: Escherbridge. Last reviewed: 2026-11-10, America/Denver.",
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
  ],
};

export const GUIDES: readonly GuideArticle[] = [
  {
    h1: "FEMA flood maps: how to find a FIRM",
    seoTitle: "FEMA flood maps: how to find a FIRM — Guides | PlantGeo",
    metaDescription: "FEMA’s Flood Map Service Center is the official place to find NFIP flood maps. This guide covers what a FIRM and an SFHA are, and how to print a FIRMette.",
    eyebrow: "FEMA flood maps",
    slug: "fema-flood-maps",
    crumb: "FEMA flood maps",
    publishedAt: "2026-11-10",
    lastReviewed: "2026-11-10",
    blocks: [
      {
        type: "paragraph",
        text: "FEMA's Flood Map Service Center is the official online place to find National Flood Insurance Program maps. A Flood Insurance Rate Map is an official map of a community — Special Flood Hazard Areas, Base Flood Elevations, and risk premium zones — not a finished reading of one parcel. The official map can tell you how FEMA has delineated flood hazard for that community. It is not a survey of the lot, and it is not a promise about where water will go. The household companion is [Flooding doesn't follow lines on a map](/guides/ready-floods).",
      },
      {
        type: "paragraph",
        text: "This is a how-to about official flood-hazard maps, not a PlantGeo product page, not a survey, and not insurance advice. It is not a LOMA filing guide.",
      },
      {
        type: "heading",
        text: "Hero figures",
      },
      {
        type: "list",
        items: [
          "**FEMA, \"Flood Maps\":** \"The FEMA Flood Map Service Center (MSC) is the official online location to find all flood hazard mapping products created under the National Flood Insurance Program, including your community's flood map.\" \"Flood maps help mortgage lenders determine insurance requirements and help communities develop strategies for reducing their risk.\"",
          "**FEMA glossary, \"Flood Insurance Rate Map (FIRM),\" last updated 8 July 2020:** \"Official map of a community on which FEMA has delineated the Special Flood Hazard Areas (SFHAs), the Base Flood Elevations (BFEs) and the risk premium zones applicable to the community.\"",
          "**FEMA glossary, \"Special Flood Hazard Area (SFHA),\" last updated 7 July 2020:** an SFHA is shown on a Flood Hazard Boundary Map (FHBM) or a FIRM in named zones including \"Zone A, AO, A1-A30, AE, A99, AH, AR, AR/A, AR/AE, AR/AH, AR/AO, AR/A1-A30, V1-V30, VE or V.\" \"The SFHA is the area where the National Flood Insurance Program's (NFIP's) floodplain management regulations must be enforced and the area where the mandatory purchase of flood insurance applies.\" For Community Rating System premium discounts, \"all AR and A99 zones are treated as non-SFHAs.\" That last sentence is FEMA's glossary note, not advice to buy or not buy a policy.",
        ],
      },
      {
        type: "heading",
        text: "Where to look, and the names on the map",
      },
      {
        type: "paragraph",
        text: "Start at the MSC. It is the official online location for NFIP mapping products, including the community's flood map. A FIRM is a community map: SFHAs, BFEs, and risk premium zones, in the glossary's words. An SFHA is the area where NFIP floodplain-management regulations must be enforced and where the mandatory purchase of flood insurance applies, again in the glossary's words. The zone list is FEMA's. This page does not invent a \"Zone X means safe,\" and it does not add a \"100-year\" or \"1-percent-annual-chance\" sentence that was not on the opened SFHA glossary page.",
      },
      {
        type: "heading",
        text: "A FIRMette",
      },
      {
        type: "paragraph",
        text: "A FIRMette, in the MSC FAQs, \"is a full-scale section of a FIRM that you create yourself and is formatted to print on most home/office printers.\" You first find a flood map, then use the View button associated with it. The click path belongs on the FAQ, which will be updated when the interface changes; this page does not invent a screenshot tutorial that will rot.",
      },
      {
        type: "paragraph",
        text: "This page does not treat a printed line as a guarantee that flooding will stay inside it. [Ready.gov's floods page](/guides/ready-floods) is the companion that says so in official household language. It also does not invent Ada County flood counts, and it does not claim an agency deployment.",
      },
      {
        type: "heading",
        text: "What to write down",
      },
      {
        type: "paragraph",
        text: "A site plan still has to say which effective map, which panel, and which zone was read, and on which date. [The National Map](/guides/national-map) is the official base-map home if you are assembling layers. The [riparian and watershed site type](/site-types/riparian-watershed-edges) is the land-kind sibling for an edge that already has water in the name.",
      },
      {
        type: "heading",
        text: "An illustrative scenario",
      },
      {
        type: "paragraph",
        text: "This is a worked example, not a customer and not a survey. A parcel is described as \"not in the floodplain\" because a neighbor's printed map, undated and unlabelled, showed a line on the other side of a road. The useful next step is the MSC, the effective panel, the zone as printed, and the date it was read.",
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
        text: "Also in this section",
      },
      {
        type: "list",
        items: [
          "[Flooding doesn't follow lines on a map](/guides/ready-floods) — Ready.gov's household reading of the same official map.",
          "[National Wetlands Inventory maps](/guides/nwi-wetlands) — a public inventory, not a permit.",
          "[The National Map](/guides/national-map) — the official base, not a finished plan.",
          "[USGS 3D Elevation Program](/guides/usgs-3dep) — elevation you can name as a source.",
        ],
      },
      {
        type: "paragraph",
        text: "PlantGeo publishes no forecasts. The Aevani catalogue, if it is mentioned at all, is a preview and not for sale yet. Affiliate applications, when they are open, do not have a quoted referral share; the share has not been set. The [site-types index](/site-types) is the land-kind companion to these official-layer guides.",
      },
      {
        type: "paragraph",
        text: "The useful next step is to open the MSC and write down the panel, the zone, and the date, or to read [Ready.gov's floods page](/guides/ready-floods). If something on this page is wrong, [write to us](/about).",
      },
      {
        type: "paragraph",
        text: "Author: Escherbridge. Last reviewed: 2026-11-10, America/Denver.",
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
    metaDescription: "Ready.gov says to look up flood risk on FEMA’s map, and that flooding doesn’t follow lines on a map. This is that household reading, not insurance advice.",
    eyebrow: "Ready.gov on floods",
    slug: "ready-floods",
    crumb: "Flooding doesn't follow lines on a map",
    publishedAt: "2026-11-10",
    lastReviewed: "2026-11-10",
    blocks: [
      {
        type: "paragraph",
        text: "Ready.gov tells households to look up flood risk on FEMA's Flood Map Service Center — and then, in the next breath, that flooding doesn't follow lines on a map: \"Where it rains it can flood.\" Look at the official map. Then remember that water does not owe the map a courtesy.",
      },
      {
        type: "paragraph",
        text: "This is a household preparedness note, not a flood-insurance ad, not advice to buy or not buy a policy, and not a claim that PlantGeo models flood risk. The official map itself is covered in [FEMA flood maps](/guides/fema-flood-maps). This page does not contradict that guide; it is the companion.",
      },
      {
        type: "heading",
        text: "Hero figures",
      },
      {
        type: "list",
        items: [
          "**Ready.gov, \"Floods\":** \"Floods are the most common disaster in the United States.\" \"Failing to evacuate flooded areas or entering flood waters can lead to injury or death.\" Floods \"may: Result from rain, snow, coastal storms, storm surges and overflows of dams and other water systems. Develop slowly or quickly. Flash floods can come without warning. Cause outages, disrupt transportation, damage buildings and create landslides.\" Under a flood warning: \"Find safe shelter right away. Do not walk, swim or drive through flood waters.\"",
          "**The same page, before a flood:** \"Know your risk for floods, visit FEMA's Flood Map Service Center to know types of flood risks in your area. However, remember that flooding doesn't follow lines on a map.\" A following item on the same list is: \"Where it rains it can flood.\" It also points at community warning systems, the Emergency Alert System, and NOAA Weather Radio.",
          "**Insurance, in Ready.gov's words, not as a sales pitch:** \"Get flood coverage under FEMA's National Flood Insurance Program (NFIP). Homeowner's insurance policies do not cover flooding. It typically takes up to 30 days for a policy to go into effect, so the time to buy is well before a disaster.\" That is Ready.gov's preparedness fact. This page does not recommend a policy, a quote site, or a PlantGeo product. A later sentence on the same page points at floodsmart.gov; if you follow it, follow it as Ready.gov's pointer, not as a house endorsement.",
        ],
      },
      {
        type: "heading",
        text: "Ready.gov's own sequence",
      },
      {
        type: "paragraph",
        text: "Know your risk at the MSC. Flooding doesn't follow lines on a map. Where it rains it can flood. Sign up for warnings. The common-disaster facts stay as Ready.gov stated them: the most common disaster in the United States; do not walk, swim, or drive through flood waters; flash floods can come without warning. This page does not invent fatality counts or dollar-damage totals.",
      },
      {
        type: "paragraph",
        text: "Homeowner's policies do not cover flooding, in Ready.gov's words. Thirty days is Ready.gov's figure for a policy to go into effect. That is a limit, not advice to buy or not buy, and this page invents no premiums, waiting-period exceptions, or \"you must buy.\"",
      },
      {
        type: "heading",
        text: "What to write down",
      },
      {
        type: "paragraph",
        text: "A site reading can cite the effective map and still has to leave room for water that does not follow the line. [FEMA flood maps](/guides/fema-flood-maps) is where to name the panel and the zone. The [riparian and watershed site type](/site-types/riparian-watershed-edges) is the land-kind sibling. PlantGeo has not modeled a watershed or a parcel as a flood product, and this page does not claim otherwise.",
      },
      {
        type: "heading",
        text: "An illustrative scenario",
      },
      {
        type: "paragraph",
        text: "This is a worked example, not a customer. A house sits just outside a printed SFHA line. The official map was read, the zone was written down, and then a summer storm put water across the drive. The Ready.gov sentence was already the warning: flooding doesn't follow lines on a map.",
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
        text: "Also in this section",
      },
      {
        type: "list",
        items: [
          "[FEMA flood maps: how to find a FIRM](/guides/fema-flood-maps) — the official community map, and its limits.",
          "[National Wetlands Inventory maps](/guides/nwi-wetlands) — a public inventory of wetland and deepwater habitats.",
          "[USGS 3D Elevation Program](/guides/usgs-3dep) — elevation as a named source, not a hillshade on a slide.",
        ],
      },
      {
        type: "paragraph",
        text: "PlantGeo publishes no forecasts. The Aevani catalogue is a preview and not for sale yet. No referral share is quoted. The [site-types index](/site-types) is the land-kind companion.",
      },
      {
        type: "paragraph",
        text: "The useful next step is to read the official map and the Ready.gov page together, and to sign up for the warnings that page names. If something on this page is wrong, [write to us](/about).",
      },
      {
        type: "paragraph",
        text: "Author: Escherbridge. Last reviewed: 2026-11-10, America/Denver.",
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
    metaDescription: "The National Wetlands Inventory maps wetland and deepwater habitats, and change over time. This guide covers what the inventory is, and what a polygon is not.",
    eyebrow: "National Wetlands Inventory",
    slug: "nwi-wetlands",
    crumb: "National Wetlands Inventory maps",
    publishedAt: "2026-11-10",
    lastReviewed: "2026-11-10",
    blocks: [
      {
        type: "paragraph",
        text: "The U.S. Fish and Wildlife Service's National Wetlands Inventory produces and distributes maps and geospatial data on the nation's wetland and deepwater habitats, and on change in those habitats over time. That public inventory is not, by itself, a decision about a permit or a property line. Use it as what the Service says it is: information on extent, status, and change. Do not ask it to close a permit, a delineation, or a property dispute.",
      },
      {
        type: "paragraph",
        text: "This is a field note about a public inventory, not a permitting desk, not legal advice, and not a jurisdictional determination. The opened source for this page is the program landing. It is a short official page, and this guide stays inside it. A later draft can quote the mapper's documentation or a standards page if those are opened; this page does not invent an accuracy radius, a national acreage, or a disclaimer attributed to FWS that was not on the program landing.",
      },
      {
        type: "heading",
        text: "Hero figures",
      },
      {
        type: "list",
        items: [
          "**U.S. Fish and Wildlife Service, National Wetlands Inventory program page:** \"The U.S. Fish and Wildlife Service is the principal federal agency tasked with providing information to the public on the extent and status of the nation's wetland and deepwater habitats, as well as changes to these habitats over time.\"",
          "**The same page, on what the program produces:** \"We produce and distribute maps and other geospatial data on American wetland and deepwater habitats, as well as monitor changes in these habitats through time.\" The page points at the Wetlands Mapper, wetlands data, and mapping status and trends.",
        ],
      },
      {
        type: "heading",
        text: "What the program actually does",
      },
      {
        type: "paragraph",
        text: "The Service is the principal federal agency for public information on wetland and deepwater habitats. It produces maps and other geospatial data, and it monitors change through time. The mapper and the data downloads are the places the program page itself names. That is the whole official job description used here.",
      },
      {
        type: "paragraph",
        text: "A polygon on a public map can support a question. It does not close a permit, a delineation, or a property dispute. That limit is the argument of this page, not a sentence quoted from FWS on the program landing. If you need the Service's own accuracy language, or its own \"not a jurisdictional determination\" wording, open the mapper documentation or a standards page and quote it there.",
      },
      {
        type: "paragraph",
        text: "This page does not treat NWI as the same product as a soil-survey hydric rating. That is a later topic, and it needs its own opened NRCS page. It also does not invent Ada County wetland counts.",
      },
      {
        type: "heading",
        text: "What to write down",
      },
      {
        type: "paragraph",
        text: "A site reading can name the inventory, the date consulted, and the limit: a public inventory of extent, status, and change, not a legal finding. The [riparian and watershed site type](/site-types/riparian-watershed-edges) is the land-kind sibling. [Homestead soils](/site-types/homestead-mapped-soils) is the soils sibling, and is not a substitute for this inventory. [The National Map](/guides/national-map) is the official base if you are assembling layers. [FEMA flood maps](/guides/fema-flood-maps) is a different official product, about flood hazard for a community, not wetland habitat.",
      },
      {
        type: "heading",
        text: "An illustrative scenario",
      },
      {
        type: "paragraph",
        text: "This is a worked example, not a customer and not a delineation. A planning note says \"the wetlands overlay is clear,\" with no date, no inventory name, and no limit. A later reviewer treats the polygon as a permit decision. The useful record would have named NWI, the date it was read, and what a public inventory can and cannot close.",
      },
      {
        type: "heading",
        text: "Sources",
      },
      {
        type: "paragraph",
        text: "Every stat on this page is cited. The official source here is thin on purpose: one program landing, opened 10 November 2026. No extra figures were added.",
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
          "[FEMA flood maps: how to find a FIRM](/guides/fema-flood-maps) — a community flood-hazard map, not a wetland inventory.",
          "[The National Map](/guides/national-map) — the official base geospatial suite.",
          "[USGS 3D Elevation Program](/guides/usgs-3dep) — elevation as a named source.",
        ],
      },
      {
        type: "paragraph",
        text: "PlantGeo publishes no forecasts. The Aevani catalogue is a preview and not for sale yet. No referral share is quoted. The [site-types index](/site-types) is the land-kind companion.",
      },
      {
        type: "paragraph",
        text: "The useful next step is to name the inventory and the date, or to open the program page itself. If something on this page is wrong, [write to us](/about).",
      },
      {
        type: "paragraph",
        text: "Author: Escherbridge. Last reviewed: 2026-11-10, America/Denver.",
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
    metaDescription: "The USGS 3D Elevation Program is the path to high-resolution elevation data. This guide covers what 3DEP publishes, and why a hillshade is not a source.",
    eyebrow: "3D Elevation Program",
    slug: "usgs-3dep",
    crumb: "USGS 3D Elevation Program",
    publishedAt: "2026-11-10",
    lastReviewed: "2026-11-10",
    blocks: [
      {
        type: "paragraph",
        text: "The U.S. Geological Survey's 3D Elevation Program is the official path to consistent, high-resolution elevation data — bare-earth surfaces and 3D point clouds — for the United States and its territories. A hillshade on a slide is not a substitute for naming that source. If you are going to talk about slope or drainage, say which elevation source you used. 3DEP is the USGS program that exists for that.",
      },
      {
        type: "paragraph",
        text: "This is a field note about official elevation data, not a grading-plan service, and not a claim that PlantGeo has flown or processed lidar.",
      },
      {
        type: "heading",
        text: "Hero figures",
      },
      {
        type: "list",
        items: [
          "**USGS, 3D Elevation Program.** The opened page describes 3DEP data products and points at quality levels (including \"What is QL0?\" and \"How are Lidar QLs defined?\"), spatial metadata, partner data acquisition, and standards.",
          "**A caption on the same page** states that a map of the United States and its territories \"shows the completion of the first-ever national baseline of consistent high-resolution elevation data - both bare earth and 3D point clouds - including baseline and next generation coverage as of September 30, 2026.\" That is the page's own caption about the map shown, not a claim that every acre is equally detailed, and not a quality-level guarantee.",
          "**A pointer, not a parcel benefit:** the same page points at the 3D Nation Study: \"Study documents $13.5 billion of benefits for 1,352 mission critical requirements for topographic and inland, nearshore, and offshore bathymetric 3D elevation data.\" That figure is the study's, as stated on the USGS 3DEP page. It is not a PlantGeo finding, and it is not a benefit that accrues to a single parcel. The study PDF was not opened for this brief, so the sentence is a pointer, not a re-analysis.",
        ],
      },
      {
        type: "heading",
        text: "What the program publishes, as the page names it",
      },
      {
        type: "paragraph",
        text: "High-resolution data; bare earth and 3D point clouds; quality levels and spatial metadata as the places to read what a given work unit actually is. This page does not invent a nationwide accuracy in feet, a lidar pulse density, or \"QL0 everywhere.\" Quality still has to be read from the work-unit metadata, even where the caption describes a national baseline of consistent high-resolution data.",
      },
      {
        type: "paragraph",
        text: "[The National Map](/guides/national-map) is the suite that uses elevation, among other base layers, as a source for cartographic products. A 3DEP dataset is not, by itself, a grading plan. A later slope-and-erosion guide can take that up when its own official pages are opened.",
      },
      {
        type: "heading",
        text: "What to write down",
      },
      {
        type: "paragraph",
        text: "A site reading can name the 3DEP source and the date consulted, and it can point at the quality-level and spatial metadata for the work unit. It is not a grading plan, and it does not invent Ada County elevation statistics.",
      },
      {
        type: "heading",
        text: "An illustrative scenario",
      },
      {
        type: "paragraph",
        text: "This is a worked example, not a customer. A slide shows a hillshade of a parcel, with no program name, no work unit, and no quality level. A later discussion treats the pretty slope as a measured surface. The useful record would have named 3DEP, the date, and the metadata that says what that work unit actually is.",
      },
      {
        type: "heading",
        text: "Sources",
      },
      {
        type: "paragraph",
        text: "Every stat on this page is cited. The official source here is the program landing, opened 10 November 2026.",
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
          "[The National Map](/guides/national-map) — the official base-map suite that uses elevation, among other layers.",
          "[FEMA flood maps: how to find a FIRM](/guides/fema-flood-maps) — a community flood-hazard map, not a substitute for named elevation.",
          "[Flooding doesn't follow lines on a map](/guides/ready-floods) — the household limit on a printed boundary.",
        ],
      },
      {
        type: "paragraph",
        text: "PlantGeo publishes no forecasts. The Aevani catalogue is a preview and not for sale yet. No referral share is quoted. The [site-types index](/site-types) is the land-kind companion.",
      },
      {
        type: "paragraph",
        text: "The useful next step is to name the elevation source and read the work-unit metadata, or to open the 3DEP page itself. If something on this page is wrong, [write to us](/about).",
      },
      {
        type: "paragraph",
        text: "Author: Escherbridge. Last reviewed: 2026-11-10, America/Denver.",
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
    metaDescription: "The National Map is the USGS suite of base geospatial products, including US Topo. This guide covers what that base is, and why it is not a site plan.",
    eyebrow: "The National Map",
    slug: "national-map",
    crumb: "The National Map",
    publishedAt: "2026-11-10",
    lastReviewed: "2026-11-10",
    blocks: [
      {
        type: "paragraph",
        text: "The National Map is the USGS suite of products, services, and applications for base geospatial information — topography, natural landscape, and built environment — including US Topo, on-demand topo, downloads, and historical topographic maps. It is a base, not a finished plan. Start from an authoritative base. Then say, in the same breath, that a base is not a site plan.",
      },
      {
        type: "paragraph",
        text: "This is a field note about official base maps, not a claim that PlantGeo is a mapping agency, and not a property survey.",
      },
      {
        type: "heading",
        text: "Hero figures",
      },
      {
        type: "list",
        items: [
          "**USGS, \"The National Map\":** \"The National Map is a suite of products, services, and applications that provide access to base geospatial information representing the topography, natural landscape, and built environment of the United States and its territories.\" It \"supports digital and print versions of topographic maps, data download, web map services, and online viewing of maps and data.\"",
          "**How the data are assembled, in USGS's words:** \"The National Geospatial Program and its partners acquire and integrate authoritative geospatial data from a variety of sources and makes the resulting nationally consistent geospatial data layers available to all through The National Map.\"",
          "**What it serves:** it \"serves as the data source for cartographic products, such as US Topo and OnDemand Topo maps, and provides access to digital historical topographic maps.\" The page also points at standards for elevation, hydrography, and topographic maps, The National Map Viewer, and training.",
        ],
      },
      {
        type: "heading",
        text: "What the suite actually is",
      },
      {
        type: "paragraph",
        text: "Products, services, and applications; digital and print topo; downloads and web services; US Topo, OnDemand Topo, and historical topo; nationally consistent layers from USGS and partners. This page does not invent a layer count, an update cycle, or an \"eight layers\" list that was not extracted from the opened landing.",
      },
      {
        type: "paragraph",
        text: "Elevation standards on that page connect to [3DEP](/guides/usgs-3dep). Hydrography standards connect to already-briefed NHD / 3DHP material on the riparian site-type page; neither is a watershed plan by itself. [FEMA flood maps](/guides/fema-flood-maps) and the [National Wetlands Inventory](/guides/nwi-wetlands) are official products with their own jobs; they are not National Map substitutes, and The National Map is not a substitute for them.",
      },
      {
        type: "paragraph",
        text: "A US Topo is not a property survey or a grading plan.",
      },
      {
        type: "heading",
        text: "What to write down",
      },
      {
        type: "paragraph",
        text: "A site reading can name The National Map product used and the date. The Ada County sample, if it is mentioned at all, is a sample on real map data and not a client. The [homestead soils](/site-types/homestead-mapped-soils) and [WUI](/site-types/wui-parcel) pages are land-kind siblings, not agency deployments.",
      },
      {
        type: "heading",
        text: "An illustrative scenario",
      },
      {
        type: "paragraph",
        text: "This is a worked example, not a customer. A planning packet is titled \"site plan\" and contains only an undated topo screenshot, with no product name. A later reader treats the screenshot as a design. The useful record would have named the National Map product, the date, and the fact that a base is not a plan.",
      },
      {
        type: "heading",
        text: "Sources",
      },
      {
        type: "paragraph",
        text: "Every stat on this page is cited. The official source here is the program landing, opened 10 November 2026.",
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
          "[USGS 3D Elevation Program](/guides/usgs-3dep) — the official elevation program the base map leans on.",
          "[FEMA flood maps: how to find a FIRM](/guides/fema-flood-maps) — a community flood-hazard map.",
          "[National Wetlands Inventory maps](/guides/nwi-wetlands) — a public inventory of wetland and deepwater habitats.",
        ],
      },
      {
        type: "paragraph",
        text: "PlantGeo publishes no forecasts. The Aevani catalogue is a preview and not for sale yet. No referral share is quoted. The [site-types index](/site-types) is the land-kind companion. [Aevani's growing guides](https://aevani.com/guides) are the catalogue-side sibling when the question turns from the parcel to a growing system; the catalogue itself is still not for sale.",
      },
      {
        type: "paragraph",
        text: "The useful next step is to name the National Map product and the date, or to open the USGS page itself. If something on this page is wrong, [write to us](/about).",
      },
      {
        type: "paragraph",
        text: "Author: Escherbridge. Last reviewed: 2026-11-10, America/Denver.",
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
];

export function guideBySlug(slug: string): GuideArticle | undefined {
  return GUIDES.find((page) => page.slug === slug);
}

export function guidePath(page: Pick<GuideArticle, "slug">): string {
  return `/guides/${page.slug}`;
}

export const GUIDE_PATHS = GUIDES.map((page) => guidePath(page));
