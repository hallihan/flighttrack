"""Great-circle geometry in aviation units (nautical miles, degrees true)."""

from math import asin, atan2, cos, degrees, radians, sin, sqrt

EARTH_RADIUS_NM = 3440.065


def distance_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two points."""
    p1, p2 = radians(lat1), radians(lat2)
    dp = p2 - p1
    dl = radians(lon2 - lon1)
    a = sin(dp / 2) ** 2 + cos(p1) * cos(p2) * sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_NM * asin(min(1.0, sqrt(a)))


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Initial bearing from point 1 to point 2, in degrees true (0-360)."""
    p1, p2 = radians(lat1), radians(lat2)
    dl = radians(lon2 - lon1)
    y = sin(dl) * cos(p2)
    x = cos(p1) * sin(p2) - sin(p1) * cos(p2) * cos(dl)
    return degrees(atan2(y, x)) % 360.0


def destination(lat: float, lon: float, bearing: float, dist_nm: float) -> tuple[float, float]:
    """Point reached by traveling dist_nm from (lat, lon) on the given bearing."""
    p1, l1 = radians(lat), radians(lon)
    b = radians(bearing)
    d = dist_nm / EARTH_RADIUS_NM
    p2 = asin(sin(p1) * cos(d) + cos(p1) * sin(d) * cos(b))
    l2 = l1 + atan2(sin(b) * sin(d) * cos(p1), cos(d) - sin(p1) * sin(p2))
    return degrees(p2), (degrees(l2) + 540.0) % 360.0 - 180.0


def offset_nm(home_lat: float, home_lon: float, lat: float, lon: float) -> tuple[float, float]:
    """(east, north) offset of a point from home, for plotting on the scope."""
    d = distance_nm(home_lat, home_lon, lat, lon)
    b = radians(bearing_deg(home_lat, home_lon, lat, lon))
    return d * sin(b), d * cos(b)
