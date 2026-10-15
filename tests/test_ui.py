import time

import pygame
import pytest

from flighttrack import settings as settings_module
from flighttrack.app import App, main
from flighttrack.feeds.base import Aircraft
from flighttrack.geo import destination, distance_nm
from flighttrack.geo.mercator import zoom_for_range
from flighttrack.platform.backlight import find_backlight
from flighttrack.ui import theme
from flighttrack.ui.layout import compute_layout
from flighttrack.ui.scope import aircraft_near, project

HOME = (43.6777, -79.6248)


def test_layout_on_the_target_panel():
    layout = compute_layout(800, 480)
    assert layout.top == pygame.Rect(0, 0, 800, 48)
    assert layout.bottom == pygame.Rect(0, 448, 800, 32)
    assert layout.view == pygame.Rect(0, 48, 592, 400)
    assert layout.panel == pygame.Rect(592, 48, 208, 400)
    assert layout.ring_radius == 192


@pytest.mark.parametrize("size", [(1024, 600), (1280, 720), (640, 480)])
def test_layout_tiles_other_landscape_sizes_exactly(size):
    layout = compute_layout(*size)
    width, height = size
    assert layout.view.right == layout.panel.left and layout.panel.right == width
    assert layout.top.bottom == layout.view.top and layout.view.bottom == layout.bottom.top
    assert layout.bottom.bottom == height
    assert layout.panel.width >= 200
    assert layout.ring_radius * 2 < min(layout.view.size)


def _zoom(layout, range_nm=10):
    return zoom_for_range(HOME[0], range_nm, layout.ring_radius)


def test_projection_puts_north_up_and_east_right_with_range_on_the_outer_ring():
    layout = compute_layout(800, 480)
    view, radius = layout.view, layout.ring_radius
    zoom = _zoom(layout)
    assert project(HOME, *HOME, zoom, view) == view.center

    x, y = project(HOME, *destination(*HOME, 0, 10), zoom, view)
    assert x == view.centerx and y == pytest.approx(view.centery - radius, abs=1)

    x, y = project(HOME, *destination(*HOME, 90, 5), zoom, view)
    assert y == pytest.approx(view.centery, abs=1)
    assert x == pytest.approx(view.centerx + radius / 2, abs=1)


def test_projection_shows_aircraft_beyond_the_ring_but_not_beyond_the_view():
    layout = compute_layout(800, 480)
    view, zoom = layout.view, _zoom(layout)
    # The view is wider than it is tall: 14 NM east fits, 14 NM north does not.
    assert project(HOME, *destination(*HOME, 90, 14), zoom, view) is not None
    assert project(HOME, *destination(*HOME, 0, 14), zoom, view) is None
    # A feed query sized by corner_factor covers the corners of the view.
    corner = 10 * layout.corner_factor
    assert project(HOME, *destination(*HOME, 56, corner * 0.98), zoom, view) is not None


def test_aircraft_within_reach_of_a_tap_are_found_nearest_first():
    positions = {"aaa": (100, 100), "bbb": (130, 100), "ccc": (400, 300)}
    assert [h for _, h in aircraft_near(positions, (104, 101))] == ["aaa", "bbb"]
    assert [h for _, h in aircraft_near(positions, (124, 100))] == ["bbb", "aaa"]
    assert [h for _, h in aircraft_near(positions, (90, 100))] == ["aaa"]
    assert aircraft_near(positions, (250, 200)) == []
    assert aircraft_near({}, (0, 0)) == []


def test_altitude_bands_and_emergency_colors():
    base = {"hex": "abc123", "lat": 0.0, "lon": 0.0, "seen_at": 0.0}
    assert theme.aircraft_color(Aircraft(**base, altitude_ft=3000)) == theme.AIRCRAFT_LOW
    assert theme.aircraft_color(Aircraft(**base, altitude_ft=18000)) == theme.AIRCRAFT_MID
    assert theme.aircraft_color(Aircraft(**base, altitude_ft=36000)) == theme.AIRCRAFT_HIGH
    assert theme.aircraft_color(Aircraft(**base, on_ground=True)) == theme.AIRCRAFT_GROUND
    emergency = Aircraft(**base, altitude_ft=36000, emergency=True)
    assert theme.aircraft_color(emergency) == theme.EMERGENCY


def test_backlight_discovery(tmp_path):
    assert find_backlight(tmp_path) is None
    assert find_backlight(tmp_path / "missing") is None
    (tmp_path / "10-0045").mkdir()
    (tmp_path / "10-0045" / "brightness").write_text("255\n")
    found = find_backlight(tmp_path)
    assert found.name == "10-0045" and found.writable


def _tap(app, pos):
    for kind in (pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP):
        app.handle_event(pygame.event.Event(kind, pos=pos, button=1))


@pytest.fixture
def demo_app(tmp_path):
    app = App(fullscreen=False, data_dir=tmp_path, replay=True)
    app.pollers[0].poll_once()
    app.draw(time.monotonic())
    yield app
    pygame.quit()


def test_without_a_home_location_the_app_shows_the_demo(tmp_path):
    app = App(fullscreen=False, data_dir=tmp_path, replay=False)
    try:
        assert app.demo and [p.name for p in app.pollers] == ["feed-replay"]
    finally:
        pygame.quit()


def test_with_a_home_location_the_app_uses_live_feeds(tmp_path):
    settings_module.save(
        settings_module.Settings(
            home_lat=51.47, home_lon=-0.45, local_receiver_url="http://receiver.example/a.json"
        ),
        tmp_path / "settings.json",
    )
    app = App(fullscreen=False, data_dir=tmp_path, replay=False)
    try:
        assert not app.demo and app.home == (51.47, -0.45)
        assert [p.name for p in app.pollers] == ["feed-public", "feed-local", "feed-watch"]
        plan = app.public_plan()
        assert (plan.lat, plan.lon, plan.interval_s) == (51.47, -0.45, 5.0)
        assert plan.radius_nm == pytest.approx(10 * app.layout.corner_factor)
        assert app.local_plan().interval_s == 1.0
        app.draw(time.monotonic())  # renders with no data yet
    finally:
        pygame.quit()


def test_buttons_are_touch_sized_and_on_screen(demo_app):
    assert demo_app.range_down.rect.right < demo_app.range_up.rect.left < 150, "both lower left"
    assert demo_app.range_up.rect.bottom <= demo_app.layout.view.bottom
    for button in (
        demo_app.range_down,
        demo_app.range_up,
        demo_app.home_button,
        demo_app.back_button,
    ):
        assert button.rect.width >= theme.MIN_TOUCH_PX
        assert button.rect.height >= theme.MIN_TOUCH_PX
        assert demo_app.screen.get_rect().contains(button.rect)
    assert all(rect.height >= theme.MIN_TOUCH_PX for rect, _ in demo_app.list_targets)


def test_range_buttons_step_and_persist(demo_app, tmp_path):
    app = demo_app
    assert app.settings.range_nm == 10
    _tap(app, app.range_up.rect.center)
    assert app.settings.range_nm == 25
    assert settings_module.load(tmp_path / "settings.json").range_nm == 25
    _tap(app, app.range_down.rect.center)
    _tap(app, app.range_down.rect.center)
    assert app.settings.range_nm == 5

    # Pressing on a button and sliding off before release must not fire it.
    app.handle_event(
        pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=app.range_up.rect.center, button=1)
    )
    app.handle_event(pygame.event.Event(pygame.MOUSEBUTTONUP, pos=(300, 200), button=1))
    assert app.settings.range_nm == 5
    assert app.selected_hex is None, "the release must not count as a tap on the map either"
    assert app.view_center is None, "nor as a drag of the map"


def test_selecting_from_the_map_and_the_list(demo_app):
    app = demo_app
    assert app.positions and app.list_targets

    # An aircraft with nothing else near it: one tap selects it outright.
    hex_code, pos = next(
        (h, p)
        for h, p in app.positions.items()
        if len(aircraft_near(app.positions, p)) == 1 and not app.airports_near(p)
    )
    _tap(app, pos)
    assert app.selected_hex == hex_code and app.candidates == []
    app.draw(time.monotonic())
    assert app.list_targets == [], "the list is replaced by the selected-aircraft card"

    _tap(app, app.back_button.rect.center)
    assert app.selected_hex is None
    app.draw(time.monotonic())

    rect, row_hex = app.list_targets[1]
    _tap(app, rect.center)
    assert app.selected_hex == row_hex

    # Tapping empty map clears the selection; a drag does nothing.
    empty = (app.layout.view.left + 20, app.layout.view.top + 20)
    assert aircraft_near(app.positions, empty) == [] and not app.airports_near(empty)
    _tap(app, empty)
    assert app.selected_hex is None
    app.draw(time.monotonic())
    app.handle_event(pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=rect.center, button=1))
    app.handle_event(
        pygame.event.Event(pygame.MOUSEBUTTONUP, pos=(rect.centerx, rect.centery + 60), button=1)
    )
    assert app.selected_hex is None


def test_selection_clears_when_the_aircraft_is_lost(demo_app):
    app = demo_app
    app.selected_hex = next(iter(app.positions))
    app.draw(time.monotonic() + 120)  # every record is stale by now
    assert app.selected_hex is None and app.positions == {}


def test_diagnostics_open_from_their_own_pill_and_not_from_the_rest_of_the_bar(demo_app):
    app = demo_app
    pill = app.diagnostics_pill
    assert pill in app.buttons and app.layout.bottom.contains(pill.face)
    assert pill.rect.width >= theme.MIN_TOUCH_PX and pill.rect.height >= 40
    # The bar itself does nothing, wherever it is tapped: not on the range, not in the middle.
    for x in (60, app.layout.bottom.centerx, pill.rect.left - 20):
        _tap(app, (x, app.layout.bottom.centery))
        assert not app.show_diagnostics
    _tap(app, pill.face.center)
    assert app.show_diagnostics and pill.active
    _tap(app, pill.face.center)
    assert not app.show_diagnostics and not pill.active
    _tap(app, pill.face.center)
    lines = app.diagnostics(now=time.monotonic())
    assert "Surface: 800 x 480" in lines
    assert any(line.startswith("Feed replay: 9 ac") for line in lines)
    app.draw(time.monotonic())
    _tap(app, app.back_button.rect.center)
    assert not app.show_diagnostics


def test_aircraft_and_labels_are_drawn_inside_the_view_only(demo_app):
    app = demo_app
    app.settings.range_nm = 50
    app.draw(time.monotonic())
    # Blank everything that legitimately uses these colors (the view, the panel's text and
    # the legend in the top bar); what is left is the bottom bar, which an unclipped label at
    # the edge of the view would spill onto.
    bars_only = app.screen.copy()
    for area in (app.layout.view, app.layout.panel, app.layout.top):
        bars_only.fill((0, 0, 0), area)
    for color in (theme.AIRCRAFT_HIGH, theme.AIRCRAFT_MID, theme.AIRCRAFT_LOW):
        in_view = pygame.mask.from_threshold(app.screen, color, (1, 1, 1, 255)).count()
        assert in_view > 0, f"nothing drawn in {color}"
        assert pygame.mask.from_threshold(bars_only, color, (1, 1, 1, 255)).count() == 0


def test_quit_and_main(tmp_path, monkeypatch, demo_app):
    assert demo_app.handle_event(pygame.event.Event(pygame.QUIT)) is False
    monkeypatch.setenv("FLIGHTTRACK_DATA_DIR", str(tmp_path))
    assert main(["--replay", "--no-portal", "--no-map", "--max-frames", "3"]) == 0


def test_map_and_weather_toggles_change_layers_persist_and_update_credits(tmp_path):
    from flighttrack.tiles import IMAGERY, PLACES
    from flighttrack.tiles.store import TileStore

    store = TileStore(tmp_path / "tiles", fetch=lambda url: None)  # never started: no fetching
    app = App(fullscreen=False, data_dir=tmp_path, replay=True, tile_store=store)
    try:
        app.home = (39.0, -98.0)  # inside NEXRAD and GOES-East coverage
        app.weather.refresh()
        now = time.monotonic()
        names = [spec.layer.name.split("-")[0] for spec in app.map_layers(now)]
        assert names == ["imagery", "clouds", "radar", "places"]
        assert app.map_layers(now)[0].layer is IMAGERY and app.map_layers(now)[-1].layer is PLACES
        assert app.attribution(now) == "Esri | NASA | IEM"
        chips = (
            app.map_toggle,
            app.radar_toggle,
            app.cloud_toggle,
            app.airspace_toggle,
            app.key_toggle,
        )
        for toggle in chips:
            assert (
                toggle.rect.height >= theme.MIN_TOUCH_PX and toggle.rect.width >= theme.MIN_TOUCH_PX
            )
            assert app.layout.top.contains(toggle.rect)
            assert toggle in app.buttons or toggle is app.airspace_toggle  # no airspace here
        assert [c.label for c in chips] == ["MAP", "RAD", "CLD", "AIRSPC", "KEY"]
        assert all(a.rect.right <= b.rect.left for a, b in zip(chips, chips[1:], strict=False))
        app.draw(now)

        def weather():
            return [
                spec.layer.name.split("-")[0]
                for spec in app.map_layers(now)
                if spec.layer.short_lived
            ]

        # Radar and clouds each have a chip, and each goes on and off by itself.
        assert weather() == ["clouds", "radar"]
        _tap(app, app.cloud_toggle.rect.center)
        assert weather() == ["radar"] and not app.cloud_toggle.active and app.radar_toggle.active
        assert app.attribution(now) == "Esri | IEM"
        _tap(app, app.cloud_toggle.rect.center)
        _tap(app, app.radar_toggle.rect.center)
        assert weather() == ["clouds"] and app.attribution(now) == "Esri | NASA"
        _tap(app, app.radar_toggle.rect.center)

        # KEY is the radar's color key and nothing else; no chip ever changes its name.
        assert not app.key_toggle.active and not app.settings.show_radar_key
        _tap(app, app.key_toggle.rect.center)
        assert app.key_toggle.active and app.settings.show_radar_key
        assert weather() == ["clouds", "radar"], "the key changes no layer"
        app.draw(now)  # draws the legend strip
        # With no radar there is nothing to explain: KEY goes out of use, and comes back as
        # it was left when the radar does.
        _tap(app, app.radar_toggle.rect.center)
        assert not app.key_toggle.active and not app.key_toggle.enabled
        _tap(app, app.key_toggle.rect.center)
        assert app.settings.show_radar_key, "a tap on it while out of use changes nothing"
        _tap(app, app.radar_toggle.rect.center)
        assert app.key_toggle.active and app.key_toggle.enabled
        _tap(app, app.key_toggle.rect.center)
        assert not app.settings.show_radar_key

        _tap(app, app.radar_toggle.rect.center)
        _tap(app, app.cloud_toggle.rect.center)
        assert [spec.layer.name for spec in app.map_layers(now)] == ["imagery", "places"]
        assert app.attribution(now) == "Esri"

        _tap(app, app.map_toggle.rect.center)
        assert app.map_layers(now) == () and app.attribution(now) == ""
        saved = settings_module.load(tmp_path / "settings.json")
        assert (saved.show_map, saved.show_radar, saved.show_clouds) == (False, False, False)
        app.draw(now)
        _tap(app, app.radar_toggle.rect.center)
        _tap(app, app.cloud_toggle.rect.center)
        assert weather() == ["clouds", "radar"] and len(app.map_layers(now)) == 2
    finally:
        pygame.quit()


def test_map_shade_follows_daylight_and_the_map_brightness_setting(tmp_path):
    from flighttrack.tiles.store import TileStore

    store = TileStore(tmp_path / "tiles", fetch=lambda url: None)
    app = App(fullscreen=False, data_dir=tmp_path, replay=True, tile_store=store)
    try:
        app._change("show_radar", False)
        app._change("show_clouds", False)
        now = time.monotonic()
        app._daylight_checked, app._daylight = now, 1.0
        day = app.map_layers(now)[0].shade
        assert day == (150, 150, 150) or day == (151, 151, 151)
        app._daylight = 0.0
        night = app.map_layers(now)[0].shade
        assert night[0] < night[2] < day[2], "night is darker and bluer"
        app._change("day_night", False)
        assert app.map_layers(now)[0].shade == day, "switching the effect off means always day"
        app._change("map_brightness", 1.0)
        assert app.map_layers(now)[0].shade == (215, 215, 215)
    finally:
        pygame.quit()


def test_without_tile_or_airspace_stores_those_toggles_are_absent(demo_app):
    shown = demo_app.buttons
    assert demo_app.map_toggle not in shown and demo_app.radar_toggle not in shown
    assert demo_app.airspace_toggle not in shown
    assert demo_app.settings_button in shown


def _drag(app, start, end, steps=4):
    app.handle_event(pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=start, button=1))
    for i in range(1, steps + 1):
        pos = (
            round(start[0] + (end[0] - start[0]) * i / steps),
            round(start[1] + (end[1] - start[1]) * i / steps),
        )
        app.handle_event(
            pygame.event.Event(pygame.MOUSEMOTION, pos=pos, rel=(0, 0), buttons=(1, 0, 0))
        )
    app.handle_event(pygame.event.Event(pygame.MOUSEBUTTONUP, pos=end, button=1))


def test_dragging_the_map_pans_it_and_home_brings_it_back(demo_app):
    app = demo_app
    view = app.layout.view
    start = (view.centerx, view.centery - 60)
    before = dict(app.positions)
    _drag(app, start, (start[0] + 120, start[1] + 40))
    assert app.view_center is not None and app.selected_hex is None
    lat, lon = app.view_center
    assert lat > app.home[0] and lon < app.home[1], "dragging right and down shows north-west"
    assert app.public_plan().lat == lat, "traffic is fetched for what is in view"
    assert app.home_button in app.buttons

    app.draw(time.monotonic())
    moved = [h for h in before if h in app.positions and app.positions[h] != before[h]]
    assert moved, "aircraft moved with the map"
    hex_code = moved[0]
    # The first 16 px of a drag are taken up telling it apart from a tap.
    assert app.positions[hex_code][0] - before[hex_code][0] == pytest.approx(120 - 30, abs=18)

    _tap(app, app.home_button.rect.center)
    assert app.view_center is None and app.home_button not in app.buttons


def test_everything_is_measured_from_the_center_of_the_view(demo_app):
    app = demo_app
    view = app.layout.view
    app.settings.range_nm = 5
    app.draw(time.monotonic())
    near_home = {hex_code for _, hex_code in app.list_targets}
    assert near_home

    # Drag the map so that somewhere about 40 NM west of home is in the middle.
    for _ in range(6):
        _drag(app, (view.left + 60, view.centery), (view.right - 60, view.centery))
    assert 30 < distance_nm(*app.home, *app.center) < 70
    app.pollers[0].poll_once()
    app.draw(time.monotonic())
    assert not near_home & {hex_code for _, hex_code in app.list_targets}

    # Whatever is listed is within range of the center, not of home, and is on screen.
    feed = {a.hex: a for a in app.store.feed().aircraft}
    for _, hex_code in app.list_targets:
        aircraft = feed[hex_code]
        assert distance_nm(*app.center, aircraft.lat, aircraft.lon) <= 5.5
        assert hex_code in app.positions
    assert app.public_plan().lat == app.center[0]
    assert app.weather is None or app.weather._location() == app.center


def test_a_dragged_map_stays_put_and_is_restored_after_a_restart(tmp_path):
    settings_module.save(
        settings_module.Settings(home_lat=48.0, home_lon=11.0), tmp_path / "settings.json"
    )
    app = App(fullscreen=False, data_dir=tmp_path, replay=False)
    view = app.layout.view
    try:
        _drag(app, (view.centerx, view.centery), (view.centerx + 150, view.centery + 90))
        moved = app.view_center
        assert moved is not None
        app.draw(time.monotonic() + 3600)
        assert app.view_center == moved, "no drifting back on its own"
    finally:
        pygame.quit()

    saved = settings_module.load(tmp_path / "settings.json")
    assert (saved.view_lat, saved.view_lon) == moved
    again = App(fullscreen=False, data_dir=tmp_path, replay=False)
    try:
        assert again.view_center == moved and again.home_button in again.buttons
        _tap(again, again.home_button.rect.center)
        assert again.view_center is None
        assert settings_module.load(tmp_path / "settings.json").view_lat is None

        # A new home means a fresh start: the old drag is not applied to it.
        _drag(again, (view.centerx, view.centery), (view.centerx + 150, view.centery))
        assert again.view_center is not None
        again.apply_changes({"home_airport": "EGLL"})
        again.configure_feeds()
        assert again.view_center is None
    finally:
        pygame.quit()


def test_every_setting_survives_a_restart(tmp_path):
    changed = settings_module.Settings(
        home_airport="EGLL",
        favorite_airports=["EGLL", "KLAX"],
        home_lat=48.0,
        home_lon=11.0,
        range_nm=2.5,
        local_receiver_url="http://rx.example/aircraft.json",
        show_map=False,
        map_style="street",
        theme="midnight",
        show_radar=False,
        show_clouds=False,
        show_radar_key=True,
        show_airspace=False,
        show_runways=False,
        hidden_traffic=["mid", "fav"],
        favorites=["N123AB", "BAW117"],
        pinned_favorites=["BAW117"],
        opensky_client_id="my-client",
        opensky_client_secret="s3cret-value",
        flightaware_key="fa-key-123456",
        adsbx_key="adsbx-key-123456",
        portal_password="scrypt$1$1$1$00$00",
        view_lat=51.2,
        view_lon=-0.9,
        day_night=False,
        brightness=0.35,
        map_brightness=0.5,
        weather_opacity=0.25,
        ring_opacity=0.4,
        feature_opacity=0.55,
    )
    defaults = settings_module.Settings()
    untouched = [name for name, value in vars(changed).items() if value == getattr(defaults, name)]
    assert untouched == [], f"add these to this test: {untouched}"
    settings_module.save(changed, tmp_path / "settings.json")
    assert settings_module.load(tmp_path / "settings.json") == changed

    app = App(fullscreen=False, data_dir=tmp_path, replay=False)
    try:
        assert app.settings == changed
        app.draw(time.monotonic())
        assert not app.map_toggle.active and not app.radar_toggle.active
        assert not app.cloud_toggle.active and not app.key_toggle.active, "no radar, no key"
    finally:
        pygame.quit()
    assert settings_module.load(tmp_path / "settings.json") == changed, "running changes nothing"


def test_a_drag_that_starts_in_the_side_panel_does_not_pan(demo_app):
    app = demo_app
    panel_rect = app.layout.panel
    _drag(app, panel_rect.center, (app.layout.view.centerx, panel_rect.centery))
    assert app.view_center is None


def test_backlight_follows_the_brightness_setting_and_the_time_of_day(demo_app, tmp_path):
    from flighttrack.platform.backlight import find_backlight

    device = tmp_path / "bl" / "10-0045"
    device.mkdir(parents=True)
    (device / "brightness").write_text("255\n")
    (device / "max_brightness").write_text("255\n")
    app = demo_app
    app.backlight = find_backlight(tmp_path / "bl")
    now = time.monotonic()
    app._daylight_checked, app._daylight = now, 1.0
    app._backlight_level = None
    app.update_backlight(now)
    assert (device / "brightness").read_text() == "255\n"
    app.set_fraction("brightness", 0.4)
    app.update_backlight(now)
    assert (device / "brightness").read_text() == "102\n"
    app._daylight = 0.0
    app.update_backlight(now)
    assert (device / "brightness").read_text() == "51\n", "half as bright at night"


def test_selected_aircraft_gets_a_track_and_favorites_get_their_color(demo_app):
    app = demo_app
    feed = app.store.feed()
    target = next(a for a in feed.aircraft if not a.on_ground and a.callsign)
    # Feed a few more positions so there is a path to draw.
    from dataclasses import replace

    from flighttrack.geo import destination

    others = [a for a in feed.aircraft if a.hex != target.hex]
    lat, lon = target.lat, target.lon
    for _ in range(5):
        lat, lon = destination(lat, lon, target.track_deg, 0.3)
        moved = replace(target, lat=lat, lon=lon, seen_at=time.monotonic())
        app.store.set_aircraft("replay", [*others, moved], time.monotonic())
    track = app.store.track(target.hex)
    assert len(track) == 6 and track[0][:2] == (target.lat, target.lon)

    app.selected_hex = target.hex
    app.favorite_keys = frozenset({target.callsign})
    app.settings.range_nm = 50
    app.draw(time.monotonic())
    magenta = pygame.mask.from_threshold(app.screen, theme.FAVORITE, (1, 1, 1, 255))
    assert magenta.count() > 20, "favorite drawn in its own color"
    assert theme.aircraft_color(target, app.favorite_keys) == theme.FAVORITE
    emergency = replace(target, emergency=True)
    assert theme.aircraft_color(emergency, app.favorite_keys) == theme.EMERGENCY

    # The selected aircraft's track outlasts it in the feed; another's goes with it, and
    # so does the selected one's once it is no longer selected.
    gone = others[0]
    assert app.store.track(gone.hex)
    app.store.set_aircraft("replay", [a for a in others if a is not gone], time.monotonic())
    app.draw(time.monotonic())
    assert app.store.track(gone.hex) == ()
    assert len(app.store.track(target.hex)) == 6
    app.show_list()
    app.draw(time.monotonic())
    assert app.store.track(target.hex) == ()


def test_range_rings_fade_with_the_opacity_setting(demo_app):
    app = demo_app
    app.store = type(app.store)()  # no aircraft, so only the rings are on the map
    view, radius = app.layout.view, app.layout.ring_radius
    on_ring = (view.centerx + radius - 1, view.centery + 40)  # on the outer ring, clear of labels

    def ring_pixel(opacity):
        app._change("ring_opacity", opacity)
        app.draw(time.monotonic())
        columns = range(on_ring[0] - 4, on_ring[0] + 5)
        x = max(columns, key=lambda px: sum(app.screen.get_at((px, on_ring[1]))[:3]))
        return app.screen.get_at((x, on_ring[1]))[:3]

    assert app.settings.ring_opacity == 0.25, "the default"
    solid, default, hidden = ring_pixel(1.0), ring_pixel(0.25), ring_pixel(0.0)
    assert solid == theme.RING
    assert theme.RING not in (theme.ACCENT, *(c[:3] for c, *_ in theme.AIRSPACE.values()))
    assert hidden == theme.MAP_FILL
    for channel in range(3):
        expected = theme.MAP_FILL[channel] + (theme.RING[channel] - theme.MAP_FILL[channel]) * 0.25
        assert default[channel] == pytest.approx(expected, abs=3)

    # The slider on the settings page covers the whole span, from hidden to solid.
    app.open_settings()
    page = app.page
    page.show_tab("Display")
    page._lay_out()
    rect = next(r for r, item in page._layout if item.label == "Range ring opacity")
    left, right, y = page._track(rect)
    for x, expected in ((left, 0.0), (right, 1.0)):
        for kind in (pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP):
            page.handle_event(pygame.event.Event(kind, pos=(x, y), button=1))
        assert app.settings.ring_opacity == pytest.approx(expected)
    assert settings_module.load(app.settings_path).ring_opacity == pytest.approx(1.0)


def test_map_features_opacity_fades_boundaries_and_runways_together(tmp_path):
    from flighttrack.tiles import PLACES
    from flighttrack.tiles.store import TileStore

    settings_module.save(
        settings_module.Settings(
            home_airport="EGLL", range_nm=5, show_radar=False, show_clouds=False
        ),
        tmp_path / "settings.json",
    )
    store = TileStore(tmp_path / "tiles", fetch=lambda url: None)  # never started
    app = App(fullscreen=False, data_dir=tmp_path, replay=False, tile_store=store)
    try:
        assert app.settings.feature_opacity == 0.8, "the default"
        now = time.monotonic()
        places = next(spec for spec in app.map_layers(now) if spec.layer is PLACES)
        assert places.alpha == 204
        (runways,) = [o for o in app.overlays() if o.key[0] == "runways"]

        def brightest_runway_pixel():
            surface = pygame.Surface(app.layout.view.size)
            (overlay,) = [o for o in app.overlays() if o.key[0] == "runways"]
            overlay.paint(surface, app.center, app.zoom())
            return max(
                sum(surface.get_at((x, y))[:3]) / 3
                for x in range(0, surface.get_width(), 2)
                for y in range(0, surface.get_height(), 2)
            )

        full = sum(theme.RUNWAY) / 3
        assert brightest_runway_pixel() == pytest.approx(full * 0.8, abs=4)

        app._change("feature_opacity", 0.3)
        places = next(spec for spec in app.map_layers(now) if spec.layer is PLACES)
        assert places.alpha == 76
        (faded,) = [o for o in app.overlays() if o.key[0] == "runways"]
        assert faded.key != runways.key, "the map is repainted when the setting changes"
        assert brightest_runway_pixel() == pytest.approx(full * 0.3, abs=4)

        app._change("feature_opacity", 0.0)
        assert brightest_runway_pixel() == 0

        app.open_settings()
        app.page.show_tab("Display")
        assert "Map features opacity" in [item.label for item in app.page.items()]
    finally:
        pygame.quit()


def _fly(app, hex_code, lat, lon, **fields):
    """Put one aircraft in the feed at a position, as the only traffic."""
    from flighttrack.feeds.base import Aircraft

    plane = Aircraft(hex_code, lat, lon, time.monotonic(), **fields)
    app.store.set_aircraft("replay", [plane], time.monotonic())
    return plane


def test_follow_keeps_the_selected_aircraft_centered_until_it_is_switched_off(demo_app, tmp_path):
    from flighttrack.ui import panel

    app = demo_app
    home = app.home
    _fly(app, "abc123", home[0] + 0.05, home[1] + 0.05, callsign="TST1", altitude_ft=5000)
    app._select("abc123")
    app.draw(time.monotonic())
    eye = panel.eye_rect(app.layout.panel)
    assert eye.width >= theme.MIN_TOUCH_PX and app.layout.panel.contains(eye)
    assert not app.following and app.view_center is None

    _tap(app, eye.center)
    assert app.following
    app.draw(time.monotonic())
    assert app.view_center == pytest.approx((home[0] + 0.05, home[1] + 0.05))
    assert app.positions["abc123"] == app.layout.view.center, "it sits in the middle"
    assert app.public_plan().lat == pytest.approx(home[0] + 0.05), "traffic is fetched around it"

    # It moves; the view goes with it.
    _fly(app, "abc123", home[0] + 0.30, home[1] - 0.10, callsign="TST1", altitude_ft=6000)
    app.draw(time.monotonic())
    assert app.view_center == pytest.approx((home[0] + 0.30, home[1] - 0.10))
    assert app.positions["abc123"] == app.layout.view.center
    assert settings_module.load(tmp_path / "settings.json").view_lat is None, "not saved each frame"

    # Switching follow off leaves the map where the aircraft had got to, and remembers it.
    _tap(app, eye.center)
    assert not app.following and app.selected_hex == "abc123"
    stayed = app.view_center
    _fly(app, "abc123", home[0] + 0.60, home[1], callsign="TST1", altitude_ft=6000)
    app.draw(time.monotonic())
    assert app.view_center == stayed
    assert settings_module.load(tmp_path / "settings.json").view_lat == pytest.approx(stayed[0])


def test_follow_ends_on_back_to_list_a_drag_or_another_choice_but_not_when_it_goes_quiet(
    demo_app,
):
    from flighttrack.ui import panel

    app = demo_app
    home = app.home
    eye = panel.eye_rect(app.layout.panel)
    view = app.layout.view

    def follow():
        _fly(app, "abc123", home[0] + 0.2, home[1] + 0.2, callsign="TST1", altitude_ft=5000)
        app._select("abc123")
        app.draw(time.monotonic())
        _tap(app, eye.center)
        app.draw(time.monotonic())
        assert app.following
        return app.view_center

    where = follow()
    _tap(app, app.back_button.rect.center)
    assert not app.following and app.selected_hex is None
    assert app.view_center == where, "Back to list stays at the latest position"

    follow()
    _drag(app, (view.centerx, view.centery + 80), (view.centerx + 90, view.centery + 80))
    assert not app.following and app.view_center is not None, "dragging takes the map back"

    follow()
    app.select_airport(None)
    assert not app.following

    where = follow()
    app.store.set_aircraft("replay", [], time.monotonic())
    app.draw(time.monotonic())
    assert app.following and app.selected_hex == "abc123", "still selected and followed"
    assert app.view_center == pytest.approx(where), "the map stays where it was last seen"
    app.stop_following()

    follow()
    _tap(app, app.home_button.rect.center)
    assert not app.following and app.view_center is None


def test_a_brightness_change_made_outside_the_app_becomes_the_setting(demo_app, tmp_path):
    from flighttrack.platform.backlight import find_backlight

    device = tmp_path / "bl" / "10-0045"
    device.mkdir(parents=True)
    (device / "brightness").write_text("255\n")
    (device / "max_brightness").write_text("255\n")
    app = demo_app
    app.backlight = find_backlight(tmp_path / "bl")
    now = time.monotonic()
    app._daylight_checked, app._daylight = now + 1e6, 1.0  # daytime, and stays so
    app._backlight_level = None
    app.set_fraction("brightness", 0.8)
    app.update_backlight(now)
    assert (device / "brightness").read_text() == "204\n"

    # A button on the display turns it down; within a second or so the setting follows.
    (device / "brightness").write_text("102\n")
    app.update_backlight(now + 2)
    assert app.settings.brightness == pytest.approx(0.4, abs=0.005)
    assert settings_module.load(tmp_path / "settings.json").brightness == pytest.approx(
        0.4, abs=0.005
    )
    app.update_backlight(now + 4)
    assert (device / "brightness").read_text() == "102\n", "and the app does not fight it"

    # The slider still works afterward, and is not mistaken for an outside change.
    app.set_fraction("brightness", 1.0)
    app.update_backlight(now + 6)
    assert (device / "brightness").read_text() == "255\n"
    app.update_backlight(now + 8)
    assert app.settings.brightness == 1.0

    # At night the screen runs at half its setting, and an outside change is read that way.
    app._daylight = 0.0
    app.update_backlight(now + 10)
    assert (device / "brightness").read_text() == "128\n"
    (device / "brightness").write_text("64\n")
    app.update_backlight(now + 12)
    assert app.settings.brightness == pytest.approx(0.5, abs=0.01)

    # Turned up beyond what the night level allows: the setting goes to full and the screen
    # is left as bright as the button made it.
    (device / "brightness").write_text("250\n")
    app.update_backlight(now + 14)
    assert app.settings.brightness == 1.0
    app.update_backlight(now + 16)
    assert (device / "brightness").read_text() == "250\n"


def test_a_selected_aircraft_that_drops_out_stays_selected_grayed_where_it_was_last_heard(
    demo_app,
):
    from flighttrack.ui import panel

    app = demo_app
    home = app.home
    _fly(app, "abc123", home[0] + 0.05, home[1] + 0.05, callsign="TST1", altitude_ft=900)
    app._select("abc123")
    app.draw(time.monotonic())
    where = app.positions["abc123"]

    app.store.set_aircraft("replay", [], time.monotonic())  # landed out of the receivers' view
    app.draw(time.monotonic())
    assert app.selected_hex == "abc123"
    assert app.positions["abc123"] == where, "drawn where it was last heard of"
    amber = pygame.mask.from_threshold(app.screen, theme.STATUS_STALE, (1, 1, 1, 255))
    pane = app.layout.panel
    assert any(
        amber.get_at((x, y))
        for x in range(pane.left, pane.right)
        for y in range(pane.top, pane.top + 120)
    ), "the pane says it is not being updated"

    # Heard again, it is live again.
    _fly(app, "abc123", home[0] + 0.06, home[1] + 0.05, callsign="TST1", altitude_ft=1200)
    app.draw(time.monotonic())
    assert "abc123" not in app._lost

    # Deselected while lost, it is gone from the map.
    app.store.set_aircraft("replay", [], time.monotonic())
    app.draw(time.monotonic())
    _tap(app, app.back_button.rect.center)
    app.draw(time.monotonic())
    assert "abc123" not in app.positions
    assert panel.aircraft_buttons(pane).eye.width >= theme.MIN_TOUCH_PX


def test_only_a_favorite_can_be_pinned_and_its_track_stays_after_it_is_deselected(demo_app):
    # A pin on anything else could be left where nothing lists it to be unpinned from.
    from flighttrack.ui import panel

    app = demo_app
    home = app.home
    _fly(app, "abc123", home[0] + 0.05, home[1] + 0.05, callsign="TST1", altitude_ft=5000)
    _fly(app, "abc123", home[0] + 0.10, home[1] + 0.05, callsign="TST1", altitude_ft=5000)
    app._select("abc123")
    app.draw(time.monotonic())
    buttons = panel.aircraft_buttons(app.layout.panel)
    for rect in (buttons.heart, buttons.pin, buttons.eye):
        assert rect.width >= theme.MIN_TOUCH_PX and app.layout.panel.contains(rect)
    assert buttons.heart.right <= buttons.pin.left and buttons.pin.right <= buttons.eye.left

    _tap(app, buttons.pin.center)
    assert app.settings.pinned_favorites == [], "not a favorite: the pin does nothing"
    _tap(app, buttons.heart.center)
    _tap(app, buttons.pin.center)
    assert app.settings.pinned_favorites == ["TST1"]
    _tap(app, app.back_button.rect.center)
    app.draw(time.monotonic())
    assert app.selected_hex is None
    assert len(app.store.track("abc123")) == 2, "still drawn, not selected"
    _tap(app, app.positions["abc123"])
    app.draw(time.monotonic())
    _tap(app, buttons.pin.center)
    assert app.settings.pinned_favorites == []


def test_the_heart_adds_the_selected_aircraft_to_the_favorites_and_takes_it_out(demo_app, tmp_path):
    from flighttrack.ui import panel

    app = demo_app
    home = app.home
    _fly(app, "abc123", home[0] + 0.05, home[1] + 0.05, callsign="TST1", altitude_ft=5000)
    app._select("abc123")
    app.draw(time.monotonic())
    heart = panel.aircraft_buttons(app.layout.panel).heart

    _tap(app, heart.center)
    assert app.settings.favorites == ["TST1"], "no registration, so by callsign"
    assert "TST1" in app.favorite_keys
    app.settings.favorites.append("ABC123")  # also a favorite by hex, typed in elsewhere
    _tap(app, heart.center)
    assert app.settings.favorites == [] and not app.favorite_keys, "every match comes out"


def test_favorites_tracks_are_pinned_from_the_settings_page_or_the_pane_and_kept(
    demo_app, tmp_path
):
    from flighttrack.ui import panel
    from flighttrack.ui.screens import ROW, SettingsScreen

    app = demo_app
    home = app.home
    app.apply_changes({"favorites": ["TST1", "N55"]})
    _fly(app, "abc123", home[0] + 0.05, home[1] + 0.05, callsign="TST1", altitude_ft=5000)
    _fly(app, "abc123", home[0] + 0.08, home[1] + 0.05, callsign="TST1", altitude_ft=5000)

    # The settings page has a switch for each favorite.
    screen = SettingsScreen(app.screen.get_size(), lambda: None, app, app.wifi)
    screen.show_tab(screen.AIRCRAFT)
    rows = {i.label: i for i in screen.items() if i.kind == ROW}
    assert set(rows) == {"TST1", "N55"} and not rows["TST1"].icons[0].on
    rows["TST1"].icons[0].action()
    assert app.settings.pinned_favorites == ["TST1"]
    assert settings_module.load(tmp_path / "settings.json").pinned_favorites == ["TST1"]

    # Its track is drawn without its being selected.
    app.draw(time.monotonic())
    assert app.selected_hex is None and "abc123" in app._pinned_here

    # The pane's pin on a favorite is the same switch.
    app._select("abc123")
    app.draw(time.monotonic())
    _tap(app, panel.aircraft_buttons(app.layout.panel).pin.center)
    assert app.settings.pinned_favorites == []
    _tap(app, panel.aircraft_buttons(app.layout.panel).pin.center)
    assert app.settings.pinned_favorites == ["TST1"]

    # No longer a favorite, no longer pinned.
    app.apply_changes({"favorites": ["N55"]})
    assert app.settings.pinned_favorites == [] and not app.pinned_keys
