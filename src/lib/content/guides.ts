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
  metaDescription: "Ten field guides to official layers: FEMA flood maps, the NFHL, Ready.gov wildfires, NWI, USGS 3DEP, and US Topo. No forecasts and no insurance advice.",
  eyebrow: "Ten field guides · an official layer is not a plan",
  publishedAt: "2026-11-10",
  lastReviewed: "2026-11-10",
  blocks: [
    {
      type: "paragraph",
      text: "These pages are how-tos for official layers a planner actually opens: a FEMA flood map, a base flood elevation, a FIRMette, effective and preliminary flood data, Ready.gov’s reading of floods and of wildfires, the National Wetlands Inventory, the USGS 3D Elevation Program, The National Map, and US Topo. Each one stays inside the page that was opened. The figures are other people’s, with the links left on. This is not insurance advice, not a survey, and not a permit. PlantGeo publishes no forecasts and claims no agency deployments.",
    },
    {
      type: "paragraph",
      text: "The shared failure is reading a line on a map as a decision the map did not make: a community flood map treated as a lot survey, a printed flood line treated as a forecast, a wetlands polygon treated as a permit, a hillshade treated as the elevation source, a topo screenshot treated as a site plan, a preliminary panel filed as a FIRMette, or a Ready.gov distance treated as a Firewise width. The guides below keep the official name and the limit in the same paragraph.",
    },
    {
      type: "heading",
      text: "The ten guides",
    },
    {
      type: "list",
      items: [
        "**[FEMA flood maps](/guides/fema-flood-maps)** — The FEMA Flood Map Service Center (MSC) is the official online location to find all flood hazard mapping products created under the National Flood Insurance Program (FEMA, “Flood Maps”). A FIRM is that community map, not a survey of one parcel and not a promise about where water will go.",
        "**[Flooding doesn't follow lines on a map](/guides/ready-floods)** — “Floods are the most common disaster in the United States” (Ready.gov). The same page says flooding doesn't follow lines on a map, and a following list item is “Where it rains it can flood.”",
        "**[National Wetlands Inventory maps](/guides/nwi-wetlands)** — The U.S. Fish and Wildlife Service produces and distributes maps and other geospatial data on American wetland and deepwater habitats, and monitors change through time (FWS). A polygon on that public inventory is not a permit, and no accuracy or acreage is stated here.",
        "**[USGS 3D Elevation Program](/guides/usgs-3dep)** — A USGS caption dates the national baseline of high-resolution elevation data, bare earth and 3D point clouds, as of September 30, 2026. That date belongs to the map shown on the program page. It is not an accuracy in feet, and it is not a claim that every acre is equally detailed.",
        "**[The National Map](/guides/national-map)** — “The National Geospatial Program and its partners acquire and integrate authoritative geospatial data” and make the resulting layers available through The National Map (USGS). A base is not a site plan.",
        "**[What is a base flood elevation](/guides/base-flood-elevation)** — FEMA’s glossary, last updated 5 March 2020, defines the BFE as the elevation of surface water from a flood that has a 1% chance of equaling or exceeding that level in any given year, on the named FIRM zones. This page does not invent a BFE for Zone A, and the number is not a finished floor.",
        "**[What is a FIRMette](/guides/firmette)** — FEMA’s FAQ says a printed FIRM is about two feet by three feet, and a FIRMette is a full-scale section formatted for a home or office printer. A FIRMette cannot be created for a preliminary map.",
        "**[Effective and preliminary flood maps](/guides/nfhl-preliminary-vs-effective)** — The NFHL holds current effective flood hazard data, and FEMA says that digital data covers over 90% of the U.S. population. That figure is population, not land area. Preliminary maps are not final regulatory products.",
        "**[Ready.gov wildfire preparedness](/guides/ready-wildfires)** — Ready.gov advises a fire-resistant zone free of leaves, debris, or flammable materials for at least 30 feet from the home. That distance is Ready.gov’s. It is not a Firewise or IBHS width.",
        "**[US Topo and historical topographic maps](/guides/us-topo)** — USGS has been the primary civilian mapping agency of the United States since 1879, and in 2009 it moved from hand-scribed historical maps to US Topo. The historical collection runs from 1884 to 2006. A topo is still not a site plan.",
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
      text: "The ten guides each close with a worked example. The label on those pages is the limit: illustrative, not a customer, not a survey, and not a permit.",
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
          "[What is a base flood elevation (BFE)](/guides/base-flood-elevation) — the number on the map is an elevation, not a finished floor.",
          "[What is a FIRMette and how to print one](/guides/firmette) — a full-scale piece of an effective map.",
          "[Effective and preliminary flood maps](/guides/nfhl-preliminary-vs-effective) — which dataset you are looking at.",
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
          "[What is a base flood elevation (BFE)](/guides/base-flood-elevation) — the number on the map is an elevation, not a finished floor.",
          "[Ready.gov wildfire preparedness](/guides/ready-wildfires) — Ready.gov's at least 30 feet, kept separate from the Firewise and IBHS widths.",
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
          "[US Topo and historical topographic maps](/guides/us-topo) — say which topo you used. A topo is still not a site plan.",
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
          "[US Topo and historical topographic maps](/guides/us-topo) — a current US Topo, an OnDemand sheet, or a historical quadrangle. None of them is a site plan.",
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
  {
    h1: "What is a base flood elevation (BFE)",
    seoTitle: "What is a base flood elevation (BFE) — Guides | PlantGeo",
    metaDescription: "FEMA defines the base flood elevation as the height of a 1% annual-chance flood on named FIRM zones. It is not a finished floor, and not a survey.",
    eyebrow: "Base flood elevation",
    slug: "base-flood-elevation",
    crumb: "What is a base flood elevation",
    publishedAt: "2026-11-10",
    lastReviewed: "2026-11-10",
    blocks: [
      {
        type: "paragraph",
        text: "The BFE is an elevation FEMA has delineated for a flood that has a 1% chance of equaling or exceeding that level in any given year, shown on the FIRM for named zones. It is not, by itself, a finished floor height, and it is not a survey of one lot. This is a field note about a number on an official map, not a grading desk, and not insurance or building-code advice.",
      },
      {
        type: "heading",
        text: "Hero figures",
      },
      {
        type: "list",
        items: [
          "**FEMA glossary, \"Base Flood Elevation (BFE),\" last updated 5 March 2020.** \"The elevation of surface water resulting from a flood that has a 1% chance of equaling or exceeding that level in any given year. The BFE is shown on the Flood Insurance Rate Map (FIRM) for zones AE, AH, A1–A30, AR, AR/A, AR/AE, AR/A1–A30, AR/AH, AR/AO, V1–V30 and VE.\" The zone list is quoted as FEMA named it. This page does not invent a BFE for Zone A or for other zones not on that glossary sentence.",
          "**The 1% annual-chance wording** is FEMA's. It is not collapsed here into an unsourced \"100-year flood\" guarantee. If that familiar phrase is used at all, it is only as a name for the same official chance, in FEMA's 1% wording.",
          "**Already opened siblings.** FEMA's FIRM glossary already names BFEs as one of the things FEMA delineates on the official community map, along with SFHAs and risk premium zones. Ready.gov's flood page already warns that flooding doesn't follow lines on a map.",
        ],
      },
      {
        type: "heading",
        text: "The limit",
      },
      {
        type: "paragraph",
        text: "[FEMA flood maps: how to find a FIRM](/guides/fema-flood-maps) is the place to name the panel, the zone, and the effective date. [Flooding doesn't follow lines on a map](/guides/ready-floods) is the household reminder that water is not obliged to stop at the printed line. [What is a FIRMette and how to print one](/guides/firmette) and [Effective and preliminary flood maps](/guides/nfhl-preliminary-vs-effective) are the companions for a printable piece of the official map, and for which dataset you are looking at.",
      },
      {
        type: "paragraph",
        text: "This page does not invent freeboard, datum, or \"add two feet\" rules that were not on the opened BFE glossary. A site reading can name the effective map, the zone, the BFE as printed, and the date consulted. It cannot treat that number as a finished floor or as a survey of the lot.",
      },
      {
        type: "heading",
        text: "An illustrative scenario",
      },
      {
        type: "paragraph",
        text: "This is a worked example, not a customer and not a survey. A sketch labels a finished floor \"at the BFE\" after reading a number on a map, with no survey, no datum, and no zone check. FEMA's glossary already says what the number is: a surface-water elevation for a 1% annual-chance flood, on named FIRM zones.",
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
          "[FEMA flood maps: how to find a FIRM](/guides/fema-flood-maps) — the official community map, and where to find it.",
          "[What is a FIRMette and how to print one](/guides/firmette) — a full-scale piece you can print, from an effective map.",
          "[Effective and preliminary flood maps](/guides/nfhl-preliminary-vs-effective) — which dataset you are looking at.",
          "[Flooding doesn't follow lines on a map](/guides/ready-floods) — water is not obliged to stop at the printed line.",
        ],
      },
      {
        type: "paragraph",
        text: "PlantGeo publishes no forecasts. The Aevani catalogue, if it is mentioned at all, is a preview and not for sale yet. No referral share is quoted. The [site-types index](/site-types) is the land-kind companion.",
      },
      {
        type: "paragraph",
        text: "The useful next step is to write down the effective map, the zone, the BFE as printed, and the date, or to read [FEMA flood maps](/guides/fema-flood-maps). If something on this page is wrong, [write to us](/about).",
      },
      {
        type: "paragraph",
        text: "Author: Escherbridge. Last reviewed: 2026-11-10, America/Denver.",
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
    metaDescription: "A FIRMette is a full-scale section of a FIRM, formatted to print on a home or office printer. FEMA’s FAQ says you cannot make one from a preliminary map.",
    eyebrow: "FIRMette",
    slug: "firmette",
    crumb: "What is a FIRMette",
    publishedAt: "2026-11-10",
    lastReviewed: "2026-11-10",
    blocks: [
      {
        type: "paragraph",
        text: "If you need a full-scale piece of the official map you can actually print, the official product is a FIRMette of an *effective* map — not a screenshot, and not a preliminary panel. This is a short how-to about that product, not a PlantGeo tool, and not a click-path tutorial that will rot. It is not a survey.",
      },
      {
        type: "heading",
        text: "Hero figures",
      },
      {
        type: "list",
        items: [
          "**FEMA Flood Map Service Center FAQs.** \"FEMA’s Flood Insurance Rate Maps (FIRMs) are approximately two feet by three feet in size when printed to full-scale. A FIRMette is a full-scale section of a FIRM that you create yourself and is formatted to print on most home/office printers.\" \"You can create a FIRMette of most effective maps in FEMA’s online inventory using the FIRMette Web tool. To create a FIRMette, you must first find a flood map, and then click the View button associated with that flood map.\" The official FAQ and tutorial are the place for the rest of the steps; this page does not invent a screenshot walkthrough.",
          "**The same FAQ, on preliminary maps:** \"Preliminary Flood Insurance Rate Maps are not final regulatory products and should not be used for the purposing of making flood determinations. Preliminary FIRMs cannot be viewed with the FIRMette tool, nor accessed with the FIRMette Desktop application. Therefore, a FIRMette cannot be created for preliminary maps.\" \"Purposing\" is the FAQ's own phrasing.",
          "**The National Flood Hazard Layer page** notes that in the NFHL Viewer you can use the NFHL Print Tool \"to download and print a full Flood Insurance Rate Map (FIRM) or FIRMette (a smaller, printable version of a FIRM) where NFHL data exists.\" That is a second official door, not a second definition.",
        ],
      },
      {
        type: "heading",
        text: "What it is, and what it is not",
      },
      {
        type: "paragraph",
        text: "A FIRMette is a full-scale section of a roughly two-by-three-foot FIRM, formatted for a home or office printer, created from most effective maps after you find the map and use View. Preliminary FIRMs are not final regulatory products and cannot be turned into a FIRMette with the official tools. [Effective and preliminary flood maps](/guides/nfhl-preliminary-vs-effective) is the sibling for that distinction. [FEMA flood maps: how to find a FIRM](/guides/fema-flood-maps) is where the search starts. [What is a base flood elevation (BFE)](/guides/base-flood-elevation) is the companion for a number printed on that map.",
      },
      {
        type: "paragraph",
        text: "A site reading can attach the FIRMette, the panel, the effective date, and the date consulted. A FIRMette of an effective map is still not a survey, and a preliminary image is not a FIRMette.",
      },
      {
        type: "heading",
        text: "An illustrative scenario",
      },
      {
        type: "paragraph",
        text: "This is a worked example, not a customer and not a survey. A screenshot of a preliminary panel is printed at letter size and filed as \"the FIRMette.\" The MSC FAQ already says a FIRMette cannot be created for preliminary maps, and that those maps are not final regulatory products.",
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
          "[FEMA flood maps: how to find a FIRM](/guides/fema-flood-maps) — find the official community map first.",
          "[Effective and preliminary flood maps](/guides/nfhl-preliminary-vs-effective) — which dataset you are looking at.",
          "[What is a base flood elevation (BFE)](/guides/base-flood-elevation) — the number on the map is an elevation, not a finished floor.",
        ],
      },
      {
        type: "paragraph",
        text: "PlantGeo publishes no forecasts. The Aevani catalogue, if it is mentioned at all, is a preview and not for sale yet. No referral share is quoted. The [site-types index](/site-types) is the land-kind companion.",
      },
      {
        type: "paragraph",
        text: "The useful next step is to find the effective map, make a FIRMette from it, and write down the panel and the date, or to read [FEMA flood maps](/guides/fema-flood-maps). If something on this page is wrong, [write to us](/about).",
      },
      {
        type: "paragraph",
        text: "Author: Escherbridge. Last reviewed: 2026-11-10, America/Denver.",
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
    metaDescription: "The National Flood Hazard Layer holds current effective flood data. Preliminary maps are an early look, not a final product for flood determinations.",
    eyebrow: "Effective and preliminary flood maps",
    slug: "nfhl-preliminary-vs-effective",
    crumb: "Effective and preliminary flood maps",
    publishedAt: "2026-11-10",
    lastReviewed: "2026-11-10",
    blocks: [
      {
        type: "paragraph",
        text: "The National Flood Hazard Layer is a geospatial database of *current effective* flood hazard data. Preliminary products are an early look that must still go through a formal review, and FEMA's MSC FAQ says they are not final regulatory products and should not be used to make flood determinations. This is a field note about which flood dataset you are looking at, not a LOMA filing desk, and not insurance advice.",
      },
      {
        type: "paragraph",
        text: "A dedicated FEMA URL for \"preliminary flood hazard data\" as a standalone path returned HTTP 404 on 2026-11-10. The quotations below are from the opened National Flood Hazard Layer page and the MSC FAQ.",
      },
      {
        type: "heading",
        text: "Hero figures",
      },
      {
        type: "list",
        items: [
          "**FEMA, \"The National Flood Hazard Layer.\"** \"The National Flood Hazard Layer (NFHL) is a geospatial database that contains current effective flood hazard data. FEMA provides the flood hazard data to support the National Flood Insurance Program.\" \"The NFHL can also be used in place of the FIRM for NFIP purposes with appropriate care.\" \"The NFHL is made from effective flood maps and Letters of Map Change (LOMC) delivered to communities.\" \"NFHL digital data covers over 90% of the U.S. population. New and revised data is being added continuously.\" \"If you need information for areas not covered by the NFHL data, there may be other FEMA products which provide coverage for those areas.\" The 90% figure is FEMA's coverage statement about population, not a claim about a particular county or parcel, and not \"90% of land area.\"",
          "**The same page, \"Preliminary Flood Hazard Data.\"** \"Preliminary flood hazard data provides the public an early look at their home or community’s projected risk to flood hazards. Before they become effective, the preliminary products go through a formal review period that provides the opportunity to appeal the preliminary determinations with better data.\" \"Preliminary FIRM products help community officials and property owners see how upcoming flood mapping changes will affect them. Community officials and property owners can also review the data for accuracy.\" \"Preliminary data may include new or revised Flood Insurance Rate Maps (FIRM), Flood Insurance Study (FIS) Reports and FIRM Databases.\" The page also names a Flood Map Changes Viewer for comparing preliminary data with existing data, and a following heading for Pending Flood Hazard Data. Pending is named only as a heading that exists; its definition is not invented here.",
          "**FEMA MSC FAQs:** \"Preliminary Flood Insurance Rate Maps are not final regulatory products and should not be used for the purposing of making flood determinations.\" A FIRMette cannot be created for preliminary maps.",
        ],
      },
      {
        type: "heading",
        text: "What to write down",
      },
      {
        type: "paragraph",
        text: "A site reading should say whether the layer was effective or preliminary, and the date consulted. [What is a FIRMette and how to print one](/guides/firmette) is the companion for a printable piece of an effective map. [FEMA flood maps: how to find a FIRM](/guides/fema-flood-maps) and [What is a base flood elevation (BFE)](/guides/base-flood-elevation) are the other official-map siblings. This page is not an appeal how-to, not a LOMA walkthrough, and not a survey.",
      },
      {
        type: "heading",
        text: "An illustrative scenario",
      },
      {
        type: "paragraph",
        text: "This is a worked example, not a customer and not a determination. Two images of the same street are on screen, one labelled preliminary and one unlabelled, and the unlabelled image is filed as \"the current map.\" The NFHL page already says the layer holds current effective data, and the MSC FAQ already says preliminary maps are not final regulatory products.",
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
          "[FEMA flood maps: how to find a FIRM](/guides/fema-flood-maps) — the official community map.",
          "[What is a FIRMette and how to print one](/guides/firmette) — a printable piece of an effective map only.",
          "[What is a base flood elevation (BFE)](/guides/base-flood-elevation) — the number on the map is an elevation, not a finished floor.",
        ],
      },
      {
        type: "paragraph",
        text: "PlantGeo publishes no forecasts. The Aevani catalogue, if it is mentioned at all, is a preview and not for sale yet. No referral share is quoted. The [site-types index](/site-types) is the land-kind companion.",
      },
      {
        type: "paragraph",
        text: "The useful next step is to say whether the layer was effective or preliminary, and the date consulted, or to read [FEMA flood maps](/guides/fema-flood-maps). If something on this page is wrong, [write to us](/about).",
      },
      {
        type: "paragraph",
        text: "Author: Escherbridge. Last reviewed: 2026-11-10, America/Denver.",
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
    metaDescription: "Ready.gov defines wildfires as unplanned fires that can reach communities, and advises a fire-resistant zone of at least 30 feet. That is not a Firewise width.",
    eyebrow: "Ready.gov wildfires",
    slug: "ready-wildfires",
    crumb: "Ready.gov wildfire preparedness",
    publishedAt: "2026-11-10",
    lastReviewed: "2026-11-10",
    blocks: [
      {
        type: "paragraph",
        text: "Ready.gov defines wildfires as unplanned fires that burn out of control in natural areas such as forests, grasslands, or prairies, and that \"spread quickly and can devastate wildlife, natural areas and communities.\" Its household guidance includes a fire-resistant zone \"for at least 30 feet from your home.\" That is Ready.gov's figure, not a substitute for the Firewise and IBHS zone widths already briefed on the [WUI parcel](/site-types/wui-parcel) page. This is a household preparedness note, not a second WUI-growth essay, and not a PlantGeo modeling claim.",
      },
      {
        type: "heading",
        text: "Hero figures",
      },
      {
        type: "list",
        items: [
          "**Ready.gov, \"Wildfires.\"** \"Wildfires are unplanned fires that burn out of control in natural areas like forests, grasslands or prairies. These fires spread quickly and can devastate wildlife, natural areas and communities.\" Prepare: several ways to receive alerts, including the FEMA app for National Weather Service alerts \"for up to five locations nationwide,\" community alerts, the Emergency Alert System, and Wireless Emergency Alerts, \"which requires no-sign up.\" \"Pay attention to air quality alerts.\" A household evacuation plan; documents kept current and copied.",
          "**The same page, \"Strengthen your Home.\"** \"Use fire-resistant materials to build, renovate or make repairs. Find an outdoor water source with a hose that can reach any area of your property. Create a fire-resistant zone that is free of leaves, debris or flammable materials for at least 30 feet from your home.\" It also advises designating a room that can be closed off from outside air, closing doors and windows, and using a portable air cleaner \"to keep indoor pollution levels low when smoky conditions exist.\"",
          "**The opened page did not, in the extracted body, use the word \"defensible.\"** That word is not imported here. Firewise Immediate Zone and other NFPA widths are not imported into this page. They belong on the [WUI parcel](/site-types/wui-parcel) sibling, and they are kept distinct from Ready.gov's \"at least 30 feet.\"",
        ],
      },
      {
        type: "heading",
        text: "What Ready.gov says to do, and what this page does not mix",
      },
      {
        type: "paragraph",
        text: "Alerts, including five NWS locations in the FEMA app, and Wireless Emergency Alerts with no sign-up. An evacuation plan. Documents. Fire-resistant materials; a hose that can reach any area of the property; a fire-resistant zone free of leaves, debris, or flammable materials for at least 30 feet from the home; a closable room and an air cleaner in smoke.",
      },
      {
        type: "paragraph",
        text: "[Flooding doesn't follow lines on a map](/guides/ready-floods) is the flood companion in the same Ready.gov household series. This page does not invent survival percentages, parcel-level wildfire probability, or an embers lecture. The opened page's \"ember\" hit was in a generic supplies paragraph, not a home-ignition-zone discussion, and is not stretched here.",
      },
      {
        type: "heading",
        text: "An illustrative scenario",
      },
      {
        type: "paragraph",
        text: "This is a worked example, not a customer and not a forecast. A household note says \"30 feet of defensible space, per Ready.gov and Firewise,\" as if those were one number. Ready.gov's page already states \"at least 30 feet.\" The Firewise and IBHS widths live on a different official set of pages, and they are not merged here.",
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
          "[WUI parcels](/site-types/wui-parcel) — the Firewise and IBHS widths, kept separate from Ready.gov's 30 feet.",
          "[Flooding doesn't follow lines on a map](/guides/ready-floods) — the Ready.gov flood companion.",
        ],
      },
      {
        type: "paragraph",
        text: "PlantGeo publishes no forecasts. The Aevani catalogue, if it is mentioned at all, is a preview and not for sale yet. No referral share is quoted. The [site-types index](/site-types) is the land-kind companion.",
      },
      {
        type: "paragraph",
        text: "The useful next step is to read Ready.gov's wildfire page, or the [WUI parcel](/site-types/wui-parcel) sibling for Firewise and IBHS widths. If something on this page is wrong, [write to us](/about).",
      },
      {
        type: "paragraph",
        text: "Author: Escherbridge. Last reviewed: 2026-11-10, America/Denver.",
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
    metaDescription: "USGS moved from hand-scribed historical topos to US Topo maps in 2009. This guide keeps those products distinct. A topo is still not a site plan.",
    eyebrow: "US Topo and historical maps",
    slug: "us-topo",
    crumb: "US Topo and historical topographic maps",
    publishedAt: "2026-11-10",
    lastReviewed: "2026-11-10",
    blocks: [
      {
        type: "paragraph",
        text: "Say which topo you used: a current US Topo, an OnDemand custom sheet, or a historical quadrangle from 1884–2006. They are not interchangeable without saying so, and none of them is a site plan. USGS has been the primary civilian mapping agency of the United States since 1879. In 2009 it transitioned from hand-scribed historical topographic maps to US Topos, which are computer-generated on a regular schedule from national databases. This is a field note about official topo products, not a claim that PlantGeo is a mapping agency.",
      },
      {
        type: "paragraph",
        text: "A narrower \"US Topo maps\" URL returned HTTP 404 on 2026-11-10. The quotations below are from the opened USGS \"Topographic Maps\" program page.",
      },
      {
        type: "heading",
        text: "Hero figures",
      },
      {
        type: "list",
        items: [
          "**USGS, \"Topographic Maps.\"** \"The U.S. Geological Survey (USGS) has been the primary civilian mapping agency of the United States since 1879. Some of the most well-known USGS maps are the 1:24,000-scale topographic maps, also called 7.5-minute quadrangles. In 2009, the USGS transitioned from our hand scribed historical topographic maps to US Topos, which are computer-generated on a regular schedule using national databases…\" \"Built on the success of over 140 years of USGS topographic mapping, the US Topo series delivers consistent map coverage of the American landscape using National Map data.\" US Topo maps are \"Available for free and immediate download.\" \"The Historical Topographic Map Collection contains USGS topographic maps published 1884 to 2006.\" The same page also names topoBuilder for \"custom topographic maps—OnDemand Topos.\"",
          "**Already opened in batch 1:** \"The National Map is a suite of products, services, and applications…\" and \"serves as the data source for cartographic products, such as US Topo and OnDemand Topo maps, and provides access to digital historical topographic maps.\"",
        ],
      },
      {
        type: "heading",
        text: "The official sequence",
      },
      {
        type: "paragraph",
        text: "Civilian mapping agency since 1879; well-known 1:24,000 / 7.5-minute quadrangles; 2009 transition from hand-scribed sheets to computer-generated US Topos on a regular schedule, from National Map data; historical collection 1884–2006; free download of US Topo; topoBuilder for OnDemand Topos. This page does not invent contour intervals, update cycles, or a completeness claim the opened page does not make. A US Topo or a historical quad is not a property survey or a grading plan.",
      },
      {
        type: "paragraph",
        text: "A site reading can name the product, the date, and the scale. The Ada County sample, if it is mentioned at all, is a sample and not a client. [The National Map: a base is not a site plan](/guides/national-map) is the parent companion. [USGS 3D Elevation Program](/guides/usgs-3dep) is the elevation sibling.",
      },
      {
        type: "heading",
        text: "An illustrative scenario",
      },
      {
        type: "paragraph",
        text: "This is a worked example, not a customer and not a survey. A base map is labelled \"the USGS topo\" after a scan of a 1950s sheet is mixed with a current US Topo, with no product name or date. The program page already keeps those products distinct.",
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
          "[The National Map: a base is not a site plan](/guides/national-map) — the official base, not a finished plan.",
          "[USGS 3D Elevation Program](/guides/usgs-3dep) — elevation you can name as a source.",
        ],
      },
      {
        type: "paragraph",
        text: "PlantGeo publishes no forecasts. The Aevani catalogue, if it is mentioned at all, is a preview and not for sale yet. No referral share is quoted. The [site-types index](/site-types) is the land-kind companion. [Aevani's growing guides](https://aevani.com/guides) are the catalogue-side sibling when the question turns from the parcel to a growing system; the catalogue itself is still not for sale.",
      },
      {
        type: "paragraph",
        text: "The useful next step is to name the product, the date, and the scale, or to read [The National Map](/guides/national-map). If something on this page is wrong, [write to us](/about).",
      },
      {
        type: "paragraph",
        text: "Author: Escherbridge. Last reviewed: 2026-11-10, America/Denver.",
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
