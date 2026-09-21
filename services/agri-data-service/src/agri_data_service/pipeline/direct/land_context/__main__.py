"""Bounded land-context snapshot command."""

import asyncio

from agri_data_service.pipeline.direct.land_context.forward import main

if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
