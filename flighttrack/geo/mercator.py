"""Web Mercator math: the projection map tiles use, so aircraft must use it too.

Zoom may be fractional. An integer zoom is a tile pyramid level; the view's own zoom is
whatever makes the selected range fit the outer ring, and tiles from the nearest level are
scaled to match.
"""

from collections.abc import Callable
from math import atan, cos, degrees, floor, log, log2, pi, radians, sin, sinh

TILE_PX = 256
METERS_PER_NM = 1852.0
# Ground meters per pixel at the equator at zoom 0.
EQUATOR_M_PER_PX = 156543.03392804097
MAX_LATITUDE = 85.05112878


def world_px(lat: float, lon: float, zoom: float) -> tuple[float, float]:
    """Position in the world pixel grid at this zoom: (0, 0) is the top-left of the map."""
    scale = TILE_PX * 2.0**zoom
    s = sin(radians(min(max(lat, -MAX_LATITUDE), MAX_LATITUDE)))
    return (lon + 180.0) / 360.0 * scale, (0.5 - log((1 + s) / (1 - s)) / (4 * pi)) * scale


def lat_lon(x: float, y: float, zoom: float) -> tuple[float, float]:
    """Inverse of world_px."""
    scale = TILE_PX * 2.0**zoom
    lon = (x / scale * 360.0) % 360.0 - 180.0
    return degrees(atan(sinh(pi * (1 - 2 * y / scale)))), lon


def shifted(center: tuple[float, float], dx: float, dy: float, zoom: float) -> tuple[float, float]:
    """The point dx pixels right and dy pixels down from center, at this zoom."""
    x, y = world_px(*center, zoom)
    return lat_lon(x + dx, y + dy, zoom)


def zoom_for_range(lat: float, range_nm: float, ring_radius_px: float) -> float:
    """The zoom at which range_nm on the ground spans ring_radius_px at this latitude."""
    meters_per_px = range_nm * METERS_PER_NM / ring_radius_px
    return log2(EQUATOR_M_PER_PX * cos(radians(lat)) / meters_per_px)


def offset_px(
    home: tuple[float, float], lat: float, lon: float, zoom: float
) -> tuple[float, float]:
    """Pixels right and down from home to a point, at this zoom."""
    hx, hy = world_px(*home, zoom)
    x, y = world_px(lat, lon, zoom)
    width = TILE_PX * 2.0**zoom
    dx = (x - hx + width / 2) % width - width / 2  # shortest way round the antimeridian
    return dx, y - hy


def tiles_covering(
    home: tuple[float, float], zoom: float, tile_zoom: int, width_px: int, height_px: int
) -> tuple[float, tuple[float, float], range, range]:
    """What a layer at tile_zoom needs to fill a view of this size centered on home.

    Returns (scale, center, columns, rows): tiles are drawn scale times their native size;
    center is home in the tile level's pixel grid; columns may run past the edge of the world
    (wrap them with x % 2**tile_zoom), rows are clamped to it.
    """
    scale = 2.0 ** (zoom - tile_zoom)
    cx, cy = world_px(*home, tile_zoom)
    half_w, half_h = width_px / 2 / scale, height_px / 2 / scale
    count = 2**tile_zoom
    columns = range(floor((cx - half_w) / TILE_PX), floor((cx + half_w) / TILE_PX) + 1)
    first_row = max(floor((cy - half_h) / TILE_PX), 0)
    last_row = min(floor((cy + half_h) / TILE_PX), count - 1)
    return scale, (cx, cy), columns, range(first_row, last_row + 1)


def projector(
    center: tuple[float, float], zoom: float, screen_x: float, screen_y: float
) -> Callable[[float, float], tuple[int, int]]:
    """A function from (lat, lon) to screen pixels for a view centered on center, with that
    center at (screen_x, screen_y). Everything that depends only on the view is worked out
    here, once, which matters when it is called for hundreds of aircraft a frame."""
    scale = TILE_PX * 2.0**zoom
    half = scale / 2
    home_x, home_y = world_px(*center, zoom)
    y0 = screen_y - home_y
    lon_scale = scale / 360.0
    lat_scale = scale / (4 * pi)

    def to_screen(lat: float, lon: float) -> tuple[int, int]:
        s = sin(radians(min(max(lat, -MAX_LATITUDE), MAX_LATITUDE)))
        x = (lon + 180.0) * lon_scale
        dx = (x - home_x + half) % scale - half  # shortest way round the antimeridian
        return round(screen_x + dx), round(y0 + half - log((1 + s) / (1 - s)) * lat_scale)

    return to_screen
