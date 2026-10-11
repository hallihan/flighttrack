"""Colors, sizes and fonts."""

from dataclasses import dataclass
from functools import lru_cache

import pygame

from flighttrack.favorites import is_favorite
from flighttrack.feeds.base import Aircraft
from flighttrack.themes import LIGHT_THEMES, THEMES

Color = tuple[int, int, int]

# ---- themes ---------------------------------------------------------------------------------
# A theme sets the colors of the chrome (bars, panels, buttons, text) and of the traffic. What
# describes the ground on the map (airports, runways, airspace, text over the map) is the same
# in every theme: it is drawn over the map, not over the chrome.
#
# Traffic colors are chosen to stay apart under deuteranopia, protanopia and tritanopia as
# well as normal vision (tests/test_themes.py holds every theme to that), and to step down in
# lightness from low to high altitude, which reads the same for everyone. All chrome text is
# at least 4.5:1 against its panel (WCAG AA); traffic and status colors used as text on the
# chrome are adjusted to that by legible() below.
NAME = "dark"  # the theme in force

# Set by apply() from the theme in force; declared here with the default's values.
BACKGROUND: Color = THEMES["dark"]["BACKGROUND"]
BAR_FILL: Color = THEMES["dark"]["BAR_FILL"]
PANEL_FILL: Color = THEMES["dark"]["PANEL_FILL"]
EDGE: Color = THEMES["dark"]["EDGE"]
MAP_FILL: Color = THEMES["dark"]["MAP_FILL"]
RING: Color = THEMES["dark"]["RING"]  # range rings: a color nothing else on the map uses
ACCENT: Color = THEMES["dark"]["ACCENT"]
ON_ACCENT: Color = THEMES["dark"]["ON_ACCENT"]  # text on ACCENT and TOGGLE_ON
TEXT: Color = THEMES["dark"]["TEXT"]
TEXT_DIM: Color = THEMES["dark"]["TEXT_DIM"]
BUTTON_FILL: Color = THEMES["dark"]["BUTTON_FILL"]
BUTTON_PRESSED: Color = THEMES["dark"]["BUTTON_PRESSED"]
BUTTON_DISABLED: Color = THEMES["dark"]["BUTTON_DISABLED"]
TOGGLE_ON: Color = THEMES["dark"]["TOGGLE_ON"]
CONFIRM_FILL: Color = THEMES["dark"]["CONFIRM_FILL"]  # a button that will do something drastic
DOT_RIM: Color = THEMES["dark"]["DOT_RIM"]  # round a legend dot on a lit chip
AIRCRAFT_LOW: Color = THEMES["dark"]["AIRCRAFT_LOW"]  # below 10,000 ft
AIRCRAFT_MID: Color = THEMES["dark"]["AIRCRAFT_MID"]  # 10,000 ft up to 30,000 ft
AIRCRAFT_HIGH: Color = THEMES["dark"]["AIRCRAFT_HIGH"]  # 30,000 ft and above
FAVORITE: Color = THEMES["dark"]["FAVORITE"]  # used for nothing else an aircraft can be
EMERGENCY: Color = THEMES["dark"]["EMERGENCY"]
AIRCRAFT_GROUND: Color = THEMES["dark"]["AIRCRAFT_GROUND"]
FAVORITE_AIRPORT: Color = THEMES["dark"]["FAVORITE_AIRPORT"]

# The same in every theme.
MAP_TEXT: Color = (236, 240, 242)  # text over the map, with its dark edge
MAP_TEXT_DIM: Color = (178, 186, 194)
HOME: Color = (255, 255, 255)
AIRCRAFT_SELECTED: Color = (255, 255, 255)
AIRCRAFT_STALE: Color = (125, 130, 138)  # all traffic, once the data has gone old
BANNER_TEXT: Color = (12, 14, 18)  # on a colored banner: stale, emergency, the follow pill
LOW_ALTITUDE_FT = 10_000
HIGH_ALTITUDE_FT = 30_000

RUNWAY: Color = (236, 236, 236)
AIRPORT: Color = (205, 215, 225)
FLIGHT_CATEGORY: dict[str, Color] = {
    # The colors pilots know these weather categories by.
    "VFR": (60, 200, 90),
    "MVFR": (70, 130, 255),
    "IFR": (235, 60, 50),
    "LIFR": (225, 80, 225),
}
# Airspace boundaries, by class: color with opacity, line width, and whether dashed. Blue
# for B and D and magenta for C is the convention of aeronautical charts, as is the dashed
# line for D. They are deliberately dull and dark next to the aircraft: the lines must read
# as ground furniture, never as traffic.
AIRSPACE: dict[str, tuple[tuple[int, int, int, int], int, bool]] = {
    "B": ((64, 88, 150, 215), 2, False),
    "C": ((110, 72, 112, 215), 2, False),
    "D": ((64, 88, 150, 215), 1, True),
}
AIRSPACE_LABEL: Color = (176, 184, 198)  # their ceiling/floor figures: neutral, not blue

STATUS_OK: Color = (80, 220, 110)
STATUS_STALE: Color = (255, 190, 60)
STATUS_DOWN: Color = (255, 70, 60)


def apply(name: str) -> None:
    """Put a theme in force (an unknown name gets the default). Main thread only."""
    global NAME, LEGEND, _COLORED
    name = name if name in THEMES else "dark"
    NAME = name
    globals().update(THEMES[name])
    LEGEND = _legend()
    _COLORED = _colored()
    _legible.clear()


def is_light() -> bool:
    return NAME in LIGHT_THEMES


def _channel(c: float) -> float:
    c /= 255
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def luminance(color: Color) -> float:
    r, g, b = (_channel(v) for v in color[:3])
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: Color, b: Color) -> float:
    """The WCAG contrast ratio of two colors, 1 to 21."""
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


_legible: dict[Color, Color] = {}


def legible(color: Color) -> Color:
    """color as text on the chrome: itself if it is at least 4.5:1 against the panels, else
    moved toward the text color (darker on a light theme, lighter on a dark one) until it
    is. So a yellow callsign stays yellowish, and readable, on chart paper."""
    found = _legible.get(color)
    if found is None:
        found, step = color, 0
        while contrast(found, PANEL_FILL) < 4.5 and step < 20:
            step += 1
            mix = step / 20
            found = tuple(round(c + (t - c) * mix) for c, t in zip(color, TEXT, strict=True))
        _legible[color] = found
    return found


# Smallest comfortable touch target on the 7" panel.
MIN_TOUCH_PX = 48


def altitude_color(altitude_ft: int | None) -> Color:
    if altitude_ft is None or altitude_ft < LOW_ALTITUDE_FT:
        return AIRCRAFT_LOW
    if altitude_ft < HIGH_ALTITUDE_FT:
        return AIRCRAFT_MID
    return AIRCRAFT_HIGH


def aircraft_color(aircraft: Aircraft, favorites: frozenset[str] = frozenset()) -> Color:
    """Emergency beats favorite beats everything else."""
    if aircraft.emergency:
        return EMERGENCY
    if is_favorite(aircraft, favorites):
        return FAVORITE
    if aircraft.on_ground:
        return AIRCRAFT_GROUND
    return altitude_color(aircraft.altitude_ft)


# The groups of traffic the pills in the top bar explain and can hide: a key (what is saved
# in settings), the color and the label.
LOW, MID, HIGH, FAV = "low", "mid", "high", "fav"


def _legend() -> tuple[tuple[str, Color, str], ...]:
    return (
        (LOW, AIRCRAFT_LOW, "<10k"),
        (MID, AIRCRAFT_MID, "10-30k"),
        (HIGH, AIRCRAFT_HIGH, ">30k"),
        (FAV, FAVORITE, "FAV"),
    )


LEGEND: tuple[tuple[str, Color, str], ...] = _legend()


def traffic_group(aircraft: Aircraft, favorite: bool) -> str | None:
    """Which legend group an aircraft belongs to: the one whose color it is drawn in. None
    for what has no pill and so can never be hidden: an emergency, and ground traffic."""
    if aircraft.emergency:
        return None
    if favorite:
        return FAV
    if aircraft.on_ground:
        return None
    altitude = aircraft.altitude_ft
    if altitude is None or altitude < LOW_ALTITUDE_FT:
        return LOW
    return MID if altitude < HIGH_ALTITUDE_FT else HIGH


@dataclass(frozen=True)
class Fonts:
    small: pygame.font.Font
    body: pygame.font.Font
    title: pygame.font.Font
    large: pygame.font.Font

    @classmethod
    def load(cls) -> "Fonts":
        return cls(
            small=pygame.font.Font(None, 18),
            body=pygame.font.Font(None, 22),
            title=pygame.font.Font(None, 26),
            large=pygame.font.Font(None, 36),
        )


SHADOW: Color = (0, 0, 0)


@lru_cache(maxsize=2048)
def shadowed_text(font: pygame.font.Font, string: str, color: Color) -> pygame.Surface:
    """Text with a dark edge, for drawing over the map where the background varies."""
    front = font.render(string, True, color)
    back = font.render(string, True, SHADOW)
    width, height = front.get_size()
    canvas = pygame.Surface((width + 2, height + 2), pygame.SRCALPHA)
    for offset in ((0, 1), (2, 1), (1, 0), (1, 2), (2, 2)):
        canvas.blit(back, offset)
    canvas.blit(front, (1, 1))
    return canvas


def text(font: pygame.font.Font, string: str, color: Color) -> pygame.Surface:
    """Rendered text on the chrome, cached: most labels are identical from one frame to the
    next. In a traffic or status color it is made legible() against the panels."""
    if color in _COLORED:
        color = legible(color)
    return _text(font, string, color)


@lru_cache(maxsize=2048)
def _text(font: pygame.font.Font, string: str, color: Color) -> pygame.Surface:
    return font.render(string, True, color)


def _colored() -> frozenset[Color]:
    """The colors that carry meaning (traffic, status), which text may be drawn in."""
    return frozenset(
        (AIRCRAFT_LOW, AIRCRAFT_MID, AIRCRAFT_HIGH, FAVORITE, EMERGENCY, AIRCRAFT_GROUND,
         AIRCRAFT_STALE, FAVORITE_AIRPORT, AIRPORT, STATUS_OK, STATUS_STALE, STATUS_DOWN)
    )  # fmt: skip


_COLORED = _colored()


@lru_cache(maxsize=512)
def faded_text(
    font: pygame.font.Font, string: str, color: Color, strength: float
) -> pygame.Surface:
    """Shadowed text at part strength (0-1), for something on its way out. The fading is
    baked in: set_alpha on a surface with its own alpha is slow."""
    faded = shadowed_text(font, string, color).copy()
    alpha = round(255 * min(max(strength, 0.0), 1.0))
    faded.fill((255, 255, 255, alpha), special_flags=pygame.BLEND_RGBA_MULT)
    return faded
