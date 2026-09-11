"""Governed completed-cohort MTBS publication dates; see warehouse/AGENTS.md."""

from collections.abc import Mapping
from datetime import date
from types import MappingProxyType

MTBS_ANNUAL_RELEASE_DATES: Mapping[int, date] = MappingProxyType(
    {
        2018: date(2020, 11, 24),
        2019: date(2021, 9, 27),
        2020: date(2022, 4, 28),
        2021: date(2023, 8, 9),
        2022: date(2024, 8, 22),
    }
)
