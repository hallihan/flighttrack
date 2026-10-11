import pytest

from flighttrack.feeds.base import Aircraft
from flighttrack.ui import format as fmt

BASE = {"hex": "abc123", "lat": 0.0, "lon": 0.0, "seen_at": 0.0}


def test_range_labels():
    assert [fmt.range_label(r) for r in (1, 2.5, 10)] == ["1 NM", "2.5 NM", "10 NM"]


def test_altitude():
    assert fmt.altitude(Aircraft(**BASE, altitude_ft=12025)) == "12,025 ft"
    assert fmt.altitude(Aircraft(**BASE, on_ground=True, altitude_ft=0)) == "GROUND"
    assert fmt.altitude(Aircraft(**BASE)) == "---"
    assert fmt.altitude_tag(Aircraft(**BASE, altitude_ft=12025)) == "120"
    assert fmt.altitude_tag(Aircraft(**BASE, altitude_ft=800)) == "008"
    assert fmt.altitude_tag(Aircraft(**BASE, on_ground=True, altitude_ft=0)) == "GND"
    assert fmt.altitude_tag(Aircraft(**BASE)) == "---"


@pytest.mark.parametrize(
    ("fpm", "expected"),
    [(None, "---"), (0, "level"), (40, "level"), (-832, "-800 fpm"), (1216, "+1,200 fpm")],
)
def test_vertical_rate(fpm, expected):
    assert fmt.vertical_rate(fpm) == expected


def test_speed_heading_distance_age():
    assert fmt.speed(301.4) == "301 kt" and fmt.speed(None) == "---"
    assert (
        fmt.heading(87.2) == "087°" and fmt.heading(359.7) == "000°" and fmt.heading(None) == "---"
    )
    assert fmt.distance(3.24) == "3.2 NM"
    assert fmt.age(4.4) == "4s" and fmt.age(-1) == "0s" and fmt.age(180) == "3m"
