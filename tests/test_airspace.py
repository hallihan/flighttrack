import json
import time

import pygame
import pytest
import requests

from flighttrack import airspace as airspace_module
from flighttrack import runways
from flighttrack.airspace import AirspaceStore, cells_around, parse_geojson
from flighttrack.geo import sun
from flighttrack.ui import theme
from flighttrack.ui.overlays import _altitude_label, airspace_painter, runway_painter


def _feature(ident, cls, ring, lower=0, upper=4000, kind="Polygon"):
    coordinates = [ring] if kind == "Polygon" else [[ring]]
    return {
        "type": "Feature",
        "properties": {
            "OBJECTID": ident,
            "NAME": f"TEST CLASS {cls}",
            "CLASS": cls,
            "LOWER_VAL": lower,
            "UPPER_VAL": upper,
        },
        "geometry": {"type": kind, "coordinates": coordinates},
    }


SQUARE = [[10.0, 48.0], [10.2, 48.0], [10.2, 48.2], [10.0, 48.2], [10.0, 48.0]]
PAYLOAD = {
    "type": "FeatureCollection",
    "features": [
        _feature(1, "B", SQUARE, lower=2500, upper=10000),
        _feature(2, "D", SQUARE, kind="MultiPolygon"),
        _feature(3, "E", SQUARE),  # not a class we draw
        {"type": "Feature", "properties": {"CLASS": "C"}, "geometry": None},  # malformed
        _feature(4, "C", SQUARE, lower=-9998, upper=-9998),
    ],
}


def test_parse_geojson_keeps_b_c_d_and_skips_what_it_cannot_use():
    parsed = parse_geojson(PAYLOAD)
    assert [(a.ident, a.cls) for a in parsed] == [(1, "B"), (2, "D"), (4, "C")]
    b = parsed[0]
    assert b.rings[0][0] == (48.0, 10.0), "stored as (lat, lon)"
    assert b.bounds == (48.0, 10.0, 48.2, 10.2)
    assert (b.lower_ft, b.upper_ft) == (2500, 10000)
    assert (parsed[2].lower_ft, parsed[2].upper_ft) == (None, None), "sentinels become None"
    assert parse_geojson({}) == [] and parse_geojson({"features": None}) == []


def test_altitude_labels_read_like_a_chart():
    b, d, c = parse_geojson(PAYLOAD)
    assert _altitude_label(b) == "100/25"
    assert _altitude_label(d) == "40/SFC"
    assert _altitude_label(c) is None


def test_cells_around_a_point():
    assert cells_around(49.0, 11.0, 10) == [(48, 10)]
    assert sorted(cells_around(49.95, 11.9, 20)) == [(48, 10), (48, 12), (50, 10), (50, 12)]
    assert (0, -180) in cells_around(1.0, 179.9, 30), "wraps across the antimeridian"


def _wait(condition, timeout=3.0):
    deadline = time.monotonic() + timeout
    while not condition() and time.monotonic() < deadline:
        time.sleep(0.01)
    return condition()


def test_store_loads_cells_in_the_background_caches_them_and_dedupes(tmp_path, monkeypatch):
    monkeypatch.setattr(airspace_module, "FETCH_SPACING_S", 0.0)
    calls = []

    def fetch(cell):
        calls.append(cell)
        return PAYLOAD

    store = AirspaceStore(tmp_path, fetch=fetch)
    store.start()
    try:
        assert store.around(49.95, 11.9, 20) == (), "nothing yet, but now requested"
        assert _wait(lambda: store.version == 4)
        found = store.around(49.95, 11.9, 20)
        assert sorted(a.ident for a in found) == [1, 2, 4], "same airspace in four cells, once"
        assert sorted(calls) == [(48, 10), (48, 12), (50, 10), (50, 12)]
        store.around(49.95, 11.9, 20)
        assert len(calls) == 4, "loaded cells are not fetched again"
    finally:
        store.stop()

    def offline(cell):
        raise requests.ConnectionError("offline")

    cached = AirspaceStore(tmp_path, fetch=offline)
    cached.start()
    try:
        cached.around(49.0, 11.0, 10)
        assert _wait(lambda: cached.version == 1)
        assert len(cached.around(49.0, 11.0, 10)) == 3, "served from disk"
    finally:
        cached.stop()


def test_store_uses_an_expired_cache_when_the_service_is_down_and_retries_later(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(airspace_module, "FETCH_SPACING_S", 0.0)
    monkeypatch.setattr(airspace_module, "out_of_date", lambda fetched, now: True)
    (tmp_path / "48_10.geojson").write_text(json.dumps(PAYLOAD))
    calls = []

    def failing(cell):
        calls.append(cell)
        raise ValueError("unexpected airspace response: 429")

    store = AirspaceStore(tmp_path, fetch=failing)
    store.start()
    try:
        store.around(49.0, 11.0, 10)
        assert _wait(lambda: store.version == 1)
        assert len(store.around(49.0, 11.0, 10)) == 3, "out of date beats nothing"

        store.around(55.0, 31.0, 10)  # no cache for this cell
        assert _wait(lambda: len(calls) == 2)
        time.sleep(0.05)
        store.around(55.0, 31.0, 10)
        time.sleep(0.05)
        assert len(calls) == 2, "a failed cell is left alone for a while"
    finally:
        store.stop()


def test_runways_near_a_point():
    found = runways.near(51.47, -0.45, 3)
    heathrow = sorted(r.le_ident for r in found if r.airport == "EGLL")
    assert heathrow == ["09L", "09R"]
    assert all(r.width_ft > 0 for r in found)
    assert runways.near(0.0, -30.0, 5) == (), "mid-Atlantic"


def test_overlays_paint_runways_and_airspace_where_they_are():
    pygame.display.init()
    pygame.display.set_mode((800, 480))
    pygame.font.init()
    try:
        fonts = theme.Fonts.load()
        surface = pygame.Surface((592, 400))

        center = (51.4775, -0.4614)
        runway_painter(runways.near(51.47, -0.45, 3), fonts)(surface, center, 13.0)
        drawn = pygame.mask.from_threshold(surface, theme.RUNWAY, (2, 2, 2, 255))
        assert drawn.count() > 200
        pieces = drawn.get_bounding_rects()
        extent = pieces[0].unionall(pieces[1:])
        assert extent.width > 150 > extent.height, "two long east-west runways"

        surface.fill((0, 0, 0))
        airspace_painter(tuple(parse_geojson(PAYLOAD)), fonts, True)(surface, (48.1, 10.1), 10.0)
        assert surface.get_at((296, 200))[:3] == (0, 0, 0), "outlines only: the middle is empty"
        assert pygame.mask.from_threshold(surface, (0, 0, 0), (1, 1, 1, 255)).count() < 592 * 400

        surface.fill((0, 0, 0))
        airspace_painter(tuple(parse_geojson(PAYLOAD)), fonts, True)(surface, (20.0, 60.0), 10.0)
        assert pygame.mask.from_threshold(surface, (0, 0, 0), (1, 1, 1, 255)).count() == 592 * 400
    finally:
        pygame.quit()


def _utc(year, month, day, hour, minute=0):
    import calendar

    return calendar.timegm((year, month, day, hour, minute, 0))


def test_sun_elevation_against_known_values():
    assert sun.sun_elevation_deg(0, 0, _utc(2026, 3, 20, 12, 10)) == pytest.approx(89.5, abs=1)
    assert sun.sun_elevation_deg(51.5, -0.13, _utc(2026, 6, 21, 12)) == pytest.approx(62, abs=0.5)
    assert sun.sun_elevation_deg(51.5, -0.13, _utc(2026, 12, 21, 12)) == pytest.approx(15, abs=0.5)
    assert sun.sun_elevation_deg(-33.87, 151.2, _utc(2026, 6, 21, 2)) == pytest.approx(
        32.7, abs=0.5
    )


def test_daylight_runs_from_night_through_twilight_to_day():
    assert sun.daylight(51.5, -0.13, _utc(2026, 6, 21, 12)) == 1.0
    assert sun.daylight(51.5, -0.13, _utc(2026, 12, 21, 0)) == 0.0
    values = [sun.daylight(0.0, 0.0, _utc(2026, 3, 20, 5, m)) for m in range(30, 60, 5)]
    values += [sun.daylight(0.0, 0.0, _utc(2026, 3, 20, 6, m)) for m in range(0, 40, 5)]
    assert values == sorted(values) and values[0] == 0.0 and values[-1] == 1.0
    assert any(0.0 < v < 1.0 for v in values), "dawn is gradual"
