"""Touch controls, the stale-data banner, the Wi-Fi page and frame pacing."""

import time

import pygame
import pytest

from flighttrack import airports
from flighttrack import app as app_module
from flighttrack import settings as settings_module
from flighttrack.app import App
from flighttrack.feeds.base import Aircraft
from flighttrack.geo.mercator import shifted
from flighttrack.platform.wifi import Wifi
from flighttrack.ui import panel, scope, theme
from flighttrack.ui.scope import resolve_tap, tap_radius

EGLL = airports.find("EGLL")


def _tap(target, pos):
    for kind in (pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP):
        target.handle_event(pygame.event.Event(kind, pos=pos, button=1))


# ---- 1. tap selection ------------------------------------------------------------------------


def test_a_tap_plainly_on_one_target_selects_it_even_with_others_in_reach():
    assert resolve_tap([]) == ("clear", [])
    assert resolve_tap([(20.0, "a")]) == ("select", ["a"])
    # Dead on one icon, another a fingertip away: no need to ask.
    assert resolve_tap([(0.0, "a"), (25.0, "b")]) == ("select", ["a"])
    assert resolve_tap([(8.0, "a"), (22.0, "b"), (30.0, "c")]) == ("select", ["a"])
    # Two nearly as close as each other, or nothing close: ask, nearest first.
    assert resolve_tap([(0.0, "a"), (6.0, "b")]) == ("select", ["a"]), "even a close neighbor"
    assert resolve_tap([(9.0, "a"), (12.0, "b")]) == ("choose", ["a", "b"])
    assert resolve_tap([(20.0, "b"), (18.0, "a")]) == ("choose", ["a", "b"])
    assert resolve_tap([(2.0, "a"), (3.0, "b")]) == ("choose", ["a", "b"]), "on top of each other"


def test_the_reach_of_a_tap_shrinks_with_the_icons_at_wide_ranges():
    assert tap_radius(scope.TAP_RADIUS_PX, 10) == scope.TAP_RADIUS_PX
    assert tap_radius(scope.TAP_RADIUS_PX, 100) < tap_radius(scope.TAP_RADIUS_PX, 50)
    assert tap_radius(scope.TAP_RADIUS_PX, 250) < tap_radius(scope.TAP_RADIUS_PX, 100)
    assert tap_radius(scope.TAP_RADIUS_PX, 250) >= 14, "never smaller than a fingertip can aim"


def _crowd(app, count, spread_px, seed=7):
    """count aircraft scattered within spread_px of the middle of the view."""
    import random

    rng = random.Random(seed)
    planes = []
    for i in range(count):
        dx, dy = rng.uniform(-spread_px, spread_px), rng.uniform(-spread_px * 0.6, spread_px * 0.6)
        lat, lon = shifted(app.center, dx, dy, app.zoom())
        planes.append(
            Aircraft(
                f"{i:06x}",
                lat,
                lon,
                time.monotonic(),
                callsign=f"T{i}",
                altitude_ft=20000,
                track_deg=90,
            )
        )
    app.store.set_aircraft("public", planes, time.monotonic())
    app.draw(time.monotonic())
    return planes


@pytest.fixture
def live_app(tmp_path):
    settings_module.save(
        settings_module.Settings(home_lat=EGLL.lat + 3.0, home_lon=EGLL.lon, range_nm=250),
        tmp_path / "settings.json",
    )
    app = App(fullscreen=False, data_dir=tmp_path, replay=False)
    yield app
    pygame.quit()


def test_in_a_crowded_sky_most_direct_taps_still_select_and_empty_map_still_clears(live_app):
    app = live_app
    _crowd(app, 400, 280)
    direct = selected = 0
    for hex_code, pos in list(app.positions.items())[:150]:
        app._select(None)
        _tap(app, pos)
        direct += 1
        selected += app.selected_hex == hex_code
        if app.candidates:
            assert hex_code in [t for k, t in app.candidates if k == "aircraft"]
    assert selected / direct > 0.9, f"only {selected} of {direct} direct taps selected"

    # A finger is not that exact. Six pixels off the icon, most taps still pick it.
    near_miss = picked = 0
    for hex_code, (x, y) in list(app.positions.items())[:150]:
        app._select(None)
        _tap(app, (x + 4, y - 4))
        near_miss += 1
        picked += app.selected_hex == hex_code
    assert picked / near_miss > 0.45, f"only {picked} of {near_miss} near-miss taps selected"

    cleared = tried = 0
    view = app.layout.view
    for x in range(view.left + 20, view.right - 20, 37):
        for y in range(view.top + 20, view.bottom - 70, 41):
            app._select(next(iter(app.positions)))
            _tap(app, (x, y))
            tried += 1
            cleared += app.selected_hex is None and not app.candidates
    assert cleared / tried > 0.12, f"only {cleared} of {tried} taps on the map cleared"


def test_the_which_one_list_never_runs_under_the_back_button_and_counts_what_it_leaves_out(
    live_app,
):
    app = live_app
    app.settings.range_nm = 10
    planes = _crowd(app, 12, 6)
    _tap(app, app.layout.view.center)
    assert len(app.candidates) == 12
    app.draw(time.monotonic())
    rows = app.candidate_targets
    assert 0 < len(rows) < 12
    assert all(rect.bottom <= app.back_button.rect.top for rect, _ in rows)
    # Every row is reachable: a tap on the last one selects that aircraft, not "Back".
    rect, target = rows[-1]
    _tap(app, rect.center)
    assert app.selected_hex == target.hex
    assert {p.hex for p in planes} >= {target.hex}


# ---- 3. the heart on the home airport --------------------------------------------------------


def test_the_heart_on_the_home_airport_needs_a_second_deliberate_tap(tmp_path, monkeypatch):
    settings_module.save(
        settings_module.Settings(home_airport="EGLL", favorite_airports=["KLAX", "EGLL"]),
        tmp_path / "settings.json",
    )
    clock = [1000.0]
    monkeypatch.setattr(app_module.time, "monotonic", lambda: clock[0])
    app = App(fullscreen=False, data_dir=tmp_path, replay=False)
    try:
        app.select_airport(EGLL)
        app.draw(clock[0])
        heart = panel.heart_rect(app.layout.panel).center

        _tap(app, heart)
        assert app.settings.favorite_airports == ["KLAX", "EGLL"] and app.heart_armed()
        app.draw(clock[0])  # the pane now asks
        _tap(app, heart)
        assert app.settings.home_airport == "EGLL", "a double tap is not a decision"

        clock[0] += app_module.HOME_HEART_MAX_S + 1
        assert not app.heart_armed(), "the question lapses"
        _tap(app, heart)
        clock[0] += 1.0
        _tap(app, heart)
        assert app.settings.favorite_airports == ["KLAX"] and app.settings.home_airport == "KLAX"

        # Any other airport's heart is still a single tap: nothing moves when it changes.
        other = airports.find("KPAO")
        app.select_airport(other)
        _tap(app, heart)
        assert "KPAO" in app.settings.favorite_airports and app.settings.home_airport == "KLAX"
    finally:
        pygame.quit()


# ---- 2. Wi-Fi status -------------------------------------------------------------------------


def test_wifi_status_stays_in_the_header_however_far_the_page_is_scrolled(tmp_path):
    import subprocess
    import threading

    release = threading.Event()
    listing = "yes:HomeNet:64:WPA2\n" + "".join(f"no:Net{i}:{60 - i}:\n" for i in range(12))

    def nmcli(args, timeout):
        if "connect" in args:
            release.wait(3)
            return subprocess.CompletedProcess(
                args, 4, "", "Error: No network with SSID 'Net11' found.\n"
            )
        out = listing if "list" in args else ""
        return subprocess.CompletedProcess(args, 0, out, "")

    app = App(
        fullscreen=False, data_dir=tmp_path, replay=True, wifi=Wifi(runner=nmcli, available=True)
    )
    try:
        app.open_settings()
        app.page.open_wifi()
        page = app.page.child
        deadline = time.monotonic() + 3
        while (page.busy or not page.networks) and time.monotonic() < deadline:
            time.sleep(0.01)
        assert page.status() is None

        # Scroll to the bottom, as you must to reach most networks, and join one.
        page._lay_out()
        page.scroll = 10_000
        page._lay_out()
        rect = next(r for r, item in page._layout if item.label == "Net11")
        assert rect.top > 56
        _tap(app, rect.center)
        assert page.status() == ("Connecting to Net11...", theme.TEXT)
        assert all(item.disabled for item in page.items() if item.action is not None)
        calls_before = page.busy
        _tap(app, rect.center)  # impatient second tap: ignored, and plainly so
        assert page.busy == calls_before

        app.draw(time.monotonic())
        header = app.screen.subsurface(pygame.Rect(90, 0, 560, 56))
        shown = pygame.mask.from_threshold(header, theme.TEXT, (40, 40, 40, 255)).count()
        assert shown > 150, "the header says what is going on"

        release.set()
        deadline = time.monotonic() + 3
        while page.busy and time.monotonic() < deadline:
            time.sleep(0.01)
        assert page.status() == ("That network is no longer in range.", theme.EMERGENCY)
        assert page.scroll > 0, "still scrolled down, and still told"
        app.draw(time.monotonic())
        red = pygame.mask.from_threshold(
            app.screen.subsurface(pygame.Rect(90, 0, 560, 56)), theme.EMERGENCY, (40, 40, 40, 255)
        )
        assert red.count() > 150
    finally:
        release.set()
        pygame.quit()


# ---- 4. frame pacing, 5. follow --------------------------------------------------------------


def test_the_screen_idles_slowly_and_wakes_fully_on_a_touch(live_app):
    app = live_app
    app.settings.range_nm = 10
    now = time.monotonic()
    assert app.frame_rate(now) == app_module.IDLE_FRAME_RATE
    app._active_until = now + app_module.ACTIVE_FOR_S
    assert app.frame_rate(now) == app_module.FRAME_RATE
    assert app.frame_rate(now + app_module.ACTIVE_FOR_S + 0.1) == app_module.IDLE_FRAME_RATE
    # The same at regional ranges: a drag there is as smooth as anywhere.
    app.settings.range_nm = 250
    assert app.frame_rate(now) == app_module.FRAME_RATE == 30


def test_following_an_aircraft_does_not_rebuild_the_map_every_frame(live_app):
    app = live_app
    app.settings.range_nm = 10
    builds = []
    original = app.basemap._compose
    app.basemap._compose = lambda *args: (builds.append(1), original(*args))[1]

    start = app.center
    plane = Aircraft(
        "abc123",
        *start,
        time.monotonic(),
        callsign="TST1",
        altitude_ft=9000,
        ground_speed_kt=450,
        track_deg=90,
    )
    app.store.set_aircraft("public", [plane], time.monotonic())
    app._select("abc123")
    app.following = True
    base = time.monotonic()
    for frame in range(300):  # ten seconds at 30 fps; a 450 kt aircraft covers 1.25 NM
        app.draw(base + frame / 30)
    assert app.view_center != start and app.positions["abc123"] == app.layout.view.center
    assert len(builds) <= 3, f"the map was rebuilt {len(builds)} times in ten seconds of following"


def test_a_drag_across_the_map_rebuilds_it_only_now_and_then(live_app):
    app = live_app
    app.settings.range_nm = 25
    builds = []
    original = app.basemap._compose
    app.basemap._compose = lambda *args: (builds.append(1), original(*args))[1]
    app.draw(time.monotonic())
    builds.clear()
    base = time.monotonic()
    for frame in range(120):  # four seconds of steady dragging, 4 px a frame
        app.pan(-4, 1)
        app.draw(base + frame / 30)
    assert len(builds) <= 12, f"rebuilt {len(builds)} times in 120 frames of dragging"


# ---- 6. stale data ---------------------------------------------------------------------------


def test_old_data_is_grayed_and_announced_and_fresh_data_is_not(live_app):
    app = live_app
    app.settings.range_nm = 10
    now = time.monotonic()
    plane = Aircraft(
        "abc123",
        *shifted(app.center, 60, -40, app.zoom()),
        now,
        callsign="TST1",
        altitude_ft=3000,
        track_deg=45,
    )
    app.store.set_aircraft("public", [plane], now)
    view = app.layout.view

    def count(color):
        return pygame.mask.from_threshold(
            app.screen.subsurface(view), color, (2, 2, 2, 255)
        ).count()

    app.draw(now + 1)
    assert count(theme.AIRCRAFT_LOW) > 40 and count(theme.AIRCRAFT_STALE) < 5
    banner_strip = pygame.Rect(view.left, view.top, view.width, 26)
    plain = app.screen.subsurface(banner_strip).copy()

    # No update for a while: the aircraft is still drawn, but not as if it were live.
    late = now + app_module.STALE_WARNING_S + 10
    app.draw(late)
    assert count(theme.AIRCRAFT_LOW) == 0 and count(theme.AIRCRAFT_STALE) > 40
    amber = pygame.mask.from_threshold(
        app.screen.subsurface(banner_strip), theme.STATUS_STALE, (60, 60, 60, 255)
    )
    assert amber.count() > 5000, "a banner across the top of the map"

    # The feed is failing outright: the banner turns red.
    app.store.set_error("public", "timed out")
    app.draw(late)
    red = pygame.mask.from_threshold(
        app.screen.subsurface(banner_strip), theme.STATUS_DOWN, (60, 60, 60, 255)
    )
    assert red.count() > 5000

    # Data again: back to normal, banner gone.
    app.store.set_aircraft("public", [plane], late)
    app.draw(late + 1)
    assert count(theme.AIRCRAFT_STALE) < 5
    assert pygame.image.tobytes(app.screen.subsurface(banner_strip), "RGB") == pygame.image.tobytes(
        plain, "RGB"
    )


def test_the_demo_is_never_called_stale(tmp_path):
    app = App(fullscreen=False, data_dir=tmp_path, replay=True)
    try:
        app.pollers[0].poll_once()
        view = app.layout.view
        app.draw(time.monotonic() + 45)
        gray = pygame.mask.from_threshold(
            app.screen.subsurface(view), theme.AIRCRAFT_STALE, (2, 2, 2, 255)
        )
        assert gray.count() < 5
    finally:
        pygame.quit()
