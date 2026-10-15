"""Tiles and settings kept in memory and off the SD card, the work done per frame, the
sources named, the web page's search and tabs, and the home marker."""

import os
import time

import pygame
import pytest
import requests

from flighttrack import app as app_module
from flighttrack import cache, credits
from flighttrack import settings as settings_module
from flighttrack.app import App
from flighttrack.enrich import Enricher
from flighttrack.feeds.base import Aircraft
from flighttrack.portal import PortalState, create_app
from flighttrack.settings import Settings
from flighttrack.state import Store
from flighttrack.tiles import TileLayer
from flighttrack.tiles import store as store_module
from flighttrack.tiles.store import TileStore, memory_budget
from flighttrack.ui import overlays, scope, screens, theme
from flighttrack.ui.screens import SettingsScreen

MAP = TileLayer("test", "http://tiles.example/{z}/{x}/{y}.png", 12, "png", "Test")
WEATHER = TileLayer("wx-1", "http://wx.example/{z}/{x}/{y}.png", 9, "png", "Wx", short_lived=True)
PNG = b"\x89PNG\r\n\x1a\n"


def _tile(number: int, size: int = 1000) -> bytes:
    return PNG + bytes([number % 251]) * (size - len(PNG))


def _wait_for(store, count=1, timeout=3.0):
    arrived, deadline = [], time.monotonic() + timeout
    while len(arrived) < count and time.monotonic() < deadline:
        arrived.extend(store.drain())
        time.sleep(0.005)
    return arrived


def _idle(store, timeout=3.0):
    """Wait until the workers have nothing left to do."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and (store._pending or not store._jobs.empty()):
        time.sleep(0.005)
    time.sleep(0.02)


# ---- tiles: memory first, the card as little as possible ------------------------------------


def test_a_tile_asked_for_again_comes_from_memory_without_touching_the_disk(tmp_path):
    fetched = []

    def fetch(url):
        fetched.append(url)
        return _tile(1)

    store = TileStore(tmp_path, workers=1, fetch=fetch, memory_bytes=100_000)
    store.start()
    try:
        store.request(MAP, 3, 4, 5)
        assert len(_wait_for(store)) == 1
        assert store.path(MAP, 3, 4, 5).exists(), "map imagery is kept for the next start"
        assert (store.disk_writes, store.disk_reads) == (1, 0)
        for _ in range(5):
            store.request(MAP, 3, 4, 5)
            assert _wait_for(store)[0][1] == _tile(1)
        assert len(fetched) == 1 and store.memory_hits == 5
        assert (store.disk_writes, store.disk_reads) == (1, 0), "not one more touch of the card"
    finally:
        store.stop()


def test_reading_a_cached_tile_marks_it_as_used_at_most_once_a_day(tmp_path):
    def offline(url):
        raise requests.ConnectionError("offline")

    path = tmp_path / "test" / "3" / "4" / "5.png"
    path.parent.mkdir(parents=True)
    path.write_bytes(_tile(2))
    an_hour_ago = time.time() - 3600
    os.utime(path, (an_hour_ago, an_hour_ago))

    def read_afresh():
        store = TileStore(tmp_path, workers=1, fetch=offline, memory_bytes=100_000)
        store.start()
        try:
            store.request(MAP, 3, 4, 5)
            assert len(_wait_for(store)) == 1 and store.disk_reads == 1
        finally:
            store.stop()

    read_afresh()
    assert path.stat().st_atime == pytest.approx(an_hour_ago, abs=2), "read, and not written"
    last_week = time.time() - 7 * 86400
    os.utime(path, (last_week, last_week))
    read_afresh()
    assert time.time() - path.stat().st_atime < 60, "not used for a week: marked as used now"
    assert path.stat().st_mtime == pytest.approx(last_week, abs=2), "its age is left alone"


def test_weather_tiles_stay_in_memory_and_reach_the_disk_only_when_pushed_out(tmp_path):
    store = TileStore(
        tmp_path,
        workers=1,
        fetch=lambda url: _tile(int(url.rsplit("/", 1)[1][:-4])),
        memory_bytes=3500,
    )
    store.start()
    try:
        for y in range(3):
            store.request(WEATHER, 5, 1, y)
        assert len(_wait_for(store, 3)) == 3
        _idle(store)
        assert store.disk_writes == 0 and not (tmp_path / "wx-1").exists()
        assert store.memory_used == (3000, 3500)

        # A fourth does not fit. The one used longest ago makes way, and only then is it
        # written, so it is not lost while still current.
        store.request(WEATHER, 5, 1, 3)
        assert len(_wait_for(store)) == 1
        _idle(store)
        assert store.disk_writes == 1
        assert store.path(WEATHER, 5, 1, 0).read_bytes() == _tile(0)
        assert not store.path(WEATHER, 5, 1, 3).exists()
        assert store.memory_used[0] <= 3500

        # A superseded layer is dropped from memory and from the disk together.
        store.forget_layers("wx-", keep="wx-2")
        _idle(store)
        assert store.memory_used[0] == 0 and not (tmp_path / "wx-1").exists()
    finally:
        store.stop()


def test_memory_for_tiles_is_a_share_of_what_is_free_within_limits(tmp_path):
    meminfo = tmp_path / "meminfo"

    def budget(available_kb):
        meminfo.write_text(f"MemTotal: 8000000 kB\nMemAvailable: {available_kb} kB\n")
        return memory_budget(meminfo)

    assert budget(6_000_000) == store_module.MAX_MEMORY_BYTES, "plenty free: the most"
    assert budget(400_000) == 400_000 * 1024 // 8, "less free: an eighth of it"
    assert budget(20_000) == store_module.MIN_MEMORY_BYTES, "never less than a little"
    assert memory_budget(tmp_path / "missing") == store_module.MAX_MEMORY_BYTES // 2


# ---- settings: written late, never lost on a normal stop ------------------------------------


@pytest.fixture
def lazy_app(tmp_path):
    app = App(fullscreen=False, data_dir=tmp_path, replay=True, save_delay_s=300.0)
    yield app, tmp_path / "settings.json"
    pygame.quit()


def test_on_the_display_a_changed_setting_waits_to_be_written_and_many_cost_one_write(lazy_app):
    app, path = lazy_app
    app.step(1)
    app.flip("show_runways")
    app.toggle_traffic("mid")
    for value in (0.3, 0.4, 0.5):
        app.set_fraction("ring_opacity", value)
    app.save_settings()
    assert app.settings.hidden_traffic == ["mid"], "applied at once"
    assert not path.exists() and app.settings_writes == 0, "but not yet on the card"

    assert not app.flush_settings(older_than_s=300.0), "not due yet"
    app._unsaved_since -= 301  # as if five minutes had passed
    assert app.flush_settings(older_than_s=300.0)
    assert app.settings_writes == 1 and settings_module.load(path) == app.settings
    assert not app.flush_settings(), "nothing more to write until something changes"


def test_a_normal_stop_writes_what_was_waiting(lazy_app):
    app, path = lazy_app
    app.flip("show_runways")
    assert not path.exists()
    app.run(max_frames=2)  # the loop ends, as it does when the service is stopped
    assert settings_module.load(path).show_runways is False


def test_shutting_down_from_the_settings_page_writes_first(tmp_path):
    order = []

    class FakePower:
        available = True

        def can_power_off(self):
            return True

        def power_off(self):
            order.append(("off", (tmp_path / "settings.json").exists()))
            return True, "Shutting down..."

    app = App(
        fullscreen=False, data_dir=tmp_path, replay=True, power=FakePower(), save_delay_s=300.0
    )
    try:
        app.flip("day_night")
        page = SettingsScreen(app.screen.get_size(), lambda: None, app, app.wifi)
        now = [100.0]
        page.clock = lambda: now[0]
        page.shut_down()
        now[0] += 1.0
        page.shut_down()
        deadline = time.monotonic() + 2
        while not order and time.monotonic() < deadline:
            time.sleep(0.01)
        assert order == [("off", True)], "the settings were on disk before the power went"
    finally:
        pygame.quit()


def test_in_tests_and_by_default_settings_are_written_at_once(tmp_path):
    app = App(fullscreen=False, data_dir=tmp_path, replay=True)
    try:
        app.flip("show_runways")
        assert settings_module.load(tmp_path / "settings.json").show_runways is False
    finally:
        pygame.quit()
    assert app_module.SETTINGS_SAVE_DELAY_S == 300.0, "five minutes"


# ---- less work per frame -------------------------------------------------------------------


@pytest.fixture
def app(tmp_path):
    settings_module.save(
        Settings(home_lat=51.0, home_lon=-1.0, range_nm=10), tmp_path / "settings.json"
    )
    app = App(fullscreen=False, data_dir=tmp_path, replay=False)
    yield app
    pygame.quit()


def _count(monkeypatch, owner, name):
    calls = []
    real = getattr(owner, name)
    monkeypatch.setattr(owner, name, lambda *a, **k: (calls.append(1), real(*a, **k))[1])
    return calls


def test_a_frame_works_out_its_layers_and_overlays_once(app, monkeypatch):
    app.draw(time.monotonic())
    layers = _count(monkeypatch, app, "map_layers")
    found = _count(monkeypatch, app, "overlays")
    app.draw(time.monotonic())
    assert (len(layers), len(found)) == (1, 1)


def test_a_track_is_copied_and_placed_only_when_it_grows(app, monkeypatch):
    store = Store()
    now = time.monotonic()
    for step in range(30):
        plane = Aircraft("abc123", 51.0 + step * 0.01, -1.0, now, callsign="TST1", altitude_ft=9000)
        store.set_aircraft("public", [plane], now)
    track = store.track("abc123")
    assert len(track) == 30 and store.track("abc123") is track, "the same tuple, not a copy"
    store.set_aircraft("public", [plane], now)  # the feed answers; the aircraft has not moved
    assert store.track("abc123") is track
    further = Aircraft("abc123", 51.5, -1.0, now, callsign="TST1", altitude_ft=9000)
    store.set_aircraft("public", [further], now)
    assert len(store.track("abc123")) == 31 and store.track("abc123") is not track
    assert store.track("nobody") == ()

    app.store = store
    app._select("abc123")
    app.draw(now)
    placed = _count(monkeypatch, scope, "world_px")
    for frame in range(10):
        app.draw(now + frame / 30)
    assert len(placed) == 10, "the view center each frame, and not one track point"


def test_the_frame_covers_the_whole_screen_without_first_wiping_it(app):
    # Whatever was on the screen before, nothing of it shows through afterward.
    garish = (255, 0, 255)
    app.screen.fill(garish)
    app.draw(time.monotonic())
    width, height = app.screen.get_size()
    left_over = [
        (x, y)
        for x in range(0, width, 7)
        for y in range(0, height, 7)
        if app.screen.get_at((x, y))[:3] == garish
    ]
    assert left_over == []


def test_a_buttons_label_is_rendered_once_not_every_frame(app, monkeypatch):
    app.draw(time.monotonic())
    rendered = []
    real = app.fonts.body

    class Counting:
        def render(self, *args):
            rendered.append(args[0])
            return real.render(*args)

        def size(self, text):
            return real.size(text)

        def __hash__(self):
            return hash(real)

        def __eq__(self, other):
            return other is real or other is self

    counting = Counting()
    for _ in range(5):
        app.range_up.draw(app.screen, counting)
    assert rendered.count("+") <= 1


def test_the_full_frame_rate_is_the_same_at_every_range(app):
    now = time.monotonic()
    app._active_until = now + 1
    rates = set()
    for range_nm in settings_module.RANGES_NM:
        app.settings.range_nm = range_nm
        rates.add(app.frame_rate(now))
    assert rates == {30}
    assert not hasattr(app_module, "WIDE_FRAME_RATE")


# ---- sources, the web page, and the home marker ---------------------------------------------


def test_every_source_is_named_briefly_on_the_web_page_and_in_full_on_the_about_tab(app):
    names = [name for name, _, _ in credits.SOURCES]
    for expected in ("adsb.fi", "adsb.lol", "Esri", "IEM", "RainViewer", "NASA GIBS", "FAA"):
        assert expected in names
    assert "OurAirports" in names and "ipinfo.io" in names
    assert any("METAR" in what for _, what, _ in credits.SOURCES)

    client = create_app(lambda: PortalState(Settings(), None, None), lambda c: None).test_client()
    page = client.get("/").get_data(as_text=True)
    footer = page[page.index("<footer>") : page.index("</footer>")]
    assert all(name in footer for name in names if name not in credits.KEYED), "each named"
    assert not any(name in footer for name in credits.KEYED), "those with a key: About only"
    assert credits.NOT_FOR_NAVIGATION in footer
    assert len(credits.BRIEF) < 160, "and briefly"

    app.open_settings()
    app.page.show_tab("About")
    about = [item.label for item in app.page.items()]
    for name, what, more in credits.SOURCES:
        assert f"{name}: {what}" in about and any(more in line for line in about)
    assert credits.NOT_FOR_NAVIGATION in about
    app.page.draw(app.screen, app.fonts, time.monotonic())


def test_the_web_page_searches_airports_as_the_display_does():
    from flighttrack import airports

    near = (51.5, -0.1)
    state = PortalState(Settings(), "London", "ip", near)
    client = create_app(lambda: state, lambda c: None).test_client()

    found = client.get("/airports?q=heathrow").get_json()
    assert found[0]["ident"] == "EGLL" and found[0]["iata"] == "LHR"
    assert set(found[0]) == {"ident", "iata", "name", "place"}
    for query in ("lon", "san f", "KJ"):
        mine = [a["ident"] for a in client.get(f"/airports?q={query}").get_json()]
        assert mine == [a.ident for a in airports.search(query, near, 8)], query
    assert client.get("/airports?q=").get_json() == []
    assert client.get("/airports").get_json() == []
    assert client.get("/airports?q=" + "x" * 5000).status_code == 200, "a long query is cut short"
    assert client.post("/airports", data={"q": "x"}).status_code == 405, "read-only"

    page = client.get("/").get_data(as_text=True)
    assert 'data-search="/airports"' in page and 'data-search="/aircraft"' in page
    assert page.count('role="combobox"') == 2


def test_the_home_marker_fades_with_the_map_features(app):
    pygame.font.init()
    font = app.fonts.small

    def strongest(opacity):
        picture, (x, y) = overlays.home_marker(font, "HOME", theme.HOME, opacity)
        return picture.get_at((x, y + 3))  # inside the house

    assert strongest(1.0)[:3] == theme.HOME and strongest(1.0)[3] == 255
    assert strongest(0.5)[3] == pytest.approx(128, abs=2)
    assert strongest(0.0)[3] == 0

    # On the map, panned away from home: the marker follows the setting. (Home is put out at
    # sea, where no airport's own marker stands in for it.)
    app.settings.home_lat, app.settings.home_lon = 48.0, -20.0
    app.configure_feeds()
    assert not app._airport_at_home()
    app.pan(60, 0)
    at = scope.screen_xy(app.center, *app.home, app.zoom(), app.layout.view)

    def on_screen(opacity):
        app._change("feature_opacity", opacity)
        app.draw(time.monotonic())
        return sum(app.screen.get_at((at[0], at[1] + 3))[:3])

    full, half, none = on_screen(1.0), on_screen(0.5), on_screen(0.0)
    assert full > half > none
    assert full == sum(theme.HOME)

    # And the house on the home airport's own marker is repainted when the setting changes.
    from flighttrack import airports

    app.choose_favorite_airport(airports.find("EGLL"))
    app.configure_feeds()
    keys = set()
    for opacity in (1.0, 0.4):
        app._change("feature_opacity", opacity)
        keys.add(next(o.key for o in app.overlays() if o.key[0] == "airports"))
    assert len(keys) == 2
    app.draw(time.monotonic())


def test_the_buttons_on_the_map_are_glass_and_show_what_is_under_them(app):
    from flighttrack.ui.widgets import GlassButton, glass_face

    on_the_map = (app.range_down, app.range_up, app.home_button, app.go_button, app.settings_button)
    assert all(isinstance(button, GlassButton) for button in on_the_map)
    for state in ("up", "down", "off"):
        face = glass_face((52, 52), state)
        assert 60 <= face.get_at((26, 40))[3] <= 200, "tinted, neither clear nor solid"
        assert face.get_at((0, 0))[3] == 0, "rounded corners"

    # The same button over a dark map and over a bright one: the difference comes through.
    spot = (app.range_up.rect.left + 8, app.range_up.rect.bottom - 8)
    seen = []
    for ground in ((10, 20, 24), (230, 230, 230)):
        app.screen.fill(ground)
        app.range_up.draw(app.screen, app.fonts.body)
        seen.append(sum(app.screen.get_at(spot)[:3]))
    assert seen[1] - seen[0] > 150
    # Drawn again and again it does not build up: a frame is the same every time.
    app.draw(time.monotonic())
    once = app.screen.get_at(spot)
    app.draw(time.monotonic())
    assert app.screen.get_at(spot) == once


def test_reset_all_settings_takes_two_taps_and_keeps_home_and_the_favorites(tmp_path):
    from flighttrack import airports

    mine = dict(
        home_airport="EGLL",
        favorite_airports=["EGLL", "KLAX"],
        favorites=["N123AB", "BAW117"],
        local_receiver_url="http://rx.example/aircraft.json",
    )
    changed = Settings(
        **mine,
        range_nm=2.5,
        show_map=False,
        show_radar=False,
        show_clouds=False,
        show_radar_key=True,
        show_airspace=False,
        show_runways=False,
        hidden_traffic=["low", "fav"],
        view_lat=40.0,
        view_lon=-74.0,
        day_night=False,
        brightness=0.3,
        map_brightness=0.4,
        weather_opacity=0.2,
        ring_opacity=0.9,
        feature_opacity=0.1,
    )
    settings_module.save(changed, tmp_path / "settings.json")
    app = App(fullscreen=False, data_dir=tmp_path, replay=False)
    try:
        assert app.view_center == (40.0, -74.0)
        app.open_settings()
        page = app.page
        now = [50.0]
        page.clock = lambda: now[0]
        page.show_tab("System")
        assert [i.label for i in page.items()].count("Reset all settings") == 1

        page.reset()
        assert app.settings == changed, "one tap only asks"
        assert any(i.label == "Reset all settings?" and i.warning for i in page.items())
        page.reset()
        assert app.settings == changed, "and a second at once is not a decision"
        now[0] += 1.0
        page.reset()

        expected = Settings(**mine)
        assert app.settings == expected
        assert settings_module.load(tmp_path / "settings.json") == expected
        # It shows at once: the map is back at home, at the usual range, with everything on.
        assert app.view_center is None and app.center == airports.find("EGLL").point
        assert app.filter_pills["low"].active and app.radar_toggle.active
        assert not app.key_toggle.active and app.hidden_traffic == frozenset()
        assert app.favorite_keys == frozenset({"N123AB", "BAW117"})
        assert "defaults" in page.reset_message
        app.close_page()
        app.draw(time.monotonic())
    finally:
        pygame.quit()
    # Anything added to the settings later is reset too, unless it is named as kept.
    kept = set(settings_module.KEPT_ON_RESET)
    assert kept <= set(vars(Settings())) and "range_nm" not in kept


# ---- the header's chips, and the current location among the favorites ----------------------


def test_every_chip_in_the_header_is_wide_enough_for_its_label(app):
    font = app.fonts.body  # what they are drawn in
    chips = [app.map_toggle, app.radar_toggle, app.cloud_toggle, app.airspace_toggle]
    chips += [app.key_toggle, *app.filter_pills.values()]
    for chip in chips:
        face = chip.rect.inflate(-6, -14)
        text = font.size(chip.label)[0]
        if chip in app.filter_pills.values():
            starts = face.left + chip.PADDING + 2 * chip.DOT + 3  # after the colored dot
            assert face.right - (starts + text) >= 4, f"{chip.label} runs into its edge"
        else:
            assert face.width - text >= 8, f"{chip.label} is cramped"
        assert chip.rect.width >= 48 and app.layout.top.contains(chip.rect)
    assert all(a.rect.right <= b.rect.left for a, b in zip(chips, chips[1:], strict=False))
    # KEY closes the map's chips; then a gap; then the traffic filters.
    first_filter = next(iter(app.filter_pills.values()))
    assert app.airspace_toggle.rect.right == app.key_toggle.rect.left
    assert first_filter.rect.left - app.key_toggle.rect.right >= 10


@pytest.mark.parametrize(
    "payload, place",
    [
        ({"city": "Denver", "region": "Colorado", "country": "US"}, "Denver, CO"),
        ({"city": "London", "region": "England", "country": "GB"}, "London, GB"),
        ({"city": "Springfield", "region": "Nowhere", "country": "US"}, "Springfield"),
        ({"region": "Texas", "country": "US"}, "TX"),
        ({"city": 7, "country": None}, ""),
    ],
)
def test_the_estimate_says_where_it_is_as_city_and_state_or_country(payload, place):
    from flighttrack.location import _parse_ip_response

    assert _parse_ip_response({"loc": "39.7,-105.0", **payload}).place == place


def test_an_estimate_cached_by_an_older_version_still_loads(tmp_path):
    import json

    from flighttrack.location import IP_CACHE_FILE, ip_location

    class Offline:
        def get(self, *args, **kwargs):
            raise requests.ConnectionError("offline")

    old = {"lat": 39.7, "lon": -105.0, "source": "ip", "label": "Denver (estimated)"}
    (tmp_path / IP_CACHE_FILE).write_text(json.dumps(old))
    found = ip_location(tmp_path, Offline())
    assert found.point == (39.7, -105.0) and found.place == ""


def test_current_location_is_always_the_first_favorite_and_can_be_made_home(tmp_path):
    from flighttrack import airports
    from flighttrack.location import Location

    here = Location(39.7, -105.0, "ip", "Denver (estimated)", "Denver, CO")
    settings_module.save(
        Settings(home_airport="EGLL", favorite_airports=["EGLL", "KLAX"]),
        tmp_path / "settings.json",
    )
    asked = []
    app = App(
        fullscreen=False,
        data_dir=tmp_path,
        replay=False,
        locate=lambda: (asked.append(1), here)[1],
    )
    try:
        # It is looked up even though a home airport is set, and finding it moves nothing.
        deadline = time.monotonic() + 2
        while app.current_location() is None and time.monotonic() < deadline:
            time.sleep(0.01)
        assert asked == [1] and app.current_location() == here
        assert not app._reconfigure.is_set() and app.home == airports.find("EGLL").point
        assert app.current_location_label() == "Current Location (Denver, CO)"

        # On the GO page: first in the favorites, above the airports; a tap only chooses it.
        app.open_go()
        page = app.page
        page.draw(app.screen, app.fonts, 0.0)
        rows = page.favorite_rects()
        assert rows[0][1] == here and [a.ident for _, a in rows[1:]] == ["EGLL", "KLAX"]
        for kind in (pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP):
            page.handle_event(pygame.event.Event(kind, pos=rows[0][0].center, button=1))
        assert page.chosen == here and app.page is page
        page.draw(app.screen, app.fonts, 0.0)
        # GO only looks: the view goes there and home stays at the airport.
        page.go(False)
        assert app.page is None and app.view_center == here.point
        assert app.settings.home_airport == "EGLL"

        # Made home (from the settings page; here directly): no home airport, the airports
        # still favorites.
        app.open_go()
        app.page.draw(app.screen, app.fonts, 0.0)
        app.page.chosen = here
        app.page.go(True)
        assert app.settings.home_airport is None
        assert app.settings.favorite_airports == ["EGLL", "KLAX"]
        app.configure_feeds()
        assert app.home == here.point and app.current_location_is_home()
        assert settings_module.load(tmp_path / "settings.json").home_airport is None
        app.draw(time.monotonic())

        # On the settings page: first on the Airports tab, marked Home, with no Remove.
        app.open_settings()
        app.page.show_tab("Airports")
        rows = [i for i in app.page.items() if i.kind == screens.ROW]
        first = rows[0]
        assert first.label == "Current Location (Denver, CO)" and first.icons[0].on
        assert first.icons[1] is None and not first.disabled, "a house, and no bin"
        assert rows[1].label.startswith("EGLL") and not rows[1].icons[0].on
        # Its house moves home to the airport; the current location's moves it back.
        rows[1].icons[0].action()
        app.configure_feeds()
        assert app.settings.home_airport == "EGLL" and not app.current_location_is_home()
        first.icons[0].action()
        assert app.settings.home_airport is None
        app.page.draw(app.screen, app.fonts, time.monotonic())
    finally:
        pygame.quit()


def test_before_the_location_is_known_its_row_is_there_but_does_nothing(app):
    assert app.current_location() is None
    assert app.current_location_label() == "Current Location (not known yet)"
    app.open_go()
    page = app.page
    page.draw(app.screen, app.fonts, 0.0)
    rect, row = page.favorite_rects()[0]
    assert row is None
    for kind in (pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP):
        page.handle_event(pygame.event.Event(kind, pos=rect.center, button=1))
    assert page.chosen is None
    app.close_page()
    app.go_here(True)  # nothing to go to: nothing happens
    app.open_settings()
    app.page.show_tab("Airports")
    assert next(i for i in app.page.items() if i.label.startswith("Current")).disabled


def test_changes_from_the_web_page_show_on_the_display_at_the_next_frame(tmp_path):
    settings_module.save(
        Settings(home_airport="EGLL", favorite_airports=["EGLL"], range_nm=2.5, brightness=0.3),
        tmp_path / "settings.json",
    )
    from flighttrack.tiles.store import TileStore

    store = TileStore(tmp_path / "tiles", fetch=lambda url: None)
    app = App(fullscreen=False, data_dir=tmp_path, replay=False, tile_store=store)
    try:
        web = create_app(app.portal_state, app.apply_changes, reset=app.request_reset).test_client()
        assert app.radar_toggle.active
        web.post("/api/set", json={"name": "show_radar", "value": False})
        assert app.settings.show_radar is False and app.radar_toggle.active, "not yet drawn"
        app.catch_up()  # what the main loop does before each frame
        assert not app.radar_toggle.active and not app.key_toggle.enabled

        web.post("/api/airports", json={"action": "home", "code": "KLAX"})
        assert app._reconfigure.is_set(), "home moved: the feeds are rebuilt"
        assert settings_module.load(tmp_path / "settings.json").home_airport == "KLAX"

        # Reset from the web: the values at once, the view and controls at the next frame.
        app.pan(50, 0)
        answer = web.post("/api/reset", json={"confirm": True}).get_json()
        assert answer["switches"]["show_radar"] is True, "the page is told the new state"
        assert answer["sliders"]["brightness"]["value"] == 1.0
        assert app.settings.range_nm == settings_module.DEFAULT_RANGE_NM
        assert app.settings.favorite_airports == ["EGLL", "KLAX"]
        app.catch_up()
        assert app.view_center is None and app.radar_toggle.active
        app.draw(time.monotonic())
    finally:
        pygame.quit()


def test_the_radar_key_never_prints_one_word_over_another():
    from flighttrack.tiles.weather import NWS_COLORS
    from flighttrack.ui import bars

    class Canvas(pygame.Surface):
        """Notes where each piece of text was put."""

        def blit(self, image, dest, *rest, **more):
            if isinstance(dest, pygame.Rect) and image.get_height() < 30:
                self.words.append(dest.copy())
            return super().blit(image, dest, *rest, **more)

    pygame.display.init()
    pygame.font.init()
    pygame.display.set_mode((800, 480))
    fonts = theme.Fonts.load()
    try:
        # As wide as it gets, with HOME beside it as well, and narrower still.
        for width in (340, 262, 200):
            canvas = Canvas((800, 480))
            canvas.words = []
            bars.draw_weather_legend(canvas, pygame.Rect(200, 400, width, 38), fonts, NWS_COLORS)
            words = sorted(canvas.words, key=lambda r: r.left)
            assert len(words) >= 3, width
            for a, b in zip(words, words[1:], strict=False):
                assert a.right <= b.left, f"at {width} px two words overlap"
    finally:
        pygame.quit()


# ---- three kinds of map --------------------------------------------------------------------


@pytest.fixture
def mapped(tmp_path):
    from flighttrack.tiles.store import TileStore

    settings_module.save(
        Settings(home_lat=39.0, home_lon=-98.0, range_nm=10, show_radar=False, show_clouds=False),
        tmp_path / "settings.json",
    )
    store = TileStore(tmp_path / "tiles", fetch=lambda url: None)  # never started
    app = App(fullscreen=False, data_dir=tmp_path, replay=False, tile_store=store)
    yield app, tmp_path / "settings.json"
    pygame.quit()


def _layer_names(app):
    return [spec.layer.name for spec in app.map_layers(time.monotonic())]


def test_each_kind_of_map_has_its_own_layers(mapped):
    from flighttrack.tiles import CHART, STREET

    app, path = mapped
    assert app.settings.map_style == "satellite" and _layer_names(app) == ["imagery", "places"]

    app.set_map_style("street")
    assert _layer_names(app) == ["street"], "its own names: no overlay of ours"
    assert app.attribution(time.monotonic()).endswith("Esri")

    # The chart goes over the imagery, so where there is none the map is not blank.
    app.set_map_style("chart")
    assert _layer_names(app) == ["imagery", "vfr"]
    chart = app.map_layers(time.monotonic())[1]
    assert chart.layer is CHART and chart.optional and chart.shade is not None
    assert "FAA" in app.attribution(time.monotonic())
    assert settings_module.load(path).map_style == "chart"
    assert (CHART.min_zoom, CHART.max_zoom) == (8, 12)
    assert CHART.url(10, 291, 391).endswith("/VFR_Sectional/MapServer/tile/10/391/291")
    assert STREET.url(10, 291, 391).endswith("/World_Street_Map/MapServer/tile/10/391/291")

    # Weather still goes on top of whichever map it is, and MAP off turns them all off.
    app._change("show_radar", True)
    app.weather.refresh()
    assert _layer_names(app)[:2] == ["imagery", "vfr"] and len(_layer_names(app)) == 3
    app._change("show_radar", False)
    app.toggle_map()
    assert _layer_names(app) == []
    app.set_map_style("nonsense")
    assert app.settings.map_style == "chart"


def test_the_chart_is_left_out_when_the_view_is_too_wide_for_it(mapped):
    app, _ = mapped
    app.set_map_style("chart")
    shown = {}
    for range_nm in settings_module.RANGES_NM:
        app.settings.range_nm = range_nm
        shown[range_nm] = "vfr" in _layer_names(app)
    assert shown[10] and shown[50], "at the ranges a chart is any use"
    assert not shown[250], "at 250 NM its tiles would be needed by the hundred"
    widest = max(r for r, on in shown.items() if on)
    assert all(on == (r <= widest) for r, on in shown.items())


def test_a_missing_chart_tile_is_not_a_missing_map(mapped):
    from flighttrack.ui.basemap import LayerSpec

    app, _ = mapped
    app.set_map_style("chart")
    app.draw(time.monotonic())
    # Nothing has been fetched (the store was never started): the imagery counts as
    # missing, the chart laid over it does not.
    assert app.basemap.missing_by_layer.get("imagery", 0) > 0
    assert "vfr" not in app.basemap.missing_by_layer
    assert not LayerSpec(app.map_layers(time.monotonic())[0].layer).optional


def test_a_long_press_on_map_steps_through_the_kinds_and_a_tap_still_switches_it(mapped):
    app, path = mapped
    chip = app.map_toggle
    now = [1000.0]
    chip.clock = lambda: now[0]

    def press(seconds):
        app.handle_event(pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=chip.rect.center, button=1))
        now[0] += seconds
        app.draw(time.monotonic())  # a frame goes by with the finger still down
        acted = (app.settings.map_style, app.settings.show_map)
        app.handle_event(pygame.event.Event(pygame.MOUSEBUTTONUP, pos=chip.rect.center, button=1))
        return acted

    # A tap switches the map off and on, as it always did, and changes nothing else.
    press(0.1)
    assert (app.settings.map_style, app.settings.show_map) == ("satellite", False)
    press(0.1)
    assert (app.settings.map_style, app.settings.show_map) == ("satellite", True)

    # Held, it moves on to the next kind while the finger is still down, and letting go
    # does not then switch the map off.
    assert press(0.7) == ("chart", True), "acts during the press, not on release"
    assert (app.settings.map_style, app.settings.show_map) == ("chart", True)
    assert chip.label == "MAP", "the chip keeps its name"
    assert app._flash[0] == "MAP: VFR CHART", "and the map says which, for a moment"
    assert press(0.7)[0] == "street" and press(0.7)[0] == "satellite", "round to the start"
    assert settings_module.load(path).map_style == "satellite"

    # Held while the map is off: it comes on, with the next kind.
    press(0.1)
    assert not app.settings.show_map
    assert press(0.7) == ("chart", True)
    # Too far out for the chart: the notice says so.
    app.settings.range_nm = 250
    app.set_map_style("satellite")
    press(0.7)
    assert "ZOOM IN" in app._flash[0]
    # The notice goes after a moment.
    app.draw(time.monotonic() + 60)
    assert app._flash is None


def test_the_kind_of_map_can_be_chosen_on_the_settings_page_and_the_web_page(mapped):
    app, path = mapped
    app.open_settings()
    app.page.show_tab("Map")
    page = app.page

    def row():
        page._lay_out()
        return next((r, i) for r, i in page._layout if i.label == "Kind of map")

    # One button for each kind, side by side on the one row, the one in force lit.
    rect, item = row()
    assert [(name, chosen) for name, chosen, _ in item.options] == [
        ("Satellite", True),
        ("VFR chart", False),
        ("Street map", False),
    ]
    faces = page.option_rects(rect, item)
    assert all(f.height >= theme.MIN_TOUCH_PX and f.width >= 120 for f in faces)
    assert all(a.right <= b.left for a, b in zip(faces, faces[1:], strict=False))
    assert all(rect.contains(f) for f in faces)

    def tap(pos):
        for kind in (pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP):
            page.handle_event(pygame.event.Event(kind, pos=pos, button=1))

    # A tap on one chooses that one, straight away: no stepping through.
    tap(faces[2].center)
    assert app.settings.map_style == "street"
    tap(faces[1].center)
    assert app.settings.map_style == "chart"
    assert [chosen for _, chosen, _ in row()[1].options] == [False, True, False]
    tap(faces[1].center)
    assert app.settings.map_style == "chart", "a tap on the one in force changes nothing"
    tap((rect.left + 60, rect.centery))
    assert app.settings.map_style == "chart", "nor does a tap on the row's label"
    page.draw(app.screen, app.fonts, time.monotonic())
    lit = app.screen.get_at((faces[1].left + 6, faces[1].top + 6))[:3]
    assert (
        lit == theme.TOGGLE_ON
        and app.screen.get_at((faces[0].left + 6, faces[0].top + 6))[:3] != lit
    )

    web = create_app(app.portal_state, app.apply_changes).test_client()
    state = web.get("/api/state").get_json()
    assert state["choices"]["map_style"] == {
        "value": "chart",
        "options": {"satellite": "Satellite", "chart": "VFR chart", "street": "Street map"},
    }
    assert web.post("/api/set", json={"name": "map_style", "value": "street"}).status_code == 200
    assert app.settings.map_style == "street"
    for bad in ("hybrid", "", 3, None, True):
        assert web.post("/api/set", json={"name": "map_style", "value": bad}).status_code == 400
    assert app.settings.map_style == "street"
    html = web.get("/").get_data(as_text=True)
    assert 'data-choice="map_style"' in html and "<select" not in html, "buttons, not a list"
    assert "map_style" not in settings_module.KEPT_ON_RESET, "a reset goes back to satellite"


def test_an_unknown_kind_of_map_in_the_settings_file_falls_back(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text('{"map_style": "hologram"}')
    assert settings_module.load(path).map_style == "satellite"


# ---- pre-loading, the 30-day limit, stand-in tiles, storage --------------------------------


def test_a_preloaded_tile_goes_into_memory_quietly_and_is_there_when_wanted(tmp_path):
    fetched = []
    store = TileStore(
        tmp_path,
        workers=1,
        fetch=lambda url: (fetched.append(url), _tile(3))[1],
        memory_bytes=10**6,
    )
    store.start()
    try:
        assert store.prefetch(MAP, 9, 1, 2) is True
        _idle(store)
        assert len(fetched) == 1 and store.drain() == [], "fetched, and not handed over to draw"
        assert store.prefetch(MAP, 9, 1, 2) is False, "already in hand: nothing to ask for"
        store.request(MAP, 9, 1, 2)
        assert _wait_for(store)[0][1] == _tile(3)
        assert len(fetched) == 1 and store.memory_hits == 1, "from memory, no second download"
    finally:
        store.stop()


def test_what_is_on_screen_is_fetched_before_what_was_only_preloaded(tmp_path):
    import threading

    order, gate = [], threading.Event()

    def fetch(url):
        gate.wait(2)
        order.append(url.rsplit("/", 2)[-2:])
        return _tile(4)

    store = TileStore(tmp_path, workers=1, fetch=fetch, memory_bytes=10**6)
    store.start()
    try:
        store.prefetch(MAP, 9, 0, 0)  # the worker takes this one up at once, and waits
        time.sleep(0.05)
        for y in (1, 2, 3):
            store.prefetch(MAP, 9, 0, y)
        store.request(MAP, 9, 5, 5)  # asked for last, wanted first
        store.request(MAP, 9, 0, 3)  # one of the quiet ones is now wanted: it jumps the queue
        gate.set()
        arrived = _wait_for(store, 2)
        assert {key for key, _ in arrived} == {("test", 9, 5, 5), ("test", 9, 0, 3)}
        _idle(store)
        assert order[1:3] == [["5", "5.png"], ["0", "3.png"]], order
    finally:
        store.stop()


def test_a_map_tile_is_used_for_thirty_days_and_then_fetched_afresh(tmp_path):
    fetched = []
    answer = {"data": _tile(5)}

    def fetch(url):
        fetched.append(url)
        if isinstance(answer["data"], Exception):
            raise answer["data"]
        return answer["data"]

    path = tmp_path / "test" / "3" / "4" / "5.png"

    def ask():
        store = TileStore(tmp_path, workers=1, fetch=fetch, memory_bytes=10**6)
        store.start()
        try:
            store.request(MAP, 3, 4, 5)
            return _wait_for(store)[0][1]
        finally:
            store.stop()

    assert ask() == _tile(5) and len(fetched) == 1
    # Twenty-nine days on: still the one on the card. Not a request made.
    day = 86400
    age = time.time() - 29 * day
    os.utime(path, (age, age))
    answer["data"] = _tile(6)
    assert ask() == _tile(5) and len(fetched) == 1
    # Thirty-one days: fetched afresh, and the new one kept.
    age = time.time() - 31 * day
    os.utime(path, (age, age))
    assert ask() == _tile(6) and len(fetched) == 2
    assert path.read_bytes() == _tile(6) and time.time() - path.stat().st_mtime < 60
    # Old again, and the network is down: the old one serves.
    os.utime(path, (age, age))
    answer["data"] = requests.ConnectionError("offline")
    assert ask() == _tile(6)
    assert store_module.MAX_AGE_S == 30 * day


def test_clearing_the_tile_cache_empties_memory_and_disk_and_things_load_again(tmp_path):
    fetched = []
    store = TileStore(
        tmp_path,
        workers=1,
        fetch=lambda url: (fetched.append(url), _tile(7))[1],
        memory_bytes=10**6,
    )
    store.start()
    try:
        for y in range(4):
            store.request(MAP, 6, 1, y)
        assert len(_wait_for(store, 4)) == 4
        _idle(store)
        assert store.disk_bytes() == 4000 and store.memory_used[0] == 4000
        store.clear()
        _idle(store)
        assert store.disk_bytes() == 0 and store.memory_used[0] == 0
        assert tmp_path.exists(), "the folder itself stays"
        store.request(MAP, 6, 1, 0)
        assert len(_wait_for(store)) == 1 and len(fetched) == 5
    finally:
        store.stop()


def _picture(color) -> bytes:
    import io

    surface = pygame.Surface((256, 256))
    surface.fill(color)
    buffer = io.BytesIO()
    pygame.image.save(surface, buffer, "tile.png")
    return buffer.getvalue()


def test_while_a_level_loads_the_level_just_left_stands_in_for_it(tmp_path):
    from flighttrack.ui.basemap import Basemap, LayerSpec

    pygame.display.init()
    pygame.display.set_mode((800, 480))
    served = {"on": True}
    red = _picture((200, 30, 30))

    def fetch(url):
        return red if served["on"] else None

    store = TileStore(tmp_path, workers=2, fetch=fetch, memory_bytes=10**7)
    store.start()
    try:
        basemap = Basemap(store, (400, 300), margin=0)
        layers = (LayerSpec(MAP),)
        center = (40.0, -100.0)

        def settle(zoom, timeout=3.0):
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                basemap.pump()
                picture, _ = basemap.render(center, zoom, layers, time.monotonic())
                if not store.busy and not basemap._raw and not basemap._dirty:
                    return picture
                time.sleep(0.01)
            return basemap.render(center, zoom, layers, time.monotonic())[0]

        assert settle(10.0).get_at((200, 150))[:3] == (200, 30, 30)
        # Zoom in by one level, and the new level cannot be had (yet): the picture is made
        # from the old level's tiles, enlarged. No hole.
        served["on"] = False
        closer = settle(11.0)
        assert basemap.missing > 0 and basemap.standing_in == basemap.missing
        assert closer.get_at((200, 150))[:3] == (200, 30, 30)
        assert closer.get_at((5, 5))[:3] == (200, 30, 30) and closer.get_at((395, 295))[:3] == (
            200,
            30,
            30,
        )
        # And out by one from where we started: made from the finer level, reduced.
        further = settle(9.0)
        assert further.get_at((200, 150))[:3] == (200, 30, 30), "the middle, where we had tiles"
    finally:
        store.stop()
        pygame.quit()


def test_the_map_round_a_still_view_is_preloaded_once_and_not_while_dragging(mapped):
    app, _ = mapped
    asked = []
    app.tile_store.prefetch = lambda layer, z, x, y: (asked.append((layer.name, z, x, y)), True)[1]
    app.basemap.missing = 0
    quiet = lambda: setattr(app.basemap, "missing", 0)  # noqa: E731 (the store never loads here)
    base = time.monotonic()

    app.draw(base)
    quiet()
    assert asked == [], "not at once: the view may still be moving"
    app.draw(base + 0.5)
    quiet()
    assert asked == []
    real = app.basemap.render
    app.basemap.render = lambda *a, **k: (real(*a, **k), quiet())[0]
    app.draw(base + 1.2)
    first = len(asked)
    assert first > 20, "the map round the view, and the next range out"
    view_zoom = {z for _, z, _, _ in asked}
    assert len(view_zoom) == 2, "this level, and the one the next range out uses"
    assert {name for name, *_ in asked} == {"imagery", "places"}, "the map, never the weather"
    for later in (1.5, 3, 10):
        app.draw(base + later)
    assert len(asked) == first, "once for a view"

    # A finger down on the map: nothing is pre-loaded until it lifts and the view settles.
    app.pan(80, 0)
    app._press_pos = (100, 100)
    app.draw(base + 20)
    app.draw(base + 22)
    assert len(asked) == first
    app._press_pos = None
    app.draw(base + 23)
    app.draw(base + 24.5)
    assert len(asked) > first, "the new view gets its turn"


def test_storage_is_reported_and_caches_can_be_cleared_from_both_settings_pages(mapped, tmp_path):
    app, _ = mapped
    for layer, size in (("imagery", 3_000_000), ("vfr", 1_000_000), ("radar-nexrad-9", 500_000)):
        (tmp_path / "tiles" / layer / "5").mkdir(parents=True)
        (tmp_path / "tiles" / layer / "5" / "1.jpg").write_bytes(b"x" * size)
    (tmp_path / "enrich" / "photos").mkdir(parents=True)
    (tmp_path / "enrich" / "photos" / "abc123-0.img").write_bytes(b"p" * 40_000)
    (tmp_path / "enrich" / "known.json").write_text("[" + " " * 1998 + "]")
    (tmp_path / "airspace").mkdir()
    (tmp_path / "airspace" / "38_-78.geojson").write_text("{" + " " * 6998 + "}")
    (tmp_path / "images").mkdir()
    (tmp_path / "images" / "N123AB.jpg").write_bytes(b"mine")
    app.enrich = Enricher(tmp_path)
    from flighttrack.airspace import AirspaceStore

    app.airspace = AirspaceStore(tmp_path / "airspace", fetch=lambda cell: {})
    app.tile_store.start()  # it measures what is on disk as it starts
    try:
        deadline = time.monotonic() + 3
        while app.tile_store.disk_usage() is None and time.monotonic() < deadline:
            time.sleep(0.01)
        assert app.tile_store.disk_usage() == {
            "imagery": 3_000_000,
            "vfr": 1_000_000,
            "radar-nexrad-9": 500_000,
        }

        assert app.storage(time.monotonic()) is None, "measured in the background, not on the frame"
        while app.storage(time.monotonic()) is None and time.monotonic() < deadline:
            time.sleep(0.01)
        sizes = app.storage(time.monotonic())
        # Every kind of thing that is cached has its own line, by name.
        assert sizes["items"] == [
            ["Map tiles", 4_000_000],
            ["Weather tiles", 500_000],
            ["Aircraft photos", 40_000],
            ["Aircraft types, operators and routes", 2000],
            ["Airspace boundaries", 7000],
            ["Location estimate", 0],
            ["How low the feeds hear", 0],
        ]
        assert sizes["used"] == 4_549_000 and sizes["total"] > sizes["free"] > 0
        assert sizes["budget"] == cache.budget(tmp_path, sizes["used"])

        app.open_settings()
        page = app.page
        page.show_tab("Storage")
        labels = [item.label for item in page.items()]
        assert any(x.startswith("SD card: ") and " free of " in x for x in labels), labels
        assert any(x.startswith("Caches: 4.5 MB used, up to ") for x in labels), labels
        for line in (
            "    Map tiles: 4.0 MB",
            "    Weather tiles: 0.5 MB",
            "    Aircraft photos: 0.0 MB",
            "    Aircraft types, operators and routes: 0.0 MB",
            "    Airspace boundaries: 0.0 MB",
        ):
            assert line in labels, (line, labels)
        now = [10.0]
        page.clock = lambda: now[0]
        page.clear_caches()
        assert (tmp_path / "tiles" / "imagery" / "5" / "1.jpg").exists(), "one tap only asks"
        assert any(i.label == "Clear caches?" and i.warning for i in page.items())
        now[0] += 1
        page.clear_caches()
        _idle(app.tile_store)
    finally:
        app.tile_store.stop()
    assert not (tmp_path / "tiles" / "imagery").exists() and not (tmp_path / "enrich").exists()
    assert not list((tmp_path / "airspace").glob("*.geojson"))
    assert app.tile_store.disk_usage() == {}
    assert (tmp_path / "images" / "N123AB.jpg").read_bytes() == b"mine", "your own pictures stay"
    assert (tmp_path / "settings.json").exists(), "and so do the settings"
    assert "cleared" in page.cache_message
    page.draw(app.screen, app.fonts, time.monotonic())

    # The web page has the same figures and the same button, and it too must be confirmed.
    cleared = []
    web = create_app(
        app.portal_state,
        app.apply_changes,
        storage=lambda: {
            "free": 5 * 10**9,
            "total": 32 * 10**9,
            "used": 10**8,
            "budget": 2 * 10**9,
            "items": [["Map tiles", 10**8], ["Aircraft photos", 0]],
        },
        clear_caches=lambda: cleared.append(1),
    ).test_client()
    assert web.get("/api/state").get_json()["storage"]["items"][0] == ["Map tiles", 10**8]
    html = web.get("/").get_data(as_text=True)
    assert 'id="tab-storage"' in html and 'id="clear"' in html
    assert web.post("/api/clear-caches", json={}).status_code == 400 and cleared == []
    evil = {"Origin": "http://evil.example"}
    assert web.post("/api/clear-caches", json={"confirm": True}, headers=evil).status_code == 403
    assert web.post("/api/clear-caches", json={"confirm": True}).status_code == 200
    assert cleared == [1]


def test_the_web_pages_tabs_are_wired_to_their_panels():
    html = (
        create_app(lambda: PortalState(Settings(), None, None), lambda c: None)
        .test_client()
        .get("/")
        .get_data(as_text=True)
    )
    for name in SettingsScreen.TABS:
        tab, panel_id = f"tab-{name.lower()}", f"panel-{name.lower()}"
        assert f'id="{tab}" aria-controls="{panel_id}"' in html
        assert f'id="{panel_id}" aria-labelledby="{tab}"' in html, name


def test_the_caches_may_take_the_lesser_of_32_gb_and_half_of_what_is_free_or_theirs(
    tmp_path, monkeypatch
):
    from collections import namedtuple

    usage = namedtuple("usage", "total used free")
    gb = 10**9

    def card(free_gb):
        monkeypatch.setattr(
            cache.shutil, "disk_usage", lambda path: usage(128 * gb, 0, free_gb * gb)
        )

    card(100)
    assert cache.budget(tmp_path, 0) == 32 * gb, "plenty of room: the cap"
    card(20)
    assert cache.budget(tmp_path, 0) == 10 * gb, "half of what is free"
    assert cache.budget(tmp_path, 4 * gb) == 12 * gb, (
        "what the caches hold already counts as theirs"
    )
    card(1)
    assert cache.budget(tmp_path, 9 * gb) == 5 * gb, "a full card: the caches must shrink"
    assert cache.MAX_AGE_S == 30 * 86400

    # The tile store prunes to that, less a little kept back for the small caches. Here the
    # card has 320 MB that is free or the tiles' own, so they may have 160 less the reserve.
    held = {"tiles": 0}
    monkeypatch.setattr(
        cache.shutil,
        "disk_usage",
        lambda path: usage(128 * gb, 0, 320_000_000 - held["tiles"]),
    )
    store = TileStore(tmp_path / "tiles", workers=1, fetch=lambda url: _tile(1, 20_000_000))
    real_count = store._count

    def count(layer, change):
        held["tiles"] += change
        real_count(layer, change)

    store._count = count
    store.start()
    try:
        for y in range(6):  # 120 MB of tiles asked for
            store.request(MAP, 4, 0, y)
        assert len(_wait_for(store, 6, timeout=10)) == 6
        _idle(store)
        limit = 320_000_000 // 2 - store_module.RESERVED_FOR_OTHERS
        assert store.limit() == limit == 96_000_000
        store.prune()
        assert 0 < store.disk_bytes() <= limit, "pruned to fit"
        on_disk = sum(p.stat().st_size for p in (tmp_path / "tiles").rglob("*") if p.is_file())
        assert on_disk == store.disk_bytes(), "the running total matches what is really there"
        # With a fixed limit given (as tests do), that is what is used.
        assert TileStore(tmp_path / "other", max_bytes=1234).limit() == 1234
    finally:
        store.stop()


def test_aircraft_photos_are_fetched_afresh_after_thirty_days_like_the_maps(tmp_path):
    asked = []
    thumb = "https://airport-data.com/images/aircraft/thumbnails/001/598/001598299.jpg"
    pictures = {"now": _picture((200, 30, 30))}

    def get_json(url):
        asked.append(url)
        if "ac_thumb" in url:
            return {"data": [{"image": thumb, "photographer": "wijken"}]}
        return {"response": "unknown aircraft"}

    def get_bytes(url, limit=None):
        asked.append(url)
        answer = pictures["now"]
        if isinstance(answer, Exception):
            raise answer
        return answer

    def look(hex_code="400a0b"):
        one = Enricher(tmp_path, get_json, get_bytes, spacing_s=0.0)
        one.start()
        try:
            deadline = time.monotonic() + 3
            answer = one.photos(hex_code)
            while answer == "loading" and time.monotonic() < deadline:
                time.sleep(0.01)
                answer = one.photos(hex_code)
            return answer
        finally:
            one.stop()

    pygame.display.init()
    first = look()
    assert first[0].data == pictures["now"]
    fetched = asked.count(thumb)
    (tmp_path / "enrich" / "known.json").unlink()  # as if only the picture file remembered it

    # Under thirty days old on disk: used as it is.
    file = tmp_path / "enrich" / "photos" / "400a0b-0.img"
    age = time.time() - 29 * 86400
    os.utime(file, (age, age))
    pictures["now"] = _picture((30, 30, 200))
    assert look()[0].data == first[0].data and asked.count(thumb) == fetched
    (tmp_path / "enrich" / "known.json").unlink()

    # Over thirty: fetched afresh, and the new one kept.
    age = time.time() - 31 * 86400
    os.utime(file, (age, age))
    assert look()[0].data == pictures["now"] and asked.count(thumb) == fetched + 1
    assert time.time() - file.stat().st_mtime < 60
    (tmp_path / "enrich" / "known.json").unlink()

    # Old again and the network is down: the old picture still shows.
    os.utime(file, (age, age))
    kept = pictures["now"]
    pictures["now"] = requests.ConnectionError("offline")
    assert look()[0].data == kept


# ---- the mark, and the name ----------------------------------------------------------------


def test_the_mark_is_in_the_top_bar_before_the_name_and_the_name_is_in_mixed_case(app):
    from flighttrack.ui import bars, brand

    assert bars.TITLE == "FlightTrack"
    app.draw(time.monotonic())
    top = app.layout.top
    icon_box = pygame.Rect(
        bars.PADDING, top.centery - bars.ICON_PX // 2, bars.ICON_PX, bars.ICON_PX
    )
    colors = {
        app.screen.get_at((x, y))[:3]
        for x in range(icon_box.left, icon_box.right)
        for y in range(icon_box.top, icon_box.bottom)
    }

    def near(want):
        return any(sum(abs(a - b) for a, b in zip(c, want, strict=True)) < 40 for c in colors)

    assert near(brand.CYAN) and near(brand.AMBER), "the scope's ring and its aircraft"
    # The chips start after the name, clear of it.
    first_chip = min(b.rect.left for b in (app.map_toggle, *app.filter_pills.values()))
    assert first_chip >= bars.title_right(app.fonts) + 4
    # The same mark at any size is a square picture, and the small ones still show both colors.
    for size in (16, 32, 180):
        picture = brand.icon(size, background=True)
        assert picture.get_size() == (size, size)
        corner = round(size * 0.15)
        assert picture.get_at((corner, corner))[:3] == brand.NIGHT, "on its own dark square"
    assert brand.icon(30).get_at((0, 0))[3] == 0, "and on nothing, for the top bar"


def test_the_web_page_uses_the_mark_as_its_icon_and_says_the_name_the_same_way():
    from pathlib import Path

    from flighttrack.ui import brand

    client = create_app(lambda: PortalState(Settings(), None, None), lambda c: None).test_client()
    html = client.get("/").get_data(as_text=True)
    assert "FLIGHTTRACK" not in html and "FlightTrack</h1>" in html
    assert 'rel="icon" type="image/svg+xml" href="/static/icon.svg"' in html
    assert 'rel="apple-touch-icon" href="/static/icon-180.png"' in html
    svg = client.get("/static/icon.svg")
    assert svg.status_code == 200 and svg.mimetype == "image/svg+xml"
    assert svg.get_data(as_text=True) == brand.svg(), "run scripts/make_icon.py after changing it"
    for path in ("/static/icon-32.png", "/static/icon-180.png", "/favicon.ico"):
        answer = client.get(path)
        assert answer.status_code == 200 and answer.data.startswith(b"\x89PNG"), path
    # Nowhere in the code or the documents is the name still all in capitals.
    root = Path(__file__).parent.parent
    for file in [
        *root.glob("*.md"),
        *root.glob("flighttrack/**/*.py"),
        *root.glob("flighttrack/**/*.html"),
    ]:
        text = file.read_text().replace("FLIGHTTRACK_", "")  # environment variables keep theirs
        assert "FLIGHTTRACK" not in text, file


# ---- the FAA's calendar ---------------------------------------------------------------------


def _utc(year, month, day, hour=12, minute=0):
    import calendar

    return float(calendar.timegm((year, month, day, hour, minute, 0)))


def test_faa_editions_take_effect_on_the_published_dates_at_0901_utc():
    from flighttrack import faa

    # The chart dates the FAA published for 2025 and 2026.
    charts = [
        (2025, 2, 20),
        (2025, 4, 17),
        (2025, 6, 12),
        (2025, 8, 7),
        (2025, 10, 2),
        (2025, 11, 27),
        (2026, 1, 22),
        (2026, 3, 19),
        (2026, 5, 14),
        (2026, 7, 9),
    ]
    for (y, m, d), following in zip(charts, charts[1:], strict=False):
        start = _utc(y, m, d, 9, 1)
        assert faa.cycle_start(start, faa.CHART_S) == start
        assert faa.cycle_start(start - 60, faa.CHART_S) == start - faa.CHART_S, "not a minute early"
        assert faa.next_cycle(start + 1, faa.CHART_S) == _utc(*following, 9, 1)
    # Airspace changes twice as often, on the same calendar.
    assert faa.cycle_start(_utc(2025, 3, 25), faa.AIRAC_S) == _utc(2025, 3, 20, 9, 1)
    assert faa.cycle_start(_utc(2025, 3, 25), faa.CHART_S) == _utc(2025, 2, 20, 9, 1)
    # And it works for dates before the one the calendar is counted from.
    assert faa.cycle_start(_utc(2024, 12, 30), faa.AIRAC_S) == _utc(2024, 12, 26, 9, 1)


def test_what_we_hold_goes_out_of_date_when_an_edition_takes_effect_not_by_its_age():
    from flighttrack import faa

    effective = _utc(2025, 10, 2, 9, 1)
    day = 86400
    # Fetched the day before a new chart edition: out of date the moment it takes effect,
    # though it is one day old.
    assert not faa.out_of_date(effective - day, effective - 60, faa.CHART_S)
    assert faa.out_of_date(effective - day, effective + 60, faa.CHART_S)
    # Fetched well into an edition: good until the next, 50 days later, and not after.
    fetched = effective + 5 * day
    assert not faa.out_of_date(fetched, effective + 55 * day, faa.CHART_S)
    assert faa.out_of_date(fetched, effective + 56 * day + 60, faa.CHART_S)
    # Fetched in the first two days of an edition, when the service may not have switched
    # over: fetched once more after those two days, and then left alone.
    early = effective + 3600
    assert not faa.out_of_date(early, effective + day, faa.CHART_S)
    assert faa.out_of_date(early, effective + 2 * day + 60, faa.CHART_S)
    assert not faa.out_of_date(effective + 2 * day + 120, effective + 30 * day, faa.CHART_S)


def test_chart_tiles_follow_the_faa_calendar_and_other_maps_the_thirty_days(tmp_path, monkeypatch):
    from flighttrack.tiles import CHART, IMAGERY, STREET

    assert CHART.cycle_s == 56 * 86400 and IMAGERY.cycle_s is None and STREET.cycle_s is None
    effective = _utc(2025, 10, 2, 9, 1)
    day = 86400
    stale = TileStore._stale
    # A chart tile fetched ten days before an edition is stale once it is out; an imagery
    # tile of the same age is not.
    assert stale(CHART, effective - 10 * day, effective + 60)
    assert not stale(IMAGERY, effective - 10 * day, effective + 60)
    # A chart tile fetched early in an edition is good for well over thirty days.
    assert not stale(CHART, effective + 3 * day, effective + 50 * day)
    assert stale(IMAGERY, effective + 3 * day, effective + 50 * day)
    assert not stale(WEATHER, 0.0, effective)

    # Through the store: a chart tile on disk from the last edition is fetched afresh, and
    # if that fails the old one serves and is not asked about again on every request.
    fetched = []
    answer = {"data": _tile(8)}

    def fetch(url):
        fetched.append(url)
        if isinstance(answer["data"], Exception):
            raise answer["data"]
        return answer["data"]

    now = {"t": effective + 10 * day}
    monkeypatch.setattr(store_module.time, "time", lambda: now["t"])
    path = tmp_path / "vfr" / "10" / "291" / "391.jpg"
    path.parent.mkdir(parents=True)
    path.write_bytes(_tile(1))
    os.utime(path, (effective - 5 * day, effective - 5 * day))  # from the edition before
    store = TileStore(tmp_path, workers=1, fetch=fetch, memory_bytes=10**6, max_bytes=10**9)
    store.start()
    try:
        store.request(CHART, 10, 291, 391)
        assert _wait_for(store)[0][1] == _tile(8) and len(fetched) == 1, "the new edition"
        os.utime(path, (now["t"], now["t"]))
        now["t"] = effective + 57 * day  # the next edition is out
        answer["data"] = requests.ConnectionError("offline")
        for _ in range(3):
            store.request(CHART, 10, 291, 391)
            assert _wait_for(store)[0][1] == _tile(8), "the old one serves"
        assert len(fetched) == 2, "asked once, not on every request"
    finally:
        store.stop()


def test_airspace_goes_out_of_date_on_the_28_day_calendar_even_while_running(tmp_path, monkeypatch):
    import json as json_module

    from flighttrack import airspace as airspace_module
    from flighttrack.airspace import AirspaceStore

    effective = _utc(2025, 3, 20, 9, 1)
    day = 86400
    assert not airspace_module.out_of_date(effective + day * 3, effective + 27 * day)
    assert airspace_module.out_of_date(effective + day * 3, effective + 28 * day + 60)
    assert airspace_module.out_of_date(effective - day, effective + 60), (
        "a day old, and out of date"
    )

    monkeypatch.setattr(airspace_module, "FETCH_SPACING_S", 0.0)
    calls = []
    store = AirspaceStore(tmp_path, fetch=lambda cell: (calls.append(cell), {"features": []})[1])
    store.start()
    try:
        store.around(49.0, 11.0, 10)
        deadline = time.monotonic() + 3
        while store.version < 1 and time.monotonic() < deadline:
            time.sleep(0.01)
        first = len(calls)
        assert first >= 1
        store.around(49.0, 11.0, 10)
        time.sleep(0.05)
        assert len(calls) == first, "in hand and in date: not fetched again"
        # A new edition comes out while the display is running. At the next hourly check the
        # cells in memory are fetched again.
        monkeypatch.setattr(airspace_module, "out_of_date", lambda fetched, now: True)
        store._checked -= airspace_module.CHECK_LOADED_EVERY_S + 1
        store.around(49.0, 11.0, 10)
        while len(calls) == first and time.monotonic() < deadline + 3:
            time.sleep(0.01)
        assert len(calls) > first
        assert json_module.loads(next(tmp_path.glob("*.geojson")).read_text()) == {"features": []}
    finally:
        store.stop()


# ---- the web page's preview of the screen ---------------------------------------------------


def test_the_preview_is_a_picture_of_the_screen_made_only_while_someone_is_looking(app):
    import io

    assert app._preview is None
    app.draw(time.monotonic())
    app._update_preview(time.monotonic())
    assert app._preview is None, "nobody has asked: no picture is made"

    assert app.preview() is None, "asked: nothing yet, but now one is wanted"
    now = time.monotonic()
    app._update_preview(now)
    first = app.preview()
    assert first.startswith(b"\xff\xd8\xff"), "a JPEG"
    picture = pygame.image.load(io.BytesIO(first))
    assert picture.get_size() == app.screen.get_size()
    # It is the screen: the top bar's color is where the top bar is.
    bar = picture.get_at((400, 3))[:3]
    assert all(abs(a - b) <= 12 for a, b in zip(bar, theme.BAR_FILL, strict=True))

    # Renewed about once a second, not every frame; and not at all once nobody is asking.
    app._update_preview(now + 0.3)
    assert app._preview is first
    app._preview_wanted = now + 1.2
    app._update_preview(now + 1.2)
    assert app._preview is not first
    latest = app._preview
    app._update_preview(now + 60)
    assert app._preview is latest, "nobody has asked for a minute: left alone"


def test_the_preview_is_blank_while_the_settings_keyboard_is_up(app):
    import io

    def shot():
        app.preview()
        app._preview_at = float("-inf")
        if app.page is not None:
            app.page.draw(app.screen, app.fonts, time.monotonic())
        else:
            app.draw(time.monotonic())
        app._update_preview(time.monotonic())
        return pygame.image.load(io.BytesIO(app.preview()))

    def looks_blank(picture):
        # The hiding picture is the plain background with one line of words across the
        # middle: where every page has its header and its Done or Cancel button, nothing.
        spots = [(740, 28), (60, 28), (400, 100), (400, 440)]
        return all(
            abs(a - b) <= 12
            for spot in spots
            for a, b in zip(picture.get_at(spot)[:3], theme.BACKGROUND, strict=True)
        )

    app.open_settings()
    assert not app.typing_in_private() and not looks_blank(shot()), "the settings page itself shows"
    app.page._edit("local_receiver_url", "Local ADS-B receiver", "http://secret", "")
    assert app.page.keyboard is not None and app.typing_in_private()
    hidden = shot()
    assert looks_blank(hidden)
    # Nothing of what was typed is in it: where the keyboard's text box is, it is plain.
    box = app.page.keyboard.box
    assert all(
        sum(hidden.get_at((x, box.centery))[:3]) < 120 for x in range(box.left + 4, box.right, 9)
    )
    app.page.keyboard = None
    assert not looks_blank(shot())

    # The Wi-Fi page's password keyboard, a page deeper, is hidden too.
    from flighttrack.platform.wifi import Wifi
    from flighttrack.ui.screens import WifiScreen

    app.page.child = WifiScreen(app.screen.get_size(), lambda: None, Wifi(available=False))
    assert not app.typing_in_private()
    app.page.child.open_keyboard("Password for HomeNet", "", lambda text: None)
    assert app.typing_in_private() and looks_blank(shot())
    # The GO page's keyboard, for airports and flights, is nothing private.
    app.close_page()
    app.open_go()
    assert not app.typing_in_private()


def test_the_web_page_has_a_preview_tab_that_only_shows_a_picture():
    jpeg = b"\xff\xd8\xff" + b"0" * 100
    held = {"picture": None, "asked": 0}

    def preview():
        held["asked"] += 1
        return held["picture"]

    def client(**more):
        return create_app(
            lambda: PortalState(Settings(), None, None), lambda c: None, **more
        ).test_client()

    web = client(preview=preview)
    html = web.get("/").get_data(as_text=True)
    assert 'id="tab-preview"' in html and 'id="panel-preview" aria-labelledby="tab-preview"' in html
    assert held["asked"] == 0, "loading the page does not take a picture: only the tab does"
    assert web.get("/preview.jpg").status_code == 503, "none made yet: try again"
    held["picture"] = jpeg
    answer = web.get("/preview.jpg")
    assert answer.status_code == 200 and answer.mimetype == "image/jpeg" and answer.data == jpeg
    assert answer.headers["Cache-Control"] == "no-store"
    # View only: there is no way to send anything to it.
    for method in (web.post, web.put, web.delete):
        assert method("/preview.jpg").status_code == 405
    # Without a display behind it (as in other tests), there is no tab and no picture.
    plain = client()
    assert 'id="tab-preview"' not in plain.get("/").get_data(as_text=True)
    assert plain.get("/preview.jpg").status_code == 503


def test_the_preview_picture_is_sized_to_the_window_not_to_the_settings_column():
    html = (
        create_app(
            lambda: PortalState(Settings(), None, None), lambda c: None, preview=lambda: None
        )
        .test_client()
        .get("/")
        .get_data(as_text=True)
    )

    def rule(selector):
        start = html.index(selector + " {")
        return html[start : html.index("}", start)]

    # The whole tab, picture and words, is nine tenths of the window wide and centered.
    assert "width: 90vw" in rule("#panel-preview") and "50% - 45vw" in rule("#panel-preview")
    picture = rule("#preview")
    assert "100vh" in picture and "800 / 480" in picture, "and never taller than the window"


# ---- how the local receiver last answered ---------------------------------------------------


def test_library_errors_are_put_into_plain_words():
    from flighttrack.feeds.http import plain_error

    resolve = (
        "HTTPConnectionPool(host='rx.example', port=80): Max retries exceeded with url: "
        "/aircraft.json (Caused by NameResolutionError(\"Failed to resolve 'rx.example'\"))"
    )
    assert plain_error(resolve) == "That name was not found on the network."
    refused = "HTTPConnectionPool(host='10.0.0.9', port=80): ... [Errno 111] Connection refused"
    assert "connection refused" in plain_error(refused)
    assert (
        plain_error("HTTPConnectionPool(...): Read timed out. (read timeout=3.0)")
        == "No answer in time."
    )
    not_found = "404 Client Error: Not Found for url: http://rx.example/aircraft.json"
    assert (
        plain_error(not_found)
        == "The receiver answered with an error: 404 (nothing at that address)."
    )
    assert (
        plain_error("Expecting value: line 1 column 1 (char 0)")
        == "It answered, but not with aircraft.json."
    )
    assert plain_error("x" * 300).endswith("...") and len(plain_error("x" * 300)) < 100
    assert plain_error("") == "It failed."


def test_the_receivers_last_answer_is_shown_ok_or_failed_with_its_time(tmp_path):
    from flighttrack.ui.format import receiver_lines

    assert receiver_lines(None, 0.0) == [], "no receiver set: nothing to say"
    settings_module.save(
        Settings(
            home_lat=51.0, home_lon=-1.0, local_receiver_url="http://rx.example/aircraft.json"
        ),
        tmp_path / "settings.json",
    )
    app = App(fullscreen=False, data_dir=tmp_path, replay=False)
    try:
        assert app.receiver_status()["checked"] is None
        assert receiver_lines(app.receiver_status(), time.time()) == [
            ("Not asked yet.", False, False)
        ]

        def aircraft_lines():
            app.open_settings()
            app.page.show_tab("Feeds")
            # The lines under the receiver's address, up to the next heading.
            found, under = [], False
            for i in app.page.items():
                if i.label == "Local ADS-B receiver":
                    under = True
                elif under and i.kind == 5:
                    found.append((i.label, i.bright, i.error))
                elif under:
                    break
            app.page.draw(app.screen, app.fonts, time.monotonic())
            app.close_page()
            return found

        # It answers: OK, how many aircraft, and when.
        plane = Aircraft("abc123", 51.0, -1.0, time.monotonic())
        app.store.set_aircraft("local", [plane, plane], time.monotonic() - 3)
        status = app.receiver_status()
        assert status["ok"] is True and status["count"] == 2
        assert time.time() - status["checked"] == pytest.approx(3, abs=0.5)
        ((line, good, bad),) = aircraft_lines()
        clock = time.strftime("%H:%M:%S", time.localtime(status["checked"]))
        assert line == f"Last response {clock} (3 s ago): OK, 2 aircraft" and good and not bad

        # Then it fails: failed, why in plain words, and when it last worked.
        app.store.set_error("local", "[Errno 111] Connection refused", time.monotonic() - 1)
        status = app.receiver_status()
        assert status["ok"] is False and "connection refused" in status["message"]
        assert status["last_good"] == pytest.approx(time.time() - 3, abs=0.5)
        lines = aircraft_lines()
        assert lines[0][0].endswith("(1 s ago): failed") and lines[0][2]
        assert "connection refused" in lines[1][0] and lines[1][2]
        assert lines[2][0].strip().startswith("Last good response ") and "(3 s ago)" in lines[2][0]

        # The web page shows the same words.
        web = create_app(
            app.portal_state, app.apply_changes, receiver_status=app.receiver_status
        ).test_client()
        shown = web.get("/api/state").get_json()["receiver_status"]
        assert [row["text"] for row in shown][1] == lines[1][0].strip()
        assert shown[0]["bad"] and not shown[0]["good"]
        assert 'id="receiver-status"' in web.get("/").get_data(as_text=True)

        # A receiver that has never answered says so.
        app.store = Store()
        app.store.set_error("local", "Read timed out.", time.monotonic())
        assert receiver_lines(app.receiver_status(), time.time())[2][0].strip() == (
            "It has not answered properly yet."
        )
        # With no receiver set there is no status, on either page.
        app.settings.local_receiver_url = None
        assert app.receiver_status() is None and aircraft_lines() == []
        assert web.get("/api/state").get_json()["receiver_status"] == []
    finally:
        pygame.quit()
