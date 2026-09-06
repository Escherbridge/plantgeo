"""Entry point for `python -m agri_data_service.pipeline.direct.evacuation_zones`."""

from __future__ import annotations

import asyncio

from agri_data_service.pipeline.direct.evacuation_zones.forward import main

if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
