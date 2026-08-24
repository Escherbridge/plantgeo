from agri_data_service.warehouse.parquet.schema import observed_stream_schema
from agri_data_service.warehouse.parquet import tiers as T
for stream in sorted(T.registered_tier_derivations()) or []:
    pass
names = ["burn-severity","calendar","drought","evacuation-zones","fire-detections","fire-perimeters",
         "sensors","signal","soil-survey","vegetation","water-gauges","watersheds","weather-observations"]
for stream in names:
    sch = observed_stream_schema(stream); strat = T.tier_derivation(stream).strategy
    nulled = [a.column for a in getattr(strat, "aggregations", ()) if a.how == "null"]
    if nulled:
        print(f"{stream}: {sorted(nulled)}")
