"""Shared test fixtures."""

import pytest

from agri_data_service.app import AgriApp, create_app


@pytest.fixture
def app() -> AgriApp:
    """Create a test Sanic application."""
    return create_app()
