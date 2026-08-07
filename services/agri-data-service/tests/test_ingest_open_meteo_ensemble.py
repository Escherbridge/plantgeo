"""Contract tests for the Open-Meteo Ensemble adapter: reviewed hosts, request bounds, and member naming."""

# ruff: noqa: PLR2004

from __future__ import annotations

from datetime import date

import pytest

from agri_data_service.ingest.open_meteo import OPEN_METEO_API_KEY_VARIABLE
from agri_data_service.ingest.open_meteo_ensemble import (
    OPEN_METEO_ENSEMBLE_BASE_URL,
    OPEN_METEO_ENSEMBLE_BOUNDS,
    OPEN_METEO_ENSEMBLE_CUSTOMER_BASE_URL,
    OPEN_METEO_ENSEMBLE_ENDPOINT,
    ensemble_hourly_parameters,
    ensemble_hourly_request,
    ensemble_hourly_url,
    ensemble_member_field_name,
)

START = date(2026, 8, 6)
END = date(2026, 8, 8)
COORDINATES = ((44.0, -116.0), (44.5, -116.5))
VARIABLES = ("precipitation", "temperature_2m")


def test_parameters_carry_the_model_and_the_gmt_time_base() -> None:
    parameters = ensemble_hourly_parameters(COORDINATES, VARIABLES, START, END, "gfs025")

    assert parameters["models"] == "gfs025"
    assert parameters["timezone"] == "GMT"
    assert parameters["cell_selection"] == "nearest"
    assert parameters["hourly"] == "precipitation,temperature_2m"
    assert parameters["latitude"] == "44,44.5"
    assert parameters["longitude"] == "-116,-116.5"


def test_an_unreviewed_model_is_refused_before_a_request_is_built() -> None:
    with pytest.raises(ValueError, match="model must be one of"):
        ensemble_hourly_parameters(COORDINATES, VARIABLES, START, END, "icon_seamless")


def test_unsorted_variables_are_refused_so_one_request_maps_to_one_url() -> None:
    with pytest.raises(ValueError, match="must be sorted"):
        ensemble_hourly_parameters(COORDINATES, ("temperature_2m", "precipitation"), START, END, "gfs025")


def test_an_inverted_window_is_refused() -> None:
    with pytest.raises(ValueError, match="must not precede"):
        ensemble_hourly_parameters(COORDINATES, VARIABLES, END, START, "gfs025")


def test_off_globe_coordinates_are_refused() -> None:
    with pytest.raises(ValueError, match="WGS84"):
        ensemble_hourly_parameters(((91.0, -116.0),), VARIABLES, START, END, "gfs025")


def test_more_than_two_hundred_locations_are_refused() -> None:
    coordinates = tuple((40.0 + index / 100, -116.0) for index in range(201))
    with pytest.raises(ValueError, match="between one and 200"):
        ensemble_hourly_parameters(coordinates, VARIABLES, START, END, "gfs025")


def test_the_persistable_url_never_carries_a_credential(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(OPEN_METEO_API_KEY_VARIABLE, "secret-key")

    url = ensemble_hourly_url(COORDINATES, VARIABLES, START, END, "gfs025")
    request = ensemble_hourly_request(COORDINATES, VARIABLES, START, END, "gfs025")

    assert "secret-key" not in url
    assert url.startswith(OPEN_METEO_ENSEMBLE_CUSTOMER_BASE_URL)
    assert request.base_url == OPEN_METEO_ENSEMBLE_CUSTOMER_BASE_URL
    assert "secret-key" in request.request_url


def test_the_free_host_answers_when_no_credential_is_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(OPEN_METEO_API_KEY_VARIABLE, raising=False)

    request = ensemble_hourly_request(COORDINATES, VARIABLES, START, END, "gfs025")

    assert request.base_url == OPEN_METEO_ENSEMBLE_BASE_URL
    assert request.request_url == ensemble_hourly_url(COORDINATES, VARIABLES, START, END, "gfs025")


def test_recorded_provenance_accepts_only_a_reviewed_host() -> None:
    # One rule for every Open-Meteo product: OpenMeteoEndpoint.require_base_url, not a per-lane copy.
    endpoint = OPEN_METEO_ENSEMBLE_ENDPOINT
    assert endpoint.require_base_url(OPEN_METEO_ENSEMBLE_BASE_URL) == OPEN_METEO_ENSEMBLE_BASE_URL
    assert endpoint.require_base_url(OPEN_METEO_ENSEMBLE_CUSTOMER_BASE_URL) == OPEN_METEO_ENSEMBLE_CUSTOMER_BASE_URL
    with pytest.raises(ValueError, match="not a reviewed endpoint"):
        endpoint.require_base_url("https://ensemble-api.open-meteo.example/v1/ensemble")


def test_member_field_names_use_the_providers_two_digit_numbering() -> None:
    assert ensemble_member_field_name("temperature_2m", 1) == "temperature_2m_member01"
    assert ensemble_member_field_name("temperature_2m", 51) == "temperature_2m_member51"
    with pytest.raises(ValueError, match="member numbers run"):
        ensemble_member_field_name("temperature_2m", 0)


def test_the_response_budget_is_bounded() -> None:
    assert OPEN_METEO_ENSEMBLE_BOUNDS.max_bytes == 64 * 1024 * 1024
    assert OPEN_METEO_ENSEMBLE_BOUNDS.timeout_seconds == 300.0
