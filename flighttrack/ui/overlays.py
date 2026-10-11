"""Vector overlays painted into the composed map: runways and airspace boundaries.

They change only with the view, so they are drawn once into the cached background (see
basemap.py), not every frame.
"""

from collections.abc import Callable, Hashable
from dataclasses import dataclass, field
from functools import lru_cache
from math import cos, hypot, radians

import pygame

from flighttrack.airports import LARGE, MEDIUM, Airport
from flighttrack.airspace import Airspace
from flighttrack.geo.mercator import EQUATOR_M_PER_PX, offset_px, shifted
from flighttrack.runways import Runway
from flighttrack.ui import theme

METERS_PER_FOOT = 0.3048
# Airspace labels (ceiling/floor) are only drawn at this range or closer.
AIRSPACE_LABEL_RANGE_NM = 25

Painter = Callable[[pygame.Surface, tuple[float, float], float], None]


@dataclass(frozen=True)
class Overlay:
    key: Hashable  # changes whenever the overlay would look different
    paint: Painter = field(compare=False)  # (surface, view center, zoom)


def _xy(
    surface: pygame.Surface, center: tuple[float, float], lat: float, lon: float, zoom: float
) -> tuple[float, float]:
    dx, dy = offset_px(center, lat, lon, zoom)
    return surface.get_width() / 2 + dx, surface.get_height() / 2 + dy


def runway_painter(
    runways: tuple[Runway, ...], fonts: theme.Fonts, opacity: float = 1.0
) -> Painter:
    """Runway outlines at true width, with designators at each end."""

    def paint(target: pygame.Surface, center: tuple[float, float], zoom: float) -> None:
        # Drawn on a layer of their own so the whole lot can be faded together.
        surface = pygame.Surface(target.get_size(), pygame.SRCALPHA)
        _paint_runways(surface, center, zoom)
        surface.set_alpha(round(255 * min(max(opacity, 0.0), 1.0)))
        target.blit(surface, (0, 0))

    def _paint_runways(surface: pygame.Surface, center: tuple[float, float], zoom: float) -> None:
        px_per_m = 2.0**zoom / (EQUATOR_M_PER_PX * cos(radians(center[0])))
        area = surface.get_rect().inflate(200, 200)
        for runway in runways:
            ax, ay = _xy(surface, center, *runway.le, zoom)
            bx, by = _xy(surface, center, *runway.he, zoom)
            length = hypot(bx - ax, by - ay)
            if length < 4 or not area.clipline((ax, ay), (bx, by)):
                continue
            ux, uy = (bx - ax) / length, (by - ay) / length  # unit vector along the runway
            half = max(runway.width_ft * METERS_PER_FOOT * px_per_m / 2, 2.0)
            nx, ny = -uy * half, ux * half
            outline = [
                (ax + nx, ay + ny),
                (bx + nx, by + ny),
                (bx - nx, by - ny),
                (ax - nx, ay - ny),
            ]
            pygame.draw.polygon(surface, theme.RUNWAY, outline, width=1)
            for ident, x, y, sign in ((runway.le_ident, ax, ay, -1), (runway.he_ident, bx, by, 1)):
                if ident:
                    text = theme.shadowed_text(fonts.small, ident, theme.RUNWAY)
                    surface.blit(
                        text, text.get_rect(center=(x + sign * ux * 16, y + sign * uy * 16))
                    )

    return paint


def _dashes(
    surface: pygame.Surface,
    color: tuple[int, int, int, int],
    points: list[tuple[float, float]],
    width: int,
    dash: float = 9.0,
    gap: float = 6.0,
) -> None:
    """A dashed line through points. The pattern carries on round corners, so a curve made
    of short segments still shows evenly spaced dashes."""
    period = dash + gap
    along = 0.0  # how far into the dash-gap pattern the line has got
    for (x0, y0), (x1, y1) in zip(points, points[1:], strict=False):
        length = hypot(x1 - x0, y1 - y0)
        if length == 0:
            continue
        ux, uy = (x1 - x0) / length, (y1 - y0) / length
        done = 0.0
        while done < length:
            phase = along % period
            if phase < dash:
                step = min(dash - phase, length - done)
                start = (x0 + ux * done, y0 + uy * done)
                end = (x0 + ux * (done + step), y0 + uy * (done + step))
                pygame.draw.line(surface, color, start, end, width)
            else:
                step = min(period - phase, length - done)
            done += step
            along += step


def _altitude_label(airspace: Airspace) -> str | None:
    """Ceiling over floor in hundreds of feet, as on a chart: "100/25", or "41/SFC"."""
    if airspace.upper_ft is None or airspace.lower_ft is None:
        return None
    floor_text = "SFC" if airspace.lower_ft == 0 else f"{round(airspace.lower_ft / 100)}"
    return f"{round(airspace.upper_ft / 100)}/{floor_text}"


def airspace_painter(airspaces: tuple[Airspace, ...], fonts: theme.Fonts, labels: bool) -> Painter:
    """Semi-transparent class B, C and D boundaries."""

    def paint(surface: pygame.Surface, center: tuple[float, float], zoom: float) -> None:
        width, height = surface.get_size()
        north, west = shifted(center, -width / 2, -height / 2, zoom)
        south, east = shifted(center, width / 2, height / 2, zoom)
        layer = pygame.Surface((width, height), pygame.SRCALPHA)
        to_label = []
        for airspace in airspaces:
            a_south, a_west, a_north, a_east = airspace.bounds
            if a_north < south or a_south > north or a_east < west or a_west > east:
                continue
            color, line_width, dashed = theme.AIRSPACE[airspace.cls]
            for ring in airspace.rings:
                points = [_xy(surface, center, lat, lon, zoom) for lat, lon in ring]
                if dashed:
                    _dashes(layer, color, [*points, points[0]], line_width)
                else:
                    pygame.draw.lines(layer, color, True, points, width=line_width)
            if labels:
                # At the northernmost point of the boundary, just inside it.
                lat, lon = max(airspace.rings[0])
                to_label.append((airspace, _xy(surface, center, lat, lon, zoom)))
        surface.blit(layer, (0, 0))
        for airspace, (x, y) in to_label:
            text = _altitude_label(airspace)
            if text and 0 <= x < width and -10 <= y < height:
                rendered = theme.shadowed_text(fonts.small, text, theme.AIRSPACE_LABEL)
                surface.blit(rendered, rendered.get_rect(midtop=(x, y + 3)))

    return paint


@lru_cache(maxsize=16)
def home_marker(
    font: pygame.font.Font, label: str, color: theme.Color, opacity: float
) -> tuple[pygame.Surface, tuple[int, int]]:
    """The house that marks home, with its label underneath, at an opacity: it describes the
    ground, so it fades with the other map features. Returns the picture and where in it the
    home point is."""
    words = theme.shadowed_text(font, label, color)
    width = max(words.get_width(), 24)
    picture = pygame.Surface((width, 26 + words.get_height()), pygame.SRCALPHA)
    x, y = width // 2, 12
    house = [
        (x, y - 9),
        (x + 8, y - 1),
        (x + 5, y - 1),
        (x + 5, y + 7),
        (x - 5, y + 7),
        (x - 5, y - 1),
        (x - 8, y - 1),
    ]
    pygame.draw.polygon(picture, (6, 10, 16), house, width=4)
    pygame.draw.polygon(picture, color, house)
    picture.blit(words, words.get_rect(midtop=(x, y + 10)))
    # Baked in, not set_alpha: that is slow on a surface with its own alpha.
    alpha = round(255 * min(max(opacity, 0.0), 1.0))
    picture.fill((255, 255, 255, alpha), special_flags=pygame.BLEND_RGBA_MULT)
    return picture, (x, y)


def airport_painter(
    airports: tuple[Airport, ...],
    fonts: theme.Fonts,
    favorites: frozenset[str],
    home: str | None,
    selected: str | None,
    home_opacity: float = 1.0,
) -> Painter:
    """A marker and ident for each airport: a ring sized by how big the airport is, filled
    for favorites, a house for home (at home_opacity), and a highlight round the selected
    one."""

    def paint(surface: pygame.Surface, center: tuple[float, float], zoom: float) -> None:
        area = surface.get_rect()
        taken: list[pygame.Rect] = []
        for airport in airports:
            x, y = _xy(surface, center, airport.lat, airport.lon, zoom)
            if not area.collidepoint(x, y):
                continue
            x, y = round(x), round(y)
            favorite = airport.ident in favorites
            color = theme.FAVORITE_AIRPORT if favorite else theme.AIRPORT
            radius = {LARGE: 6, MEDIUM: 5}.get(airport.size, 4)
            if airport.ident == home:
                picture, (hx, hy) = home_marker(fonts.small, airport.ident, color, home_opacity)
                surface.blit(picture, (x - hx, y - hy))
                if airport.ident == selected:
                    pygame.draw.circle(surface, theme.AIRCRAFT_SELECTED, (x, y), 16, width=2)
                taken.append(picture.get_rect(topleft=(x - hx, y - hy)))
                continue
            else:
                pygame.draw.circle(surface, (6, 10, 16), (x, y), radius + 2)
                pygame.draw.circle(surface, color, (x, y), radius, width=0 if favorite else 2)
            if airport.ident == selected:
                pygame.draw.circle(surface, theme.AIRCRAFT_SELECTED, (x, y), 16, width=2)
            label = theme.shadowed_text(fonts.small, airport.ident, color)
            box = label.get_rect(midtop=(x, y + radius + 4))
            # Bigger airports are painted first, so they keep their labels when space is short.
            if airport.ident == selected or favorite or box.collidelist(taken) == -1:
                taken.append(box)
                surface.blit(label, box)

    return paint


# How low the feeds hear aircraft (reach.py), shown while diagnostics are open: (below this
# many feet above the ground, or None for the ground itself; color; label). Above the last,
# the last color.
REACH_BANDS: tuple[tuple[int | None, theme.Color, str], ...] = (
    (None, (40, 170, 80), "Gnd"),
    (500, (130, 200, 60), "500"),
    (1000, (230, 210, 60), "1k"),
    (2000, (240, 150, 50), "2k"),
    (4000, (225, 80, 60), "4k"),
    (99999, (170, 60, 170), "4k+"),
)
REACH_ALPHA = 105


def reach_color(floor: int) -> theme.Color:
    from flighttrack.reach import GROUND

    if floor == GROUND:
        return REACH_BANDS[0][1]
    for top, color, _ in REACH_BANDS[1:]:
        if floor < top:
            return color
    return REACH_BANDS[-1][1]


def reach_painter(floors: list) -> Painter:
    """A tint over each cell the feeds' floor is known for, by how low above the ground
    they hear there (floors as Reach.floors gives them)."""
    from flighttrack.reach import cell_bounds

    def paint(target: pygame.Surface, center: tuple[float, float], zoom: float) -> None:
        layer = pygame.Surface(target.get_size(), pygame.SRCALPHA)
        area = layer.get_rect()
        for cell, floor in floors:
            south, west, north, east = cell_bounds(cell)
            x0, y0 = _xy(layer, center, north, west, zoom)
            x1, y1 = _xy(layer, center, south, east, zoom)
            left, top, right, bottom = round(x0), round(y0), round(x1), round(y1)
            rect = pygame.Rect(left, top, max(right - left, 1), max(bottom - top, 1))
            if rect.colliderect(area):
                layer.fill((*reach_color(floor), REACH_ALPHA), rect)
        target.blit(layer, (0, 0))

    return paint
