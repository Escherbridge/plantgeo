"""Resolution fixture: a strategy module that exists but whose own import fails (a code fault, exit 70)."""

from __future__ import annotations

import agri_data_service.no_such_dependency  # type: ignore[import-not-found]  # noqa: F401 - the fault under test
