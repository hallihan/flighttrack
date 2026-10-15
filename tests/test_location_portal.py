import json
import time

import pygame
import pytest
import requests

from flighttrack import airports
from flighttrack import settings as settings_module
from flighttrack.app import App
from flighttrack.feeds.base import Aircraft
from flighttrack.location import IP_CACHE_FILE, Location, configured_location, ip_location
from flighttrack.portal import PortalState, _describe, create_app, validate
from flighttrack.settings import Settings

_EGLL = airports.find("EGLL")
EGLL_HOME = Location(_EGLL.lat, _EGLL.lon, "airport", "EGLL")


def test_airport_lookup_by_icao_iata_and_national_code():
    heathrow = airports.find("EGLL")
    assert heathrow.name == "London Heathrow Airport"
    assert (round(heathrow.lat, 1), round(heathrow.lon, 1)) == (51.5, -0.5)
    assert airports.find("lhr") == heathrow
    assert airports.find("  egll ") == heathrow
    assert airports.find("PAO").ident == "KPAO", "US national code without the K prefix"
    assert "EGLL" in heathrow.description and "London" in heathrow.description


@pytest.mark.parametrize("code", ["", "   ", "ZZZZZ", "not an airport"])
def test_unknown_airport_codes(code):
    assert airports.find(code) is None


def test_airport_takes_precedence_over_coordinates():
    both = Settings(home_airport="EGLL", home_lat=10.0, home_lon=20.0)
    assert configured_location(both) == EGLL_HOME
    only_coordinates = Settings(home_lat=10.0, home_lon=20.0)
    assert configured_location(only_coordinates).source == "coordinates"
    assert configured_location(Settings()) is None


def test_unknown_stored_airport_falls_through():
    assert configured_location(Settings(home_airport="ZZZZZ")) is None
    assert configured_location(
        Settings(home_airport="ZZZZZ", home_lat=1.0, home_lon=2.0)
    ).point == (
        1.0,
        2.0,
    )


class FakeSession:
    def __init__(self, payload=None, error=None):
        self.payload, self.error = payload, error

    def get(self, url, headers, timeout):
        if self.error:
            raise self.error
        response = requests.Response()
        response.status_code = 200
        response._content = json.dumps(self.payload).encode()
        return response


def test_ip_location_parses_the_answer_and_caches_it(tmp_path):
    session = FakeSession({"loc": "51.5085,-0.1257", "city": "London"})
    found = ip_location(tmp_path, session)
    assert found == Location(51.5085, -0.1257, "ip", "London (estimated)", "London")
    assert (tmp_path / IP_CACHE_FILE).exists()

    # When the lookup later fails, the cached answer is used.
    offline = FakeSession(error=requests.ConnectionError("offline"))
    assert ip_location(tmp_path, offline) == found


@pytest.mark.parametrize("payload", [{}, {"loc": "nonsense"}, {"loc": "95,10"}, {"loc": "1"}])
def test_ip_location_rejects_bad_answers_and_returns_none_without_a_cache(tmp_path, payload):
    assert ip_location(tmp_path, FakeSession(payload)) is None
    assert not (tmp_path / IP_CACHE_FILE).exists()


def test_form_validation():
    changes, errors = validate({"home_airport": " lhr ", "local_receiver_url": ""})
    assert errors == {}
    assert changes == {
        "home_airport": "EGLL",
        "favorite_airports": ["EGLL"],
        "local_receiver_url": None,
        "favorites": [],
    }

    changes, errors = validate(
        {"home_airport": "", "local_receiver_url": "http://rx.example/a.json"}
    )
    assert errors == {}
    assert changes["home_airport"] is None
    assert changes["local_receiver_url"] == "http://rx.example/a.json"

    _, errors = validate({"home_airport": "ZZZZZ", "local_receiver_url": "ftp://x"})
    assert set(errors) == {"home_airport", "local_receiver_url"}
    _, errors = validate({"home_airport": "", "local_receiver_url": "javascript:alert(1)"})
    assert set(errors) == {"local_receiver_url"}


@pytest.fixture
def portal():
    state = {"settings": Settings(), "applied": [], "flights": [], "resets": 0}

    def get_state():
        return PortalState(state["settings"], None, None)

    def apply_changes(changes):
        state["applied"].append(changes)
        for name, value in changes.items():
            setattr(state["settings"], name, value)

    def reset():
        state["resets"] += 1

    app = create_app(get_state, apply_changes, flights=lambda: state["flights"], reset=reset)
    return app.test_client(), state


def test_the_web_page_has_the_same_tabs_as_the_display_and_carries_its_state(portal):
    from flighttrack.ui.screens import SettingsScreen

    client, _ = portal
    page = client.get("/")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    for name in SettingsScreen.TABS:
        assert f'id="tab-{name.lower()}"' in html and f'id="panel-{name.lower()}"' in html
    for label in ("Screen brightness", "Rain radar", "Favorite airports", "Favorite aircraft"):
        assert label in html
    # What it shows comes with it, so the page is right from the first moment.
    assert '"show_radar": true' in html or '"show_radar":true' in html
    state = client.get("/api/state").get_json()
    assert state["switches"]["show_map"] is True and state["here"]["known"] is False
    assert state["sliders"]["brightness"] == {"value": 1.0, "min": 0.1}
    assert state["centered"]["source"] is None
    # Nothing here can touch the network settings or turn the machine off.
    assert "/api/wifi" not in html and "poweroff" not in html.lower()
    assert "has no password" in html


def _post(client, path, data, **more):
    return client.post(path, json=data, **more)


def test_switches_and_sliders_are_set_by_name_and_nothing_else_can_be(portal):
    client, state = portal
    assert _post(client, "/api/set", {"name": "show_radar", "value": False}).status_code == 200
    assert state["applied"][-1] == {"show_radar": False}
    answer = _post(client, "/api/set", {"name": "brightness", "value": 0.42}).get_json()
    assert (
        state["applied"][-1] == {"brightness": 0.42}
        and answer["sliders"]["brightness"]["value"] == 0.42
    )
    # Out of range is brought into range; the floor is the setting's own.
    _post(client, "/api/set", {"name": "brightness", "value": -3})
    assert state["applied"][-1] == {"brightness": 0.1}
    _post(client, "/api/set", {"name": "ring_opacity", "value": 7})
    assert state["applied"][-1] == {"ring_opacity": 1.0}

    before = len(state["applied"])
    for bad in (
        {"name": "home_airport", "value": "EGLL"},
        {"name": "range_nm", "value": 5},
        {"name": "local_receiver_url", "value": "http://evil.example"},
        {"name": "show_radar", "value": "yes"},
        {"name": "brightness", "value": True},
        {"name": "brightness", "value": "0.5"},
        {"name": "__class__", "value": 1},
        {},
    ):
        assert _post(client, "/api/set", bad).status_code == 400, bad
    assert client.post("/api/set", data="not json", content_type="text/plain").status_code == 400
    assert len(state["applied"]) == before


def test_airports_are_added_made_home_and_removed_as_on_the_display(portal):
    client, state = portal

    answer = _post(client, "/api/airports", {"action": "add", "code": "lax"}).get_json()
    assert (
        state["settings"].favorite_airports == ["KLAX"] and state["settings"].home_airport is None
    )
    assert answer["airports"] == [
        {
            "ident": "KLAX",
            "name": "Los Angeles International Airport",
            "place": answer["airports"][0]["place"],
            "home": False,
            "category": "",
        }
    ]
    _post(client, "/api/airports", {"action": "add", "code": "KLAX"})
    assert state["settings"].favorite_airports == ["KLAX"], "no duplicates"

    # Making one home adds it if need be; home is one of the favorites.
    answer = _post(client, "/api/airports", {"action": "home", "code": "LHR"}).get_json()
    assert state["settings"].home_airport == "EGLL"
    assert state["settings"].favorite_airports == ["KLAX", "EGLL"]
    assert [a["home"] for a in answer["airports"]] == [False, True]

    # Removing home hands it to the favorite added before it; removing the last, to nowhere.
    _post(client, "/api/airports", {"action": "remove", "code": "EGLL"})
    assert (state["settings"].home_airport, state["settings"].favorite_airports) == (
        "KLAX",
        ["KLAX"],
    )
    _post(client, "/api/airports", {"action": "remove", "code": "KLAX"})
    assert (state["settings"].home_airport, state["settings"].favorite_airports) == (None, [])

    # The current location is made home by having no home airport; the list is untouched.
    _post(client, "/api/airports", {"action": "home", "code": "EGLL"})
    _post(client, "/api/airports", {"action": "home", "code": ""})
    assert (state["settings"].home_airport, state["settings"].favorite_airports) == (None, ["EGLL"])

    before = len(state["applied"])
    bad = _post(client, "/api/airports", {"action": "add", "code": "zzzzz"})
    assert bad.status_code == 400 and "No airport has the code ZZZZZ" in bad.get_json()["error"]
    assert _post(client, "/api/airports", {"action": "explode", "code": "EGLL"}).status_code == 400
    assert len(state["applied"]) == before


def test_the_receiver_address_is_checked_before_it_is_kept(portal):
    client, state = portal
    good = "http://rx.example/tar1090/data/aircraft.json"
    assert _post(client, "/api/receiver", {"url": good}).get_json()["receiver"] == good
    assert state["settings"].local_receiver_url == good
    for bad in ("rx.example", "javascript:alert(1)", "ftp://rx.example/x"):
        answer = _post(client, "/api/receiver", {"url": bad})
        assert answer.status_code == 400 and "http://" in answer.get_json()["error"]
    assert state["settings"].local_receiver_url == good
    _post(client, "/api/receiver", {"url": ""})
    assert state["settings"].local_receiver_url is None


def test_what_is_typed_is_never_put_into_the_page_as_markup(portal):
    client, state = portal
    attack = '"><script>alert(1)</script>'
    answer = _post(client, "/api/airports", {"action": "add", "code": attack})
    assert answer.status_code == 400 and answer.content_type == "application/json"
    state["settings"].favorites = ["</script><script>alert(1)</script>"]  # as if it got in
    html = client.get("/").get_data(as_text=True)
    assert "<script>alert(1)" not in html, "state is embedded as escaped JSON"


def test_portal_refuses_cross_site_changes_on_every_route(portal):
    client, state = portal
    evil = {"Origin": "http://evil.example"}
    for path, data in (
        ("/api/set", {"name": "show_map", "value": False}),
        ("/api/airports", {"action": "home", "code": "EGLL"}),
        ("/api/aircraft", {"action": "add", "entry": "N123AB"}),
        ("/api/pin", {"entry": "N123AB", "on": True}),
        ("/api/receiver", {"url": "http://evil.example/a.json"}),
        ("/api/reset", {"confirm": True}),
    ):
        assert _post(client, path, data, headers=evil).status_code == 403, path
    assert state["applied"] == [] and state["resets"] == 0
    same_site = _post(
        client,
        "/api/set",
        {"name": "show_map", "value": False},
        headers={"Origin": "http://localhost"},
    )
    assert same_site.status_code == 200
    assert client.get("/api/state", headers=evil).status_code == 200, "reading is not changing"


def test_reset_from_the_web_page_must_be_confirmed(portal):
    client, state = portal
    assert _post(client, "/api/reset", {}).status_code == 400
    assert _post(client, "/api/reset", {"confirm": "yes"}).status_code == 400
    assert state["resets"] == 0
    assert _post(client, "/api/reset", {"confirm": True}).status_code == 200
    assert state["resets"] == 1


def test_aircraft_on_the_map_are_suggested_as_favorites_by_registration(portal):
    from flighttrack.feeds.base import Aircraft

    client, state = portal
    base = {"lat": 0.0, "lon": 0.0, "seen_at": 0.0}
    state["flights"] = [
        Aircraft("a1b2c3", **base, callsign="BAW117", registration="G-XLEA", type_code="A388"),
        Aircraft("a1b2c4", **base, callsign="BAW22", registration=None),
        Aircraft("4ca1d3", **base, callsign=None, registration=None),
        Aircraft("abcdef", **base, callsign="DAL5", registration="N123AB", type_code="B739"),
    ]
    found = client.get("/aircraft?q=baw").get_json()
    assert [f["entry"] for f in found] == ["BAW22", "G-XLEA"], "by registration when known"
    assert {k: v for k, v in found[1].items() if k != "seen"} == {
        "entry": "G-XLEA",
        "callsign": "BAW117",
        "registration": "G-XLEA",
        "type": "A388",
    }
    assert isinstance(found[1]["seen"], int), "how long ago, in seconds"
    assert [f["entry"] for f in client.get("/aircraft?q=gxl").get_json()] == ["G-XLEA"], (
        "dashes ignored"
    )
    assert [f["entry"] for f in client.get("/aircraft?q=4ca").get_json()] == ["4CA1D3"]
    assert [f["entry"] for f in client.get("/aircraft?q=n1").get_json()] == ["N123AB"]
    assert client.get("/aircraft?q=").get_json() == [] and client.get("/aircraft").get_json() == []
    assert client.get("/aircraft?q=zzz").get_json() == []


def _app(tmp_path, **kwargs):
    return App(fullscreen=False, data_dir=tmp_path, replay=False, **kwargs)


def test_home_airport_in_settings_centers_the_display_without_any_lookup(tmp_path):
    settings_module.save(Settings(home_airport="EGLL"), tmp_path / "settings.json")

    def locate():
        raise AssertionError("IP lookup must not run when a home airport is set")

    app = _app(tmp_path, locate=locate)
    try:
        assert not app.demo and app.location.source == "airport"
        assert app.home == EGLL_HOME.point
    finally:
        pygame.quit()


def test_ip_estimate_is_used_when_nothing_is_configured_then_airport_replaces_it(tmp_path):
    estimate = Location(48.85, 2.35, "ip", "Paris (estimated)")
    app = _app(tmp_path, locate=lambda: estimate)
    try:
        assert app.demo, "demo until the lookup answers"
        deadline = time.monotonic() + 2
        while not app._reconfigure.is_set() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert app._reconfigure.is_set()
        app._reconfigure.clear()
        app.configure_feeds()
        assert not app.demo and app.home == (48.85, 2.35) and app.location.source == "ip"
        assert app.portal_state().location_label == "Paris (estimated)"

        # Setting a home airport through the portal overrides the estimate and is persisted.
        app.apply_changes({"home_airport": "EGLL", "local_receiver_url": None})
        assert app._reconfigure.is_set()
        app.configure_feeds()
        assert app.location == EGLL_HOME
        assert settings_module.load(tmp_path / "settings.json").home_airport == "EGLL"
        app.draw(time.monotonic())

        # Clearing it goes back to the estimate.
        app.apply_changes({"home_airport": None})
        app.configure_feeds()
        assert app.location == estimate
    finally:
        pygame.quit()


def test_failed_lookup_leaves_the_demo_running(tmp_path):
    app = _app(tmp_path, locate=lambda: None)
    try:
        time.sleep(0.05)
        assert app.demo and not app._reconfigure.is_set()
    finally:
        pygame.quit()


def test_local_receiver_from_the_portal_adds_a_feed(tmp_path):
    settings_module.save(Settings(home_airport="EGLL"), tmp_path / "settings.json")
    app = _app(tmp_path)
    try:
        assert [p.name for p in app.pollers] == ["feed-public", "feed-watch"]
        app.apply_changes({"local_receiver_url": "http://rx.example/aircraft.json"})
        app.configure_feeds()
        assert [p.name for p in app.pollers] == ["feed-public", "feed-local", "feed-watch"]
    finally:
        pygame.quit()


def test_favorites_are_parsed_matched_and_saved_through_the_portal(portal):
    from flighttrack import favorites
    from flighttrack.feeds.base import Aircraft

    entries, rejected = favorites.parse("n123ab, C-GABC\nBAW117;a1b2c3\n n123ab \n")
    assert entries == ["N123AB", "C-GABC", "BAW117", "A1B2C3"] and rejected == []
    _, rejected = favorites.parse("N123AB\n<script>\nWAYTOOLONGTOBEACALLSIGN")
    assert len(rejected) == 2

    keys = favorites.keys(entries)
    base = {"hex": "ffffff", "lat": 0.0, "lon": 0.0, "seen_at": 0.0}
    assert favorites.is_favorite(Aircraft(**base, registration="N123AB"), keys)
    assert favorites.is_favorite(Aircraft(**base, registration="CGABC"), keys), "dashes ignored"
    assert favorites.is_favorite(Aircraft(**base, callsign="BAW117"), keys)
    assert favorites.is_favorite(Aircraft(**{**base, "hex": "a1b2c3"}), keys)
    assert not favorites.is_favorite(Aircraft(**base, registration="N999ZZ"), keys)
    assert not favorites.is_favorite(Aircraft(**base, registration="N123AB"), frozenset())

    client, state = portal
    for typed in ("n123ab", "BAW117", "N-123AB"):  # the last is the first again, with a dash
        assert _post(client, "/api/aircraft", {"action": "add", "entry": typed}).status_code == 200
    assert state["settings"].favorites == ["N123AB", "BAW117"]
    assert client.get("/api/state").get_json()["aircraft"] == ["N123AB", "BAW117"]

    for typed in ("not valid!", "N123AB, BAW117", "", "WAYTOOLONGTOBEACALLSIGN"):
        bad = _post(client, "/api/aircraft", {"action": "add", "entry": typed})
        assert bad.status_code == 400, typed
        assert "not a registration, callsign or hex code" in bad.get_json()["error"]
    _post(client, "/api/aircraft", {"action": "remove", "entry": "n123ab"})
    assert state["settings"].favorites == ["BAW117"]

    # A favorite's track can be pinned, and unpinned; only a favorite's.
    assert _post(client, "/api/pin", {"entry": "BAW117", "on": True}).status_code == 200
    assert state["settings"].pinned_favorites == ["BAW117"]
    assert client.get("/api/state").get_json()["pinned"] == ["BAW117"]
    assert _post(client, "/api/pin", {"entry": "N999ZZ", "on": True}).status_code == 400
    assert _post(client, "/api/pin", {"entry": "BAW117", "on": "yes"}).status_code == 400
    assert _post(client, "/api/pin", {"entry": "BAW117", "on": False}).status_code == 200
    assert state["settings"].pinned_favorites == []


def test_favorites_from_the_portal_apply_without_rebuilding_the_feeds(tmp_path):
    settings_module.save(Settings(home_airport="EGLL"), tmp_path / "settings.json")
    app = _app(tmp_path)
    try:
        app.apply_changes({"home_airport": "EGLL", "favorites": ["N123AB"]})
        assert not app._reconfigure.is_set(), "nothing about the feeds changed"
        assert app.favorite_keys == frozenset({"N123AB"})
        assert settings_module.load(tmp_path / "settings.json").favorites == ["N123AB"]
        assert app.portal_state().settings.favorites == ["N123AB"]
    finally:
        pygame.quit()


def test_favorite_airports_on_the_web_page_accept_any_code_and_always_include_home():
    changes, errors = validate(
        {"home_airport": "lhr", "favorite_airports": "PAO\nklax, EGLL\n", "local_receiver_url": ""}
    )
    assert errors == {}
    assert changes["favorite_airports"] == ["KPAO", "KLAX", "EGLL"], "stored as ICAO idents"

    changes, _ = validate(
        {"home_airport": "LAX", "favorite_airports": "PAO", "local_receiver_url": ""}
    )
    assert changes["favorite_airports"] == ["KLAX", "KPAO"], "home is added if it was left out"

    changes, _ = validate(
        {"home_airport": "", "favorite_airports": "PAO", "local_receiver_url": ""}
    )
    assert changes["home_airport"] is None and changes["favorite_airports"] == ["KPAO"]

    _, errors = validate(
        {"home_airport": "", "favorite_airports": "PAO\nNOPE9", "local_receiver_url": ""}
    )
    assert errors["favorite_airports"] == "No airport found with the code NOPE9."


def test_the_web_page_lists_favorites_as_the_display_does(tmp_path):
    from types import SimpleNamespace

    settings_module.save(
        Settings(
            home_airport="KPAO",
            favorite_airports=["KPAO", "KSJC"],
            favorites=["N456QX", "N55"],
            pinned_favorites=["N456QX"],
        ),
        tmp_path / "settings.json",
    )
    metar = SimpleNamespace(
        lookup=lambda ident: SimpleNamespace(category={"KPAO": "MVFR"}.get(ident))
    )
    app = _app(tmp_path)
    app.metar = metar
    try:
        kpao = airports.find("KPAO")
        now = time.monotonic()
        app.store.set_aircraft(
            "public",
            [Aircraft("abc213", kpao.lat, kpao.lon, now - 7200, registration="N456QX")],
            now,
        )
        answer = _describe(app.portal_state())
        assert [a["category"] for a in answer["airports"]] == ["MVFR", ""]
        assert answer["where"] == {"N456QX": "KPAO, 2h ago", "N55": ""}
        assert answer["pinned"] == ["N456QX"]
        page = create_app(app.portal_state, app.apply_changes).test_client().get("/").get_data(True)
        for part in (
            'id="airport-plus"',
            'id="plane-plus"',
            "function entry(",
            'class="list-head"',
        ):
            assert part in page, part
    finally:
        pygame.quit()
