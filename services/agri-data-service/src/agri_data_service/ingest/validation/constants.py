"""The stream catalog's vocabulary: the kind and storage location a `StreamDefinition` can declare."""

from __future__ import annotations

from typing import Literal

StreamKind = Literal["time_series", "snapshot", "reference"]
# `historical_table` was a third member until 2026-09-04. It named a store no `StreamDefinition` had
# declared since 2026-08-15 and that no statement reads any more; keeping it would leave the type
# advertising a store nothing can produce. See models.py above `DEFAULT_STREAM_DEFINITIONS`.
StreamStore = Literal["features", "drought_areas"]
