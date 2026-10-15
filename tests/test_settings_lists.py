"""The settings page's lists: a weather badge on each favorite airport, and where each
favorite aircraft was last known to be."""

import time
from types import SimpleNamespace

import pygame

from flighttrack import airports
from flighttrack import settings as settings_module
from flighttrack.app import App
from flighttrack.feeds.base import Aircraft
from flighttrack.ui import screens, theme


def test_a_position_is_told_by_the_nearest_airport():
    kpao = airports.find("KPAO")
    assert airports.whereabouts(kpao.lat + 0.02, kpao.lon) == "KPAO", "within 5 NM: the airport"
    far = airports.whereabouts(kpao.lat - 0.5, kpao.lon - 0.9)  # over the sea
    miles, unit, way, ident = far.split()
    assert unit == "NM" and way in ("N", "NE", "E", "SE", "S", "SW", "W", "NW")
    assert int(miles) > 5 and "-" not in ident
    assert airports.whereabouts(40.0, -40.0) == "", "mid-Atlantic: nothing near"


def test_favorite_airports_show_their_flight_category_and_aircraft_where_last_seen(tmp_path):
    settings_module.save(
        settings_module.Settings(
            home_airport="KPAO",
            favorite_airports=["KPAO", "KSJC"],
            favorites=["N456QX", "DAL1234", "N55"],
        ),
        tmp_path / "settings.json",
    )
    reports = {"KPAO": SimpleNamespace(category="IFR"), "KSJC": "none"}
    metar = SimpleNamespace(lookup=lambda ident: reports.get(ident, "loading"))
    app = App(fullscreen=False, data_dir=tmp_path, replay=False, metar=metar)
    try:
        kpao = airports.find("KPAO")
        now = time.monotonic()
        landed = Aircraft("abc213", kpao.lat, kpao.lon, now - 7200, registration="N456QX")
        flying = Aircraft("a00001", kpao.lat + 0.5, kpao.lon, now, callsign="DAL1234")
        app.store.set_aircraft("public", [landed, flying], now)
        seen = app.favorite_whereabouts()
        assert seen["N456QX"] == "KPAO, 2h ago"
        assert seen["DAL1234"].endswith(", now") and len(seen["DAL1234"]) > len(", now")
        assert "N55" not in seen, "never seen: nothing said"

        app.open_settings()
        page = app.page
        page.show_tab("Airports")
        rows = {i.label.split()[0]: i for i in page.items() if i.kind == screens.ROW}
        assert rows["KPAO"].badge == ("IFR", theme.FLIGHT_CATEGORY["IFR"])
        assert rows["KSJC"].badge is None, "no report, no badge"
        page.draw(app.screen, app.fonts, time.monotonic())

        page.show_tab("Aircraft")
        rows = {i.label: i for i in page.items() if i.kind == screens.ROW}
        assert rows["N456QX"].value == "KPAO, 2h ago" and rows["N55"].value == ""
        page.draw(app.screen, app.fonts, time.monotonic())
    finally:
        pygame.quit()


def test_the_selected_aircraft_gets_its_flight_so_far_and_says_where_it_started(tmp_path):
    settings_module.save(
        settings_module.Settings(home_lat=37.0, home_lon=-122.6), tmp_path / "settings.json"
    )
    asked = []
    kpao = airports.find("KPAO")

    def trace(hex_code):
        asked.append(hex_code)
        now = time.monotonic()
        return [(kpao.lat, kpao.lon, None, now - 900), (kpao.lat + 0.1, kpao.lon, 3000, now - 600)]

    app = App(fullscreen=False, data_dir=tmp_path, replay=False, find_trace=trace)
    try:
        now = time.monotonic()
        plane = Aircraft("abc213", kpao.lat + 0.3, kpao.lon, now, registration="N456QX")
        app.store.set_aircraft("public", [plane], now)
        app._select("abc213")
        app.draw(now)
        deadline = time.monotonic() + 2
        while len(app.store.track("abc213")) < 3 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert asked == ["abc213"] and len(app.store.track("abc213")) == 3
        assert app.departure("abc213") == "From KPAO"
        app.draw(time.monotonic())
        assert asked == ["abc213"], "asked once"
    finally:
        pygame.quit()
