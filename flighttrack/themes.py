"""The color themes: plain data, used by the display (ui/theme.py) and the web page.

A theme sets the colors of the chrome (bars, panels, buttons, text) and of the traffic. What
describes the ground on the map (airports, runways, airspace, text over the map) is the same
in every theme: it is drawn over the map, not over the chrome.

Traffic colors are chosen to stay apart under deuteranopia, protanopia and tritanopia as
well as normal vision (tests/test_themes.py holds every theme to that), and to step down in
lightness from low to high altitude, which reads the same for everyone. All chrome text is
at least 4.5:1 against its panel (WCAG AA).
"""

Color = tuple[int, int, int]

# Their names, in the order they are offered.
NAMES: dict[str, str] = {
    "dark": "Dark",
    "light": "Light",
    "contrast": "Contrast",
    "midnight": "Midnight",
    "ember": "Ember",
}
LIGHT_THEMES = frozenset({"light"})  # chrome light, text dark

THEME_ROLES = (
    "BACKGROUND", "BAR_FILL", "PANEL_FILL", "EDGE", "MAP_FILL", "RING", "ACCENT", "ON_ACCENT",
    "TEXT", "TEXT_DIM", "BUTTON_FILL", "BUTTON_PRESSED", "BUTTON_DISABLED", "TOGGLE_ON",
    "CONFIRM_FILL", "DOT_RIM", "AIRCRAFT_LOW", "AIRCRAFT_MID", "AIRCRAFT_HIGH", "FAVORITE",
    "EMERGENCY", "AIRCRAFT_GROUND", "FAVORITE_AIRPORT",
)  # fmt: skip

THEMES: dict[str, dict[str, Color]] = {
    # Graphite chrome, amber accent; cream, cyan and periwinkle traffic, rose favorites.
    "dark": {
        "BACKGROUND": (16, 17, 19), "BAR_FILL": (23, 24, 27), "PANEL_FILL": (30, 32, 36),
        "EDGE": (72, 76, 84), "MAP_FILL": (18, 19, 21), "RING": (255, 255, 255),
        "ACCENT": (255, 178, 36), "ON_ACCENT": (24, 20, 12), "TEXT": (242, 241, 236),
        "TEXT_DIM": (170, 172, 178), "BUTTON_FILL": (42, 45, 51), "BUTTON_PRESSED": (92, 78, 44),
        "BUTTON_DISABLED": (27, 29, 32), "TOGGLE_ON": (230, 158, 30), "CONFIRM_FILL": (150, 44, 40),
        "DOT_RIM": (23, 24, 27), "AIRCRAFT_LOW": (255, 236, 128), "AIRCRAFT_MID": (40, 214, 255),
        "AIRCRAFT_HIGH": (128, 128, 255), "FAVORITE": (249, 137, 167), "EMERGENCY": (255, 64, 48),
        "AIRCRAFT_GROUND": (130, 130, 130), "FAVORITE_AIRPORT": (255, 205, 60),
    },
    # Chart paper and sectional blue, for daylight and the VFR chart.
    "light": {
        "BACKGROUND": (232, 226, 212), "BAR_FILL": (241, 236, 224), "PANEL_FILL": (249, 246, 238),
        "EDGE": (176, 166, 144), "MAP_FILL": (226, 220, 206), "RING": (255, 248, 220),
        "ACCENT": (16, 88, 168), "ON_ACCENT": (255, 255, 255), "TEXT": (26, 30, 38),
        "TEXT_DIM": (84, 88, 98), "BUTTON_FILL": (236, 230, 216), "BUTTON_PRESSED": (196, 212, 232),
        "BUTTON_DISABLED": (240, 236, 226), "TOGGLE_ON": (16, 88, 168),
        "CONFIRM_FILL": (250, 206, 200), "DOT_RIM": (60, 60, 60), "AIRCRAFT_LOW": (255, 232, 40),
        "AIRCRAFT_MID": (100, 206, 236), "AIRCRAFT_HIGH": (196, 96, 236),
        "FAVORITE": (255, 159, 128), "EMERGENCY": (255, 58, 30), "AIRCRAFT_GROUND": (130, 130, 130),
        "FAVORITE_AIRPORT": (255, 205, 60),
    },
    # Black and white, yellow accent, the most saturated traffic colors.
    "contrast": {
        "BACKGROUND": (0, 0, 0), "BAR_FILL": (0, 0, 0), "PANEL_FILL": (0, 0, 0),
        "EDGE": (255, 255, 255), "MAP_FILL": (0, 0, 0), "RING": (255, 255, 255),
        "ACCENT": (255, 255, 0), "ON_ACCENT": (0, 0, 0), "TEXT": (255, 255, 255),
        "TEXT_DIM": (190, 190, 190), "BUTTON_FILL": (0, 0, 0), "BUTTON_PRESSED": (70, 70, 70),
        "BUTTON_DISABLED": (0, 0, 0), "TOGGLE_ON": (255, 255, 0), "CONFIRM_FILL": (190, 0, 0),
        "DOT_RIM": (0, 0, 0), "AIRCRAFT_LOW": (255, 249, 31), "AIRCRAFT_MID": (0, 232, 255),
        "AIRCRAFT_HIGH": (79, 124, 255), "FAVORITE": (249, 131, 178), "EMERGENCY": (255, 64, 48),
        "AIRCRAFT_GROUND": (150, 150, 150), "FAVORITE_AIRPORT": (255, 205, 60),
    },
    # Deep indigo and coral; lemon, mint and blue traffic, orchid favorites.
    "midnight": {
        "BACKGROUND": (15, 13, 25), "BAR_FILL": (22, 19, 36), "PANEL_FILL": (30, 26, 48),
        "EDGE": (82, 72, 116), "MAP_FILL": (16, 14, 24), "RING": (214, 204, 255),
        "ACCENT": (255, 128, 102), "ON_ACCENT": (24, 14, 18), "TEXT": (244, 240, 250),
        "TEXT_DIM": (178, 170, 200), "BUTTON_FILL": (42, 36, 66), "BUTTON_PRESSED": (110, 60, 70),
        "BUTTON_DISABLED": (26, 23, 40), "TOGGLE_ON": (240, 116, 92),
        "CONFIRM_FILL": (150, 40, 52), "DOT_RIM": (22, 19, 36), "AIRCRAFT_LOW": (250, 246, 90),
        "AIRCRAFT_MID": (28, 224, 178), "AIRCRAFT_HIGH": (74, 142, 240),
        "FAVORITE": (204, 97, 158), "EMERGENCY": (255, 56, 40), "AIRCRAFT_GROUND": (130, 130, 140),
        "FAVORITE_AIRPORT": (255, 205, 60),
    },
    # Warm and dim, low glare in a dark room.
    "ember": {
        "BACKGROUND": (18, 13, 10), "BAR_FILL": (26, 18, 14), "PANEL_FILL": (34, 24, 19),
        "EDGE": (90, 66, 52), "MAP_FILL": (20, 14, 11), "RING": (255, 214, 170),
        "ACCENT": (255, 140, 64), "ON_ACCENT": (26, 16, 10), "TEXT": (246, 234, 222),
        "TEXT_DIM": (186, 166, 150), "BUTTON_FILL": (52, 38, 30), "BUTTON_PRESSED": (112, 72, 42),
        "BUTTON_DISABLED": (30, 22, 17), "TOGGLE_ON": (228, 122, 52),
        "CONFIRM_FILL": (150, 44, 40), "DOT_RIM": (26, 18, 14), "AIRCRAFT_LOW": (255, 236, 128),
        "AIRCRAFT_MID": (40, 214, 255), "AIRCRAFT_HIGH": (128, 128, 255),
        "FAVORITE": (249, 137, 167), "EMERGENCY": (255, 64, 48), "AIRCRAFT_GROUND": (130, 130, 130),
        "FAVORITE_AIRPORT": (255, 205, 60),
    },
}  # fmt: skip


# The web page's CSS variables, from the theme roles.
CSS_VARS = {
    "bg": "BACKGROUND", "bar": "BAR_FILL", "panel": "PANEL_FILL", "edge": "EDGE",
    "accent": "ACCENT", "on": "TOGGLE_ON", "on-text": "ON_ACCENT", "text": "TEXT",
    "dim": "TEXT_DIM", "bad": "EMERGENCY", "warn": "CONFIRM_FILL", "button": "BUTTON_FILL",
    "pressed": "BUTTON_PRESSED", "off": "BUTTON_DISABLED", "gold": "FAVORITE_AIRPORT",
    "fav": "FAVORITE",
}  # fmt: skip


def _hex(color: Color) -> str:
    return "#" + "".join(f"{c:02x}" for c in color)


def css_colors(name: str) -> dict[str, str]:
    """A theme's colors as the web page's CSS variables: {"bg": "#101113", ...}, and
    "scheme", light or dark."""
    colors = THEMES.get(name, THEMES["dark"])
    found = {var: _hex(colors[role]) for var, role in CSS_VARS.items()}
    found["scheme"] = "light" if name in LIGHT_THEMES else "dark"
    return found


def css(name: str) -> str:
    """The same as a CSS rule for :root."""
    colors = css_colors(name)
    scheme = colors.pop("scheme")
    body = "; ".join(f"--{var}: {value}" for var, value in colors.items())
    return f":root {{ color-scheme: {scheme}; {body}; }}"
