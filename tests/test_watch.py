"""The watch on the selected aircraft and the favorites, wherever they are."""

import time

import pygame
import requests

from flighttrack import settings as settings_module
from flighttrack.app import App
from flighttrack.feeds.base import Aircraft
from flighttrack.feeds.watch import WATCH, Watch
from flighttrack.state import Store


class Answer:
    def __init__(self, status, body):
        self.status_code, self._body = status, body

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))


class Session:
    """Answers every hex request with those of the given records it asks about."""

    def __init__(self, records, fail_first=False):
        self.records, self.fail_first, self.urls, self.headers = records, fail_first, [], {}

    def get(self, url, timeout=None):
        self.urls.append(url)
        if self.fail_first and len(self.urls) == 1:
            return Answer(503, {})
        wanted = url.rsplit("/", 1)[1].split(",")
        return Answer(200, {"ac": [r for r in self.records if r["hex"] in wanted]})


def record(hex_code, lat=37.0, lon=-122.6, **fields):
    return {"hex": hex_code, "lat": lat, "lon": lon, "alt_baro": 3000, "seen_pos": 1.0, **fields}


def test_the_selected_aircraft_and_the_favorites_are_asked_about_in_one_request():
    store = Store()
    resolved = []

    def resolve(entry):
        resolved.append(entry)
        return {"N456QX": "abc213"}.get(entry)

    session = Session([record("abc123"), record("abc213"), record("4ca1d3")])
    watch = Watch(store, session, resolve=resolve, pause=lambda s: None)
    assert watch.fetch(0, 0, 0) == [] and session.urls == [], "nothing to ask: no request"

    assert watch.set("abc123", ["N456QX", "4CA1D3", "DAL1234"])
    assert not watch.set("abc123", ["N456QX", "4CA1D3", "DAL1234"]), "no change"
    found = watch.fetch(0, 0, 0)
    assert {a.hex for a in found} == {"abc123", "abc213", "4ca1d3"}
    assert len(session.urls) == 1 and session.urls[0].startswith("https://api.adsb.lol/")
    assert session.urls[0].endswith("abc123,abc213,4ca1d3"), "the selected one first"
    assert resolved == ["N456QX", "DAL1234"], "a hex is its own; the rest looked up once"
    watch.fetch(0, 0, 0)
    assert resolved == ["N456QX", "DAL1234"], "remembered"

    # A favorite callsign is watched once it has been seen flying.
    store.set_aircraft(
        "area",
        [Aircraft("a00001", 35, -80, time.monotonic(), callsign="DAL1234")],
        time.monotonic(),
    )
    assert "a00001" in watch.hexes()


def test_adsb_fi_answers_when_adsb_lol_does_not():
    session = Session([record("abc123")], fail_first=True)
    watch = Watch(Store(), session, resolve=lambda e: None, pause=lambda s: None)
    watch.set("abc123", [])
    assert [a.hex for a in watch.fetch(0, 0, 0)] == ["abc123"]
    assert "adsb.fi" in session.urls[1] and watch.attribution == "adsb.fi"


def test_the_watch_does_not_make_the_areas_data_look_fresh():
    store = Store()
    store.set_aircraft("public", [], 100.0)
    store.set_error("public", "down", 200.0)
    store.set_aircraft(WATCH, [], 300.0)
    feed = store.feed()
    assert feed.updated_at == 100.0 and feed.all_failing


def test_a_selected_aircraft_stays_up_to_date_when_the_map_is_dragged_away(tmp_path):
    settings_module.save(
        settings_module.Settings(home_lat=37.0, home_lon=-122.6), tmp_path / "settings.json"
    )
    app = App(fullscreen=False, data_dir=tmp_path, replay=False)
    try:
        now = time.monotonic()
        plane = Aircraft("abc123", 37.05, -122.55, now, callsign="TST1", altitude_ft=3000)
        app.store.set_aircraft("public", [plane], now)
        app._select("abc123")
        app.draw(now)
        assert app.watch is not None
        session = Session([record("abc123", lat=37.06, lon=-122.54)])
        app.watch._session = session

        # Dragged far away: the area poller no longer sees it, the watch still does.
        app.view_center = (40.0, -100.0)
        app.store.set_aircraft("public", [], time.monotonic())
        watch_poller = next(p for p in app.pollers if p.name == "feed-watch")
        watch_poller.poll_once()
        app.draw(time.monotonic())
        assert app.selected_hex == "abc123" and "abc123" not in app._lost, "still current"
        assert session.urls and session.urls[0].endswith("abc123")
    finally:
        pygame.quit()
