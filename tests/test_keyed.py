"""The services that need a key: OpenSky, FlightAware AeroAPI and ADS-B Exchange. Fakes only."""

import time

import pytest
import requests

from flighttrack import airports, keyed, net


class Answer:
    def __init__(self, status, body=None):
        self.status_code, self._body = status, body

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            error = requests.HTTPError(str(self.status_code))
            error.response = self
            raise error


class Session:
    def __init__(self, answers):
        self.answers, self.calls, self.headers = list(answers), [], {}

    def _next(self, method, url, kwargs):
        self.calls.append((method, url, kwargs))
        return self.answers.pop(0)

    def get(self, url, **kwargs):
        return self._next("GET", url, kwargs)

    def post(self, url, **kwargs):
        return self._next("POST", url, kwargs)


WALL, MONO = 1_800_000_000.0, 5_000.0


def test_opensky_signs_in_once_and_again_when_its_token_runs_out():
    token = Answer(200, {"access_token": "t1", "expires_in": 1800})
    path = [
        [WALL - 600, 37.0, -122.6, None, 90.0, True],
        [WALL - 300, 37.1, -122.5, 1000.0, 90.0, False],
    ]
    session = Session([token, Answer(200, {"path": path})])
    sky = keyed.OpenSky("me", "secret", session, wall=lambda: WALL, clock=lambda: MONO)
    track = sky.track("ABC213")
    assert track == [(37.0, -122.6, None, MONO - 600), (37.1, -122.5, 3281, MONO - 300)], (
        "metres to feet"
    )
    method, url, kwargs = session.calls[0]
    assert method == "POST" and kwargs["data"]["grant_type"] == "client_credentials"
    assert session.calls[1][2]["headers"]["Authorization"] == "Bearer t1"
    assert session.calls[1][2]["params"] == {"icao24": "abc213", "time": 0}

    # The token has run out: one new one, and the request again.
    session.answers = [Answer(401), Answer(200, {"access_token": "t2"}), Answer(200, {"path": []})]
    assert sky.track("abc213") == []
    assert session.calls[-1][2]["headers"]["Authorization"] == "Bearer t2"
    assert sky.usage.used == 2 and sky.usage.lines()[0][1], "two requests, the last good"


def test_opensky_tells_where_an_aircraft_last_landed():
    kpao = airports.find("KPAO")
    flights = [
        {"icao24": "abc213", "lastSeen": int(WALL) - 90000, "estArrivalAirport": "KSJC"},
        {
            "icao24": "abc213",
            "lastSeen": int(WALL) - 7200,
            "estArrivalAirport": "KPAO",
            "callsign": "N456QX   ",
        },
    ]
    session = Session(
        [Answer(200, {"access_token": "t", "expires_in": 1800}), Answer(200, flights)]
    )
    sky = keyed.OpenSky("me", "secret", session, wall=lambda: WALL, clock=lambda: MONO)
    landed = sky.last_landing("abc213")
    assert (landed.lat, landed.lon) == (kpao.lat, kpao.lon) and landed.on_ground
    assert landed.callsign == "N456QX" and MONO - landed.seen_at == pytest.approx(7200)
    params = session.calls[1][2]["params"]
    assert params["end"] - params["begin"] == 2 * 86400, "OpenSky allows two days at most"
    session.answers = [Answer(404)]
    assert sky.last_landing("abc213") is None


def test_aeroapi_gives_the_flight_under_way_within_its_daily_budget():
    flights = {
        "flights": [
            {
                "actual_off": None,
                "origin": {"code_iata": "CLT"},
                "destination": {"code_iata": "JFK"},
            },
            {
                "actual_off": "2026-10-15T15:00:00Z",
                "actual_on": None,
                "operator": "DAL",
                "status": "En Route",
                "origin": {"code_iata": "ATL", "city": "Atlanta"},
                "destination": {"code_iata": "BOS", "city": "Boston"},
            },
        ]
    }
    session = Session([Answer(200, flights)])
    aero = keyed.AeroApi("key-123", session)
    found = aero.fetch("DAL1234")
    assert (found.operator, found.origin, found.destination, found.status) == (
        "DAL",
        ("ATL", "Atlanta"),
        ("BOS", "Boston"),
        "En Route",
    )
    assert session.calls[0][2]["headers"] == {"x-apikey": "key-123"}
    assert session.calls[0][1].endswith("/flights/DAL1234")
    aero.usage.used = aero.usage.daily_cap
    with pytest.raises(keyed.OverBudget):
        aero.fetch("DAL1234")
    assert len(session.calls) == 1, "over the budget: not asked"


def test_aeroapi_gives_a_track_that_starts_where_it_took_off():
    flights = {
        "flights": [
            {
                "fa_flight_id": "N456QX-1792000000-adhoc-0",
                "actual_off": "2027-01-15T07:30:00Z",
                "origin": {"code_icao": "KPAO", "code_iata": "PAO"},
            }
        ]
    }
    off = 1_800_000_000.0 - 1800  # 2027-01-15T07:30:00Z
    positions = {
        "positions": [
            {
                "latitude": 37.5,
                "longitude": -122.1,
                "altitude": 25,
                "timestamp": "2027-01-15T07:50:00Z",
            },
            {
                "latitude": 37.48,
                "longitude": -122.12,
                "altitude": 8,
                "timestamp": "2027-01-15T07:35:00Z",
            },
            {
                "latitude": "bad",
                "longitude": -122.1,
                "altitude": 8,
                "timestamp": "2027-01-15T07:36:00Z",
            },
        ]
    }
    session = Session([Answer(200, flights), Answer(200, positions)])
    aero = keyed.AeroApi("key-123", session, wall=lambda: WALL, clock=lambda: MONO)
    track = aero.track("n456qx")
    kpao = keyed.airports.find("KPAO")
    assert track[0] == (kpao.lat, kpao.lon, None, MONO - (WALL - off)), "on the ground at KPAO"
    assert [p[2] for p in track] == [None, 800, 2500], "hundreds of feet, oldest first"
    assert session.calls[1][1].endswith("/flights/N456QX-1792000000-adhoc-0/track")
    assert aero.usage.used == 2, "two requests, both counted"


def test_aeroapi_answers_the_pane_from_a_worker_and_keeps_the_answer():
    session = Session([Answer(404)])
    aero = keyed.AeroApi("key-123", session)
    assert aero.flight("N456QX") == keyed.LOADING
    deadline = time.monotonic() + 2
    while aero.flight("N456QX") == keyed.LOADING and time.monotonic() < deadline:
        time.sleep(0.01)
    assert aero.flight("N456QX") is None and len(session.calls) == 1, "kept, not asked again"


def test_a_refused_key_says_so_in_plain_words():
    session = Session([Answer(401)])
    aero = keyed.AeroApi("bad", session)
    with pytest.raises(requests.HTTPError):
        aero.fetch("DAL1")
    line, good, bad = aero.usage.lines()[0]
    assert "The key was refused." in line and bad and not good


def test_adsbx_sends_its_key_and_keeps_to_its_allowance():
    session = Session([Answer(200, {"ac": [{"hex": "abc123", "lat": 37.0, "lon": -122.6}]})])
    feed = keyed.AdsbxFeed("rapid-key", session)
    found = feed.fetch(37.0, -122.6, 400)
    assert [a.hex for a in found] == ["abc123"]
    method, url, kwargs = session.calls[0]
    assert url.endswith("/v2/lat/37.0000/lon/-122.6000/dist/250/"), "at most 250 NM"
    assert kwargs["headers"]["x-rapidapi-key"] == "rapid-key"
    assert kwargs["headers"]["x-rapidapi-host"] == keyed.ADSBX_HOST
    feed.usage.used = keyed.ADSBX_DAILY
    with pytest.raises(keyed.OverBudget):
        feed.fetch(37.0, -122.6, 40)


def test_every_service_has_a_running_count():
    net.LIMITER.count("https://opendata.adsb.fi/api/v3/lat/1/lon/2/dist/3")
    net.LIMITER.count("https://api.adsbdb.com/v0/aircraft/x")
    lines = keyed.usage_lines()
    assert len(lines) == len(keyed.PROVIDERS)
    fi = next(line for line in lines if line.startswith("adsb.fi:"))
    assert "today" in fi and "a request a second" in fi


def test_the_app_uses_the_keys_it_is_given(tmp_path):
    import pygame

    from flighttrack import settings as settings_module
    from flighttrack.app import App
    from flighttrack.feeds.base import Aircraft

    settings_module.save(
        settings_module.Settings(home_lat=37.0, home_lon=-122.6), tmp_path / "settings.json"
    )
    app = App(fullscreen=False, data_dir=tmp_path, replay=False, keyed_services=True)
    try:
        assert app.opensky() is None and app.aeroapi() is None and app.adsbx is None
        assert app.service_lines("FlightAware AeroAPI") == [], "nothing until a key is set"

        # ADS-B Exchange: its key makes the feeds be built again, with it as the last resort.
        app._reconfigure.clear()
        app.set_key("adsbx_key", "rapid-key-0001")
        assert app._reconfigure.is_set()
        app.configure_feeds()
        assert app.adsbx is not None and app.attribution_source._feeds[-1] is app.adsbx

        # FlightAware: the pane shows the flight it is on.
        app.set_key("flightaware_key", "fa-key-0001")
        aero = app.aeroapi()
        aero._known["DAL1"] = (
            time.monotonic() + 600,
            keyed.Flight("Delta", ("ATL", "Atlanta"), ("BOS", "Boston"), "En Route"),
        )
        app.enrich = type("Nothing", (), {
            "route": lambda self, c: None, "airframe": lambda self, h: None,
            "photos": lambda self, *a: None,
        })()  # fmt: skip
        details = app._details(Aircraft("abc123", 37.0, -122.6, time.monotonic(), callsign="DAL1"))
        assert details.origin == ("ATL", "Atlanta") and details.operator == "Delta"

        # OpenSky: a track when adsb.lol has none.
        app.set_key("opensky_client_id", "me-0000")
        app.set_key("opensky_client_secret", "secret-0000")
        sky = app.opensky()
        now = time.monotonic()
        sky.track = lambda hex_code: [
            (37.0, -122.6, None, now - 600),
            (37.1, -122.6, 900, now - 300),
        ]
        app.find_trace = lambda hex_code: []
        plane = Aircraft("abc123", 37.2, -122.6, now)
        app.store.set_aircraft("public", [plane], now)
        app._select("abc123")
        app._ask_trace(now)
        deadline = time.monotonic() + 2
        while len(app.store.track("abc123")) < 3 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert len(app.store.track("abc123")) == 3
    finally:
        pygame.quit()


def test_opensky_saying_the_client_is_wrong_is_put_plainly_and_the_id_is_shown_whole():
    from flighttrack import secrets_store

    refusal = Answer(401, {"error": "invalid_client", "error_description": "Invalid client"})
    sky = keyed.OpenSky("meone-api-client", "secret", Session([refusal]))
    with pytest.raises(requests.HTTPError):
        sky.track("abc123")
    line = sky.usage.lines()[0][0]
    assert "did not accept the client ID and secret" in line
    assert secrets_store.shown("opensky_client_id", "someone-api-client") == "someone-api-client"
    assert secrets_store.shown("opensky_client_secret", "abcdefghij1234").endswith("1234")
    assert "abcdefghij" not in secrets_store.shown("opensky_client_secret", "abcdefghij1234")


def test_the_counts_on_the_feeds_tab_move_as_requests_are_made(tmp_path):
    import pygame

    from flighttrack.app import App

    app = App(fullscreen=False, data_dir=tmp_path, replay=True)
    try:
        app.open_settings()
        app.page.show_tab("Feeds")

        def adsb_fi_line():
            return next(i.label for i in app.page.items() if i.label.startswith("adsb.fi:"))

        before = adsb_fi_line()
        net.LIMITER.count("https://opendata.adsb.fi/api/v3/lat/1/lon/2/dist/3")
        after = adsb_fi_line()
        assert before != after, "the page is rebuilt every frame: the count is live"
        page = app.portal_state()
        assert any(line.startswith("adsb.fi:") for line in page.usage)
    finally:
        pygame.quit()
