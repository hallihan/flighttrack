import pytest

from flighttrack.geo import bearing_deg, destination, distance_nm, offset_nm


def test_one_degree_of_latitude_is_sixty_nautical_miles():
    assert distance_nm(0, 0, 1, 0) == pytest.approx(60.04, abs=0.01)


def test_distance_to_self_is_zero():
    assert distance_nm(43.7, -79.6, 43.7, -79.6) == 0


@pytest.mark.parametrize(
    ("target", "expected"),
    [((1, 0), 0), ((0, 1), 90), ((-1, 0), 180), ((0, -1), 270)],
)
def test_cardinal_bearings(target, expected):
    assert bearing_deg(0, 0, *target) == pytest.approx(expected)


def test_destination_round_trips_with_distance_and_bearing():
    lat, lon = destination(43.6777, -79.6248, 250, 35)
    assert distance_nm(43.6777, -79.6248, lat, lon) == pytest.approx(35, abs=1e-6)
    assert bearing_deg(43.6777, -79.6248, lat, lon) == pytest.approx(250, abs=1e-6)


def test_destination_wraps_across_the_antimeridian():
    _, lon = destination(0, 179.9, 90, 60)
    assert -180 <= lon < -179


def test_offset_is_east_north():
    east, north = offset_nm(50, 10, *destination(50, 10, 90, 5))
    assert east == pytest.approx(5, abs=1e-6)
    assert north == pytest.approx(0, abs=1e-3)

    east, north = offset_nm(50, 10, *destination(50, 10, 180, 5))
    assert east == pytest.approx(0, abs=1e-6)
    assert north == pytest.approx(-5, abs=1e-6)
