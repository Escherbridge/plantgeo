"""Scientific checks for meteorological component and mean semantics."""

import pytest

from agri_data_service.warehouse.weather_forecast.wind import WindVector, mean_wind, meteorological_vector


@pytest.mark.parametrize(
    ("direction", "u", "v"),
    [(0, 0, -10), (90, -10, 0), (180, 0, 10), (270, 10, 0), (360, 0, -10)],
)
def test_meteorological_from_direction_cardinals(direction: float, u: float, v: float) -> None:
    vector = meteorological_vector(10, direction)
    assert vector.u == pytest.approx(u, abs=1e-10)
    assert vector.v == pytest.approx(v, abs=1e-10)
    assert vector.speed == pytest.approx(10)
    assert vector.direction == pytest.approx(direction % 360)


def test_wraparound_mean_points_north_not_south() -> None:
    average = mean_wind([meteorological_vector(10, 350), meteorological_vector(10, 10)])
    assert average.direction == pytest.approx(0, abs=1e-10)
    assert average.speed == pytest.approx(9.84807753)


def test_opposed_vectors_cancel_without_inventing_a_direction() -> None:
    average = mean_wind([meteorological_vector(10, 90), meteorological_vector(10, 270)])
    assert average.speed == pytest.approx(0, abs=1e-10)
    assert average.direction is None


def test_component_mean_accounts_for_speed_not_only_angle() -> None:
    average = mean_wind([meteorological_vector(12, 0), meteorological_vector(4, 180)])
    assert average.speed == pytest.approx(4)
    assert average.direction == pytest.approx(0, abs=1e-10)


def test_zero_wind_is_available_without_direction_and_empty_mean_refuses() -> None:
    assert meteorological_vector(0, None) == WindVector(0, 0)
    with pytest.raises(ValueError, match="empty"):
        mean_wind([])
    with pytest.raises(ValueError, match="requires direction"):
        meteorological_vector(1, None)


@pytest.mark.parametrize("speed", [-1, True, float("nan"), float("inf")])
def test_invalid_speed_refused(speed: float) -> None:
    with pytest.raises(ValueError, match=r"wind speed cannot be negative|wind components require finite"):
        meteorological_vector(speed, 0)


@pytest.mark.parametrize("direction", [-1, 361, True, float("nan")])
def test_invalid_direction_refused(direction: float) -> None:
    with pytest.raises(ValueError, match=r"meteorological direction must be in|wind components require finite"):
        meteorological_vector(1, direction)
