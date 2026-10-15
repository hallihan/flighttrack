"""Every theme is held to the same rules: traffic colors that stay apart for people with
any common color-vision deficiency, and chrome text everyone can read."""

import itertools

import pygame
import pytest
from colorvision import VISION, difference, lab, seen

from flighttrack import themes
from flighttrack.ui import theme

# Traffic shown in color on the map, which must be told apart from each other. (Ground
# traffic is a dot, not an aircraft shape: its shape tells it apart.)
TRAFFIC = ("AIRCRAFT_LOW", "AIRCRAFT_MID", "AIRCRAFT_HIGH", "FAVORITE", "EMERGENCY")
# Below this CIEDE2000 difference, two small icons on a busy map could be taken for each
# other. The navy-and-cyan scheme these themes replaced came to 7.5 for deuteranopia.
APART = 12.0
SATELLITE_GROUND = (52, 62, 46)  # typical dimmed imagery under the traffic


@pytest.mark.parametrize("name", list(themes.THEMES))
def test_traffic_colors_stay_apart_for_every_kind_of_color_vision(name):
    colors = themes.THEMES[name]
    for vision in VISION:
        for a, b in itertools.combinations(TRAFFIC, 2):
            gap = difference(seen(colors[a], vision), seen(colors[b], vision))
            assert gap >= APART, f"{name}, {vision}: {a} and {b} only {gap:.1f} apart"


@pytest.mark.parametrize("name", list(themes.THEMES))
def test_altitude_bands_step_down_in_lightness(name):
    # Lightness survives every color-vision deficiency, and gives the bands an order.
    colors = themes.THEMES[name]
    low, mid, high = (lab(colors[r])[0] for r in ("AIRCRAFT_LOW", "AIRCRAFT_MID", "AIRCRAFT_HIGH"))
    assert low - mid >= 10 and mid - high >= 10, (name, low, mid, high)


@pytest.mark.parametrize("name", list(themes.THEMES))
def test_traffic_shows_on_dark_imagery(name):
    colors = themes.THEMES[name]
    for role in TRAFFIC:
        ratio = theme.contrast(colors[role], SATELLITE_GROUND)
        assert ratio >= 3.0, f"{name}: {role} is only {ratio:.1f}:1 on satellite imagery"


@pytest.mark.parametrize("name", list(themes.THEMES))
def test_chrome_text_reads_at_wcag_aa(name):
    c = themes.THEMES[name]
    pairs = {
        "text on panels": (c["TEXT"], c["PANEL_FILL"]),
        "text on bars": (c["TEXT"], c["BAR_FILL"]),
        "dim text on panels": (c["TEXT_DIM"], c["PANEL_FILL"]),
        "dim text on bars": (c["TEXT_DIM"], c["BAR_FILL"]),
        "text on buttons": (c["TEXT"], c["BUTTON_FILL"]),
        "text on a pressed button": (c["TEXT"], c["BUTTON_PRESSED"]),
        "text on a confirm button": (c["TEXT"], c["CONFIRM_FILL"]),
        "text on a lit chip": (c["ON_ACCENT"], c["TOGGLE_ON"]),
        "text on the accent": (c["ON_ACCENT"], c["ACCENT"]),
    }
    for what, (fore, back) in pairs.items():
        ratio = theme.contrast(fore, back)
        assert ratio >= 4.5, f"{name}: {what} is only {ratio:.1f}:1"


@pytest.mark.parametrize("name", list(themes.THEMES))
def test_colored_text_on_the_chrome_is_made_legible(name):
    theme.apply(name)
    try:
        for color in theme._COLORED:
            assert theme.contrast(theme.legible(color), theme.PANEL_FILL) >= 4.5, (name, color)
    finally:
        theme.apply("dark")


def test_the_theme_setting_is_put_in_force_on_the_display_and_the_web_page(tmp_path):
    from flighttrack import settings as settings_module
    from flighttrack.app import App
    from flighttrack.portal import create_app

    settings_module.save(
        settings_module.Settings(home_lat=37.0, home_lon=-122.6), tmp_path / "settings.json"
    )
    app = App(fullscreen=False, data_dir=tmp_path, replay=False)
    try:
        assert theme.NAME == "dark"
        web = create_app(app.portal_state, app.apply_changes).test_client()
        answer = web.post("/api/set", json={"name": "theme", "value": "light"}).get_json()
        assert app.settings.theme == "light"
        assert answer["colors"]["panel"] == "#f9f6ee" and answer["colors"]["scheme"] == "light"
        assert "--panel: #f9f6ee" in web.get("/").get_data(as_text=True)
        app.draw(0.0)  # the next frame puts it in force on the display
        assert theme.NAME == "light" and theme.PANEL_FILL == (249, 246, 238)
        assert web.post("/api/set", json={"name": "theme", "value": "plaid"}).status_code == 400
        app.set_theme("contrast")
        app.draw(0.0)
        assert theme.NAME == "contrast" and theme.LEGEND[0][1] == (255, 249, 31)
    finally:
        theme.apply("dark")
        pygame.quit()
