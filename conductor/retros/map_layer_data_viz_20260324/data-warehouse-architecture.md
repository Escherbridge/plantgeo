# Regenerative Agriculture Data Service — Architecture Reference

See full research output in agent results. Key decisions:

## Tech Stack
- **API:** FastAPI (Python 3.12) + SQLAlchemy 2.0 + GeoAlchemy2
- **DB:** PostgreSQL 16 + PostGIS 3.4 + pgvector
- **Task Queue:** Celery + Redis
- **RAG:** LangChain + pgvector (no separate vector DB)
- **LLM:** LiteLLM (provider-agnostic: Claude, GPT-4o, Llama)
- **Tiles:** Martin v1.4 for spatial layers
- **Geo Processing:** GDAL / Rasterio / Shapely

## Repo: `agri-data-service`
Separate repo from PlantGeo. Connects via REST API + direct tile consumption.

## Data Sources (17+)
SSURGO, SoilGrids, PRISM, NOAA, NASA POWER, USGS 3DEP/NWIS, CDL, NLCD, USDA PLANTS, GBIF, FAO Ecocrop/GAEZ, OpenFarm, NOAA Atlas 14, EPA Ecoregions, USDA PHZ

## Strategies Modeled (10)
Silvopasture, Agroforestry, Hydroponics, Aquaponics, Cover Cropping, Keyline Design, Biochar, Contour Farming, Managed Grazing, Permaculture Food Forests
