import json

import pytest

from flighttrack import settings as s


def test_missing_file_gives_defaults(tmp_path):
    loaded = s.load(tmp_path / "nope.json")
    assert loaded == s.Settings()
    assert loaded.home is None and loaded.range_nm == s.DEFAULT_RANGE_NM


def test_round_trip(tmp_path):
    path = tmp_path / "nested" / "settings.json"
    s.save(s.Settings(home_lat=51.47, home_lon=-0.45, range_nm=2.5), path)
    loaded = s.load(path)
    assert loaded.home == (51.47, -0.45) and loaded.range_nm == 2.5
    assert not path.with_suffix(".tmp").exists()


@pytest.mark.parametrize("content", ["{not json", "[1, 2]", '"text"'])
def test_corrupt_file_gives_defaults(tmp_path, content):
    path = tmp_path / "settings.json"
    path.write_text(content)
    assert s.load(path) == s.Settings()


@pytest.mark.parametrize(
    "raw",
    [
        {"range_nm": 7},
        {"range_nm": "far"},
        {"home_lat": 95, "home_lon": 0},
        {"home_lat": 10},
        {"home_lat": "x", "home_lon": "y"},
        {"unknown_key": True},
    ],
)
def test_invalid_values_are_replaced_by_defaults(tmp_path, raw):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(raw))
    assert s.load(path) == s.Settings()


def test_range_steps_through_the_product_ranges_and_stops_at_the_ends():
    assert s.RANGES_NM == (1, 2.5, 5, 10, 25, 30, 40, 50, 100, 250)
    settings = s.Settings(range_nm=1)
    seen = [settings.range_nm]
    for _ in range(len(s.RANGES_NM)):
        settings.step_range(1)
        seen.append(settings.range_nm)
    assert seen == [1, 2.5, 5, 10, 25, 30, 40, 50, 100, 250, 250]

    settings.range_nm = 2.5
    settings.step_range(-1)
    settings.step_range(-1)
    assert settings.range_nm == 1
