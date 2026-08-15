"""Agri Data Service Warehouse Layer (L1).

Database engine factories, sessions, SQL object loading, and ORM mappings.
May import L0 foundation; may NOT import method, pipeline, planes, or interface.

This facade has no consumers today (nothing imports `agri_data_service.warehouse`
directly; every caller reaches into `db.engine`/`db.base`/`db.sql_objects`/
`db.sql_queries` itself) -- it exists so the layer package importlib-imports
cleanly for `tests/test_layer_import_contract.py`. Re-export only symbols that
actually exist in those modules; do not invent names here.
"""

from agri_data_service.db.base import Base
from agri_data_service.db.engine import async_session, combined_local_engine
from agri_data_service.db.sql_objects import load_object_sql
from agri_data_service.db.sql_queries import load_query_sql

__all__ = [
    "Base",
    "async_session",
    "combined_local_engine",
    "load_object_sql",
    "load_query_sql",
]
