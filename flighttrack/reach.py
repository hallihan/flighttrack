"""How low the feeds hear aircraft, place by place: a map learned over time.

ADS-B receivers hear an aircraft only above some height that depends on where they are,
the terrain and buildings round them, and how far off it is. Near a receiver an airport's
taxiways may be heard; twenty miles away the same network may hear nothing below 2,000
feet. So a track that ends near an airport is a landing only if it ended down where
hearing ends there; one that ends well above that went quiet for another reason.

The map is a grid of cells CELL_DEG across. For each, and for each source separately (their
receivers differ, and a local receiver may be off), the lowest altitude at which each of
the last few distinct aircraft was heard, and whether any was heard on the ground. A
source's floor there is the second lowest of those (one odd report does not make it), once
MIN_AIRCRAFT have been; or the ground, once MIN_AIRCRAFT have been heard on it. Kept on
disk, so it gets better over weeks; what is older than MAX_AGE_DAYS is let go, as receivers
come and go.

Altitudes from the feeds are above sea level (barometric, so off by a few hundred feet with
the weather). To show the floor above the ground, each cell also gets the height of the
ground at its middle, from Open-Meteo's elevation API (Copernicus DEM, 90 m), asked a
hundred cells at a time and kept with the map.

Plain Python, no pygame: tested directly.
"""

import gzip
import json
import threading
import time
from collections.abc import Iterable
from pathlib import Path

import requests

from flighttrack import net
from flighttrack.feeds.base import Aircraft
from flighttrack.feeds.http import USER_AGENT

CELL_DEG = 0.05  # about 3 NM north to south
KEEP = 6  # the lowest this many aircraft are kept for each cell
MIN_AIRCRAFT = 3
MAX_AGE_DAYS = 60
GROUND = -1  # as an altitude: heard on the ground
FRESH_S = 15.0  # only reports this fresh are counted: not what the feed is still repeating
ELEVATION_URL = "https://api.open-meteo.com/v1/elevation"
ELEVATION_BATCH = 100  # cells a request: the most the API takes
FEET_PER_METER = 3.28084

Cell = tuple[int, int]


def cell_of(lat: float, lon: float) -> Cell:
    return int(lat // CELL_DEG), int(lon // CELL_DEG)


def cell_bounds(cell: Cell) -> tuple[float, float, float, float]:
    """South, west, north, east edges of a cell, in degrees."""
    south, west = cell[0] * CELL_DEG, cell[1] * CELL_DEG
    return south, west, south + CELL_DEG, west + CELL_DEG


def _lowest(values: Iterable[int | None]) -> int | None:
    known = [v for v in values if v is not None]
    return min(known) if known else None  # GROUND (-1) is the lowest of all


class Reach:
    """Kept separately for each source (adsb.fi, adsb.lol, ADS-B Exchange, the local
    receiver): their receivers are not the same, and come and go on their own. Whether an
    aircraft went down out of hearing is judged by the sources that were hearing then; the
    map shown is the lowest of them all."""

    def __init__(self, wall=time.time) -> None:
        self._wall = wall
        self._lock = threading.Lock()
        # cell -> source -> [[altitude or GROUND, hex, day], ...], lowest first, one each
        self._cells: dict[Cell, dict[str, list[list]]] = {}
        self._ground: dict[Cell, int] = {}  # cell -> height of the ground, feet
        self.version = 0  # goes up when any cell's floor (or its ground) changes

    def add(self, source: str, aircraft: Iterable[Aircraft], now: float) -> None:
        """Count one source's answer (now is the monotonic time of the answer)."""
        day = int(self._wall() // 86400)
        with self._lock:
            for a in aircraft:
                if now - a.seen_at > FRESH_S:
                    continue
                if a.on_ground:
                    height = GROUND
                elif a.altitude_ft is not None:
                    height = a.altitude_ft
                else:
                    continue
                entries = self._cells.setdefault(cell_of(a.lat, a.lon), {}).setdefault(source, [])
                before = _floor(entries)
                mine = next((e for e in entries if e[1] == a.hex), None)
                if mine is not None:
                    if height >= mine[0]:
                        mine[2] = day
                        continue
                    entries.remove(mine)
                elif len(entries) >= KEEP and height >= entries[-1][0]:
                    continue
                entries.append([height, a.hex, day])
                entries.sort(key=lambda e: e[0])
                del entries[KEEP:]
                if _floor(entries) != before:
                    self.version += 1

    def floor(self, lat: float, lon: float, sources: Iterable[str] | None = None) -> int | None:
        """How low aircraft are heard here, in feet above sea level (GROUND for the
        ground), by the lowest of the given sources (all, if none are given); None if too
        few have been heard by them to say."""
        with self._lock:
            by_source = self._cells.get(cell_of(lat, lon), {})
            wanted = by_source.keys() if sources is None else set(sources) & by_source.keys()
            return _lowest(_floor(by_source[s]) for s in wanted)

    def floors(self, south: float, west: float, north: float, east: float) -> list:
        """(cell, the lowest of the sources' floors above the ground in feet, or GROUND)
        for every cell in the box with a floor and the height of its ground known."""
        with self._lock:
            found = []
            for cell, by_source in self._cells.items():
                s, w, n, e = cell_bounds(cell)
                if n < south or s > north or e < west or w > east:
                    continue
                value = _lowest(_floor(entries) for entries in by_source.values())
                ground = self._ground.get(cell)
                if value is None or (value != GROUND and ground is None):
                    continue
                found.append((cell, GROUND if value == GROUND else max(value - ground, 0)))
            return found

    def counts(self) -> tuple[int, int, int]:
        """Cells with reports, with a floor, and with the height of their ground."""
        with self._lock:
            floors = sum(1 for c in self._cells.values() if _cell_floor(c) is not None)
            return len(self._cells), floors, len(self._ground.keys() & self._cells.keys())

    def source_counts(self) -> dict[str, int]:
        """For each source, the cells it has a floor for."""
        with self._lock:
            found: dict[str, int] = {}
            for by_source in self._cells.values():
                for source, entries in by_source.items():
                    if _floor(entries) is not None:
                        found[source] = found.get(source, 0) + 1
            return dict(sorted(found.items()))

    def missing_ground(self, limit: int = ELEVATION_BATCH) -> list[Cell]:
        """Cells with a floor whose ground height is not known yet: to be asked for."""
        with self._lock:
            return [
                c
                for c, by_source in self._cells.items()
                if c not in self._ground and _cell_floor(by_source) not in (None, GROUND)
            ][:limit]

    def set_ground(self, heights: dict[Cell, int]) -> None:
        with self._lock:
            self._ground.update(heights)
            self.version += 1

    def save(self, path: Path) -> None:
        with self._lock:
            data = {
                "cells": {f"{c[0]},{c[1]}": by_source for c, by_source in self._cells.items()},
                "ground": {f"{c[0]},{c[1]}": h for c, h in self._ground.items()},
            }
        path.write_bytes(gzip.compress(json.dumps(data, separators=(",", ":")).encode()))

    def load(self, path: Path) -> None:
        try:
            data = json.loads(gzip.decompress(path.read_bytes()))
        except (OSError, ValueError, EOFError):
            return
        if not isinstance(data, dict):
            return
        oldest = int(self._wall() // 86400) - MAX_AGE_DAYS
        cells: dict[Cell, dict[str, list[list]]] = {}
        ground: dict[Cell, int] = {}
        for key, height in (data.get("ground") or {}).items():
            try:
                ground[_cell_key(key)] = int(height)
            except (TypeError, ValueError):
                continue
        for key, by_source in (data.get("cells") or {}).items():
            if not isinstance(by_source, dict):
                continue
            try:
                cell = _cell_key(key)
                for source, entries in by_source.items():
                    kept = [[int(h), str(x), int(d)] for h, x, d in entries if int(d) >= oldest]
                    if kept:
                        cells.setdefault(cell, {})[str(source)] = sorted(kept)[:KEEP]
            except (TypeError, ValueError):
                continue
        with self._lock:
            self._ground.update(ground)
            for cell, by_source in cells.items():
                mine = self._cells.setdefault(cell, {})
                for source, entries in by_source.items():
                    merged = {e[1]: e for e in entries}
                    for e in mine.get(source, []):
                        if e[1] not in merged or e[0] < merged[e[1]][0]:
                            merged[e[1]] = e
                    mine[source] = sorted(merged.values(), key=lambda e: e[0])[:KEEP]
            self.version += 1


def _cell_key(key: str) -> Cell:
    lat, lon = (int(v) for v in key.split(","))
    return lat, lon


def _cell_floor(by_source: dict[str, list[list]]) -> int | None:
    return _lowest(_floor(entries) for entries in by_source.values())


def _floor(entries: list[list]) -> int | None:
    grounded = sum(1 for e in entries if e[0] == GROUND)
    if grounded >= MIN_AIRCRAFT:
        return GROUND
    if len(entries) < MIN_AIRCRAFT:
        return None
    heights = [e[0] for e in entries if e[0] != GROUND]
    if len(heights) < 2:
        return heights[0] if heights else None
    return heights[1]


# A track point this close to an airport and no higher than this above where hearing ends
# there is where an aircraft goes out of hearing to land, or comes into it after taking off.
NEAR_NM = 5.0
FLOOR_MARGIN_FT = 800
# Where nothing has been learned yet: this high above the field, at most.
FIELD_MARGIN_FT = 1500
# Where the ground itself is heard, a landing is heard too: only a point this low counts.
HEARD_TO_GROUND_FT = 300


def low_beside(
    reach: "Reach | None",
    lat: float,
    lon: float,
    altitude: int | None,
    sources: Iterable[str] | None = None,
):
    """The airport a track point is low beside, as an aircraft is just before it lands
    out of hearing or just after it takes off into it; None if it is not. sources are the
    sources hearing at the time: how low the others can hear does not matter."""
    from flighttrack import airports

    nearby = [a for a in airports.near(lat, lon, NEAR_NM, "LMS") if "-" not in a.ident]
    if not nearby:
        return None
    from flighttrack.geo import distance_nm

    airport = min(nearby, key=lambda a: distance_nm(a.lat, a.lon, lat, lon))
    if altitude is None:
        return airport  # on the ground there
    field = airport.elevation_ft or 0
    floor = reach.floor(lat, lon, sources) if reach is not None else None
    if floor is None:
        ceiling = field + FIELD_MARGIN_FT
    elif floor == GROUND:
        ceiling = field + HEARD_TO_GROUND_FT
    else:
        ceiling = max(floor, field) + FLOOR_MARGIN_FT
    return airport if altitude <= ceiling else None


def open_meteo_ground(
    cells: list[Cell], session: requests.Session | None = None
) -> dict[Cell, int]:
    """The height of the ground in the middle of each cell, in feet, from Open-Meteo's
    elevation API (up to ELEVATION_BATCH cells a request). Blocks; raises on failure."""
    session = session or requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    found: dict[Cell, int] = {}
    for start in range(0, len(cells), ELEVATION_BATCH):
        batch = cells[start : start + ELEVATION_BATCH]
        middles = [cell_bounds(c) for c in batch]
        params = {
            "latitude": ",".join(f"{(s + n) / 2:.4f}" for s, _, n, _ in middles),
            "longitude": ",".join(f"{(w + e) / 2:.4f}" for _, w, _, e in middles),
        }
        response = net.get(session, ELEVATION_URL, params=params, timeout=(4.0, 10.0))
        response.raise_for_status()
        heights = response.json().get("elevation")
        if not isinstance(heights, list) or len(heights) != len(batch):
            raise ValueError("unexpected answer from the elevation service")
        for cell, meters in zip(batch, heights, strict=True):
            if isinstance(meters, int | float):
                found[cell] = round(max(meters, 0) * FEET_PER_METER)
    return found
