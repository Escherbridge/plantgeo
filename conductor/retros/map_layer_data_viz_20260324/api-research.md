# API Research — Real Data Source Shapes

## 1. USGS Water Services (Streamflow Gauges)

**Endpoint:** `https://waterservices.usgs.gov/nwis/iv/?format=json&stateCd={state}&parameterCd=00060&siteStatus=active&siteType=ST`
**Auth:** None required
**Format:** JSON (nested `value.timeSeries[]`)

### Response Shape
```typescript
interface USGSResponse {
  value: {
    timeSeries: Array<{
      sourceInfo: {
        siteName: string;                    // "NASELLE RIVER NEAR NASELLE, WA"
        siteCode: Array<{ value: string }>;  // ["12010000"]
        geoLocation: {
          geogLocation: {
            srs: string;        // "EPSG:4326"
            latitude: number;   // 46.3739937
            longitude: number;  // -123.743482
          }
        };
        siteProperty: Array<{ name: string; value: string }>;  // stateCd, countyCd, hucCd
      };
      variable: { variableName: string };  // "Streamflow, ft³/s"
      values: Array<{
        value: Array<{
          value: string;        // "827" (cfs as string)
          dateTime: string;     // ISO 8601
          qualifiers: string[]; // ["P" = provisional]
        }>;
      }>;
    }>;
  };
}
```

### Mapping to Demo Data
```
sourceInfo.siteCode[0].value → siteNo
sourceInfo.siteName → siteName
sourceInfo.geoLocation.geogLocation.latitude → lat
sourceInfo.geoLocation.geogLocation.longitude → lon
values[0].value[0].value → flowCfs (parse as number)
values[0].value[0].dateTime → updatedAt
```

### Live Fetch Strategy
Query by bounding box instead of state code for generalized use:
```
https://waterservices.usgs.gov/nwis/iv/?format=json&bBox={west},{south},{east},{north}&parameterCd=00060&siteStatus=active&siteType=ST
```

---

## 2. US Drought Monitor (National GeoJSON)

**Endpoint:** `https://droughtmonitor.unl.edu/data/json/usdm_current.json`
**Auth:** None required
**Format:** GeoJSON FeatureCollection with MultiPolygon

### Response Shape
```typescript
// Standard GeoJSON FeatureCollection
interface USDMResponse {
  type: "FeatureCollection";
  features: Array<{
    type: "Feature";
    id: number;
    geometry: {
      type: "MultiPolygon";
      coordinates: number[][][][];
    };
    properties: {
      DM: number;       // 0-4 drought severity
      Shape_Leng: number;
      Shape_Area: number;
    };
  }>;
}
```

### Notes
- This is a NATIONAL dataset (~5MB) — covers entire US
- Published every Thursday
- Properties.DM values: 0=D0 Abnormally Dry, 1=D1 Moderate, 2=D2 Severe, 3=D3 Extreme, 4=D4 Exceptional
- Our DroughtLayer already expects this exact shape (FeatureCollection with DM property)
- For demo: use the live endpoint directly (no auth, free, national coverage)

---

## 3. NASA GIBS — Vegetation (NDVI/EVI)

**Endpoint:** WMS (not WMTS — WMTS tile matrix sets are restrictive)
**Auth:** None required
**Format:** Raster PNG tiles via WMS GetMap

### Working URL Pattern
```
https://gibs.earthdata.nasa.gov/wms/epsg3857/best/wms.cgi?
  SERVICE=WMS&VERSION=1.1.1&REQUEST=GetMap
  &LAYERS=MODIS_Terra_L3_NDVI_Monthly
  &FORMAT=image/png&TRANSPARENT=true
  &SRS=EPSG:3857&WIDTH=256&HEIGHT=256
  &BBOX={bbox-epsg-3857}
  &TIME={YYYY-MM-DD}
```

### Available Layers
- `MODIS_Terra_L3_NDVI_Monthly` — NDVI monthly (2000-present, ~2mo delay)
- `MODIS_Terra_L3_EVI_Monthly` — EVI monthly (water stress proxy)
- `VIIRS_SNPP_NDVI_8Day` — Higher-res 8-day NDVI

### Notes
- Global coverage — works for any location, not just WA
- MapLibre substitutes `{bbox-epsg-3857}` automatically for raster sources
- Latest available data: ~2 months behind current date

---

## 4. SoilGrids ISRIC — Soil Properties WMS

**Endpoint:** `https://maps.isric.org/mapserv?map=/map/{property}.map&SERVICE=WMS&VERSION=1.3.0&REQUEST=GetMap`
**Auth:** None required
**Status:** Confirmed 200 OK
**Format:** Raster PNG tiles

### Available Properties (replace {property} in URL)
| Property | Map file | Layer name | Unit |
|----------|----------|------------|------|
| pH | phh2o | phh2o_0-5cm_mean | pH*10 |
| Organic Carbon | soc | soc_0-5cm_mean | dg/kg |
| Clay | clay | clay_0-5cm_mean | g/kg |
| Sand | sand | sand_0-5cm_mean | g/kg |
| Nitrogen | nitrogen | nitrogen_0-5cm_mean | cg/kg |
| Bulk Density | bdod | bdod_0-5cm_mean | cg/cm³ |
| CEC | cec | cec_0-5cm_mean | mmol(c)/kg |

### WMS URL Pattern
```
https://maps.isric.org/mapserv?map=/map/{property}.map
  &SERVICE=WMS&VERSION=1.3.0&REQUEST=GetMap
  &LAYERS={property}_0-5cm_mean
  &CRS=EPSG:3857
  &BBOX={bbox-epsg-3857}
  &WIDTH=256&HEIGHT=256
  &FORMAT=image/png
```

### Notes
- Global coverage — works anywhere
- This is a MUCH better approach than point data for soil visualization
- Can be used as a raster layer in MapLibre (same as NDVI)
- User selects property → URL changes → different soil map renders

---

## 5. NASA FIRMS — Fire Detections

**Endpoint:** `https://firms.modaps.eosdis.nasa.gov/api/area/csv/{MAP_KEY}/VIIRS_SNPP_NRT/{bbox}/{days}`
**Auth:** Requires free MAP_KEY (DEMO_KEY does not work)
**Format:** CSV or JSON

### Workaround — FIRMS WMS (no auth)
```
https://firms.modaps.eosdis.nasa.gov/mapserver/wms/fires/USA_contiguous_and_Hawaii/?
  SERVICE=WMS&VERSION=1.1.1&REQUEST=GetMap
  &LAYERS=fires_viirs_snpp_24
  &FORMAT=image/png&TRANSPARENT=true
  &SRS=EPSG:3857&WIDTH=256&HEIGHT=256
  &BBOX={bbox-epsg-3857}
```

### Notes
- The WMS endpoint is FREE (no key needed) and shows fire detections as raster tiles
- Shows last 24h, 48h, or 7 days of fire detections
- Available layers: `fires_viirs_snpp_24`, `fires_viirs_noaa20_24`, `fires_modis_24`
- Global coverage

---

## Strategy: Generalized Data (Not WA-Specific)

All data sources above are **global or national** — they work for any viewport location:
- USGS: Query by bounding box (`bBox` param)
- USDM: National GeoJSON — just render it, viewport clips naturally
- GIBS: Global WMS tiles
- SoilGrids: Global WMS tiles
- FIRMS: National/global WMS tiles

**For demo mode:** Use WMS raster tile layers for soil/fire/NDVI (no mock data needed — these are live free tiles). Use the live USDM GeoJSON for drought. Only water gauges need mock data (or live USGS API with bbox query).
