"""HTTP serving surface (L4): the `/api/v1/parquet` plane over the day-partitioned Parquet warehouse.

See `AGENTS.md` in this directory for the wire freeze it implements and the memory guard it carries.
"""

from agri_data_service.interface.http.botanical_species_information import botanical_species_information_bp
from agri_data_service.interface.http.parquet_routes import parquet_bp

__all__ = ["botanical_species_information_bp", "parquet_bp"]
