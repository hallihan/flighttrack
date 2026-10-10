"""Deciding what to ask a public feed for, given the range on screen.

The public APIs answer "aircraft within N nautical miles of a point" with N capped at 250, and
wide areas return a lot of data (about 120 KB compressed for 250 NM over a busy region). So the
plan trades refresh rate against area: every few seconds for the usual ranges, slower as the
area grows.
"""

from dataclasses import dataclass

MAX_QUERY_NM = 250.0


@dataclass(frozen=True, slots=True)
class QueryPlan:
    lat: float
    lon: float
    radius_nm: float
    interval_s: float


def plan_query(home: tuple[float, float], range_nm: float, corner_factor: float) -> QueryPlan:
    """corner_factor is how far the view's corners are from home, as a multiple of the range.

    Asks for enough to fill the view's corners where the API allows it. At the widest range
    only the range ring itself is covered and the corners stay empty.
    """
    lat, lon = home
    radius = min(range_nm * corner_factor, MAX_QUERY_NM)
    if radius <= 100:
        interval = 5.0
    elif radius < MAX_QUERY_NM:
        interval = 10.0
    else:
        interval = 15.0
    return QueryPlan(lat, lon, radius, interval)
