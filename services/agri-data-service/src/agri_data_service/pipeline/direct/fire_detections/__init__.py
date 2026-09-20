"""Direct FIRMS publication package with compatibility exports for the historical flat module."""

from __future__ import annotations

from agri_data_service.pipeline.errors import PipelineOperationError

from .adapter import DirectFireDetectionsAdapter
from .forward import WRITER_CONTRACT, _parse_args, main, parser, run_fire_forward
from .models import FireDaySource, FireForwardConfig
from .rows import fire_table_from_features
from .source import fetch_fire_day

# Compatibility name only: the live error type is the reusable shared direct-pipeline error.
DirectFireDetectionsError = PipelineOperationError

__all__ = [
    "WRITER_CONTRACT",
    "DirectFireDetectionsAdapter",
    "DirectFireDetectionsError",
    "FireDaySource",
    "FireForwardConfig",
    "_parse_args",
    "fetch_fire_day",
    "fire_table_from_features",
    "main",
    "parser",
    "run_fire_forward",
]
