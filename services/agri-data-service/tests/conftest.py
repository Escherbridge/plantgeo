"""Shared test fixtures."""

import pytest


@pytest.fixture
def app():
    """Create a test Sanic application."""
    from agri_data_service.app import create_app

    return create_app()
