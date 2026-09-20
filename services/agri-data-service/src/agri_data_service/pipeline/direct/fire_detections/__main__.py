"""Entry point for `python -m agri_data_service.pipeline.direct.fire_detections`."""

from __future__ import annotations

import asyncio

from .forward import main

if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
