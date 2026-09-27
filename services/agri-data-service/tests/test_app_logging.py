"""`app.py::create_app` now configures Wave O's shared logging chain instead of its own copy.

See `foundation/observability/AGENTS.md` "Logging contract" -- COV2-07 is exactly this: the web
process is redacted from GL-1, not left on its own inline `structlog.configure(...)` block.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from agri_data_service import app as app_module
from agri_data_service.config import settings

if TYPE_CHECKING:
    import pytest


def test_web_process_lines_are_redacted_json(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # A developer .env with SANIC_DEBUG=true selects the console renderer; pin the production path.
    monkeypatch.setattr(settings, "sanic_debug", False)
    app_module.create_app()  # building the app is what calls configure_logging, the subject here
    capsys.readouterr()  # drop app-construction output; only the canary line is under test
    monkeypatch.setenv("SOME_SERVICE_API_KEY", "supersecretvalue123")
    app_module.logger.info("web_process_canary_event", token="supersecretvalue123")
    captured = capsys.readouterr()
    lines = [json.loads(line) for line in captured.out.splitlines() if line.strip()]
    matching = [line for line in lines if line.get("event") == "web_process_canary_event"]
    assert matching, "expected the canary event to render as one JSON line on stdout"
    payload = matching[0]
    assert payload["level"] == "info"
    assert {"event", "level", "timestamp", "service"} <= payload.keys()
    assert "supersecretvalue123" not in json.dumps(payload)
