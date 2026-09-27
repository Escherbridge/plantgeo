"""Run one soil-properties operator verb."""

import asyncio

from agri_data_service.pipeline.direct.soil_properties.forward import main

if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
