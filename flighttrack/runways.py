"""Runways near a point, from the bundled OurAirports table, for drawing over the map."""

import csv
import gzip
import io
from collections import defaultdict
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from math import cos, floor, radians

from flighttrack.geo import distance_nm

DEFAULT_WIDTH_FT = 75.0


@dataclass(frozen=True, slots=True)
class Runway:
    airport: str
    le_ident: str  # designator at the low-numbered end, e.g. "09L"
    le: tuple[float, float]
    he_ident: str
    he: tuple[float, float]
    width_ft: float
    length_ft: int | None = None


@lru_cache(maxsize=1)
def _grid() -> dict[tuple[int, int], list[Runway]]:
    """Runways bucketed by the whole-degree cell of their low end. Built on first use."""
    raw = resources.files("flighttrack").joinpath("data/runways.csv.gz").read_bytes()
    grid: dict[tuple[int, int], list[Runway]] = defaultdict(list)
    with io.TextIOWrapper(gzip.GzipFile(fileobj=io.BytesIO(raw)), encoding="utf-8") as text:
        for row in csv.DictReader(text):
            le = (float(row["le_lat"]), float(row["le_lon"]))
            runway = Runway(
                row["airport"],
                row["le_ident"],
                le,
                row["he_ident"],
                (float(row["he_lat"]), float(row["he_lon"])),
                float(row["width_ft"]) if row["width_ft"] else DEFAULT_WIDTH_FT,
                int(float(row["length_ft"])) if row.get("length_ft") else None,
            )
            grid[(floor(le[0]), floor(le[1]))].append(runway)
    return dict(grid)


@lru_cache(maxsize=1)
def _by_airport() -> dict[str, tuple[Runway, ...]]:
    found: dict[str, list[Runway]] = defaultdict(list)
    for cell in _grid().values():
        for runway in cell:
            found[runway.airport].append(runway)
    return {ident: tuple(runways) for ident, runways in found.items()}


def warm() -> None:
    """Build the tables now. Meant for a worker thread at start-up."""
    _by_airport()


def at(airport_ident: str) -> tuple[Runway, ...]:
    """The runways of one airport, longest first. Empty if we have none surveyed."""
    runways = _by_airport().get(airport_ident, ())
    return tuple(sorted(runways, key=lambda r: -(r.length_ft or 0)))


@lru_cache(maxsize=16)
def near(lat: float, lon: float, radius_nm: float) -> tuple[Runway, ...]:
    """Runways with an end within radius_nm of the point."""
    lat_span = radius_nm / 60.0
    lon_span = lat_span / max(cos(radians(lat)), 0.01)
    found = []
    for cell_lat in range(floor(lat - lat_span), floor(lat + lat_span) + 1):
        for cell_lon in range(floor(lon - lon_span), floor(lon + lon_span) + 1):
            wrapped = (cell_lon + 180) % 360 - 180
            for runway in _grid().get((cell_lat, wrapped), ()):
                if (
                    distance_nm(lat, lon, *runway.le) <= radius_nm
                    or distance_nm(lat, lon, *runway.he) <= radius_nm
                ):
                    found.append(runway)
    return tuple(found)
