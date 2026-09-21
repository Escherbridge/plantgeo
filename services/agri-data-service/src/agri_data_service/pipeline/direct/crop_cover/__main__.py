"""Run the bounded annual crop-cover operator."""

import asyncio

from agri_data_service.pipeline.direct.crop_cover.forward import main

if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
