"""Class B, C and D airspace boundaries, from the FAA's public airspace service.

United States only: there is no comparable keyless worldwide source. Elsewhere the lookups
simply come back empty.

Boundaries are fetched in 2-degree cells on a worker thread, simplified by the server to
about 30 m (enough at any range we draw them), and cached on disk for a few weeks: airspace
changes on the 56-day chart cycle.
"""

import json
import logging
import queue
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from math import cos, floor, radians
from pathlib import Path
from typing import Any

import requests

from flighttrack import faa, net
from flighttrack.feeds.http import USER_AGENT

log = logging.getLogger(__name__)

QUERY_URL = (
    "https://services6.arcgis.com/ssFJjBXIUyZDrSYZ/arcgis/rest/services"
    "/Class_Airspace/FeatureServer/0/query"
)
CELL_DEG = 2
CHECK_LOADED_EVERY_S = 3600.0  # how often cells already in memory are checked for a new edition


def out_of_date(fetched_at: float, now: float) -> bool:
    """Airspace is republished every 28 days, on the FAA's calendar: what we hold is out of
    date when an edition has taken effect since we fetched it, whatever its age."""
    return faa.out_of_date(fetched_at, now, faa.AIRAC_S)


RETRY_AFTER_S = 600.0
# The service refuses bursts of requests, so cells are fetched one at a time with a pause.
FETCH_SPACING_S = 2.0
CLASSES = ("B", "C", "D")

Cell = tuple[int, int]  # south-west corner, whole degrees
Ring = tuple[tuple[float, float], ...]  # (lat, lon) points


@dataclass(frozen=True, slots=True)
class Airspace:
    ident: int
    cls: str  # "B", "C" or "D"
    name: str
    lower_ft: int | None
    upper_ft: int | None
    rings: tuple[Ring, ...]
    bounds: tuple[float, float, float, float]  # south, west, north, east


def _altitude(value: Any) -> int | None:
    """Feet, or None for the service's "not applicable" markers (negative numbers)."""
    return int(value) if isinstance(value, int | float) and 0 <= value < 100_000 else None


def parse_geojson(payload: dict[str, Any]) -> list[Airspace]:
    """Airspaces in a GeoJSON response. Malformed features are skipped."""
    found = []
    for feature in payload.get("features") or []:
        try:
            properties, geometry = feature["properties"], feature["geometry"]
            cls = properties["CLASS"]
            if cls not in CLASSES:
                continue
            polygons = geometry["coordinates"]
            if geometry["type"] == "Polygon":
                polygons = [polygons]
            rings = tuple(
                tuple((float(lat), float(lon)) for lon, lat, *_ in ring)
                for polygon in polygons
                for ring in polygon
                if len(ring) >= 3
            )
            if not rings:
                continue
            lats = [lat for ring in rings for lat, _ in ring]
            lons = [lon for ring in rings for _, lon in ring]
            found.append(
                Airspace(
                    int(properties["OBJECTID"]),
                    cls,
                    str(properties.get("NAME") or ""),
                    _altitude(properties.get("LOWER_VAL")),
                    _altitude(properties.get("UPPER_VAL")),
                    rings,
                    (min(lats), min(lons), max(lats), max(lons)),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return found


def _http_fetch(cell: Cell) -> dict[str, Any]:
    south, west = cell
    response = net.get(
        requests,
        QUERY_URL,
        params={
            "where": "CLASS IN ('B','C','D')",
            "geometry": f"{west},{south},{west + CELL_DEG},{south + CELL_DEG}",
            "geometryType": "esriGeometryEnvelope",
            "inSR": "4326",
            "spatialRel": "esriSpatialRelIntersects",
            "outFields": "OBJECTID,NAME,CLASS,LOWER_VAL,UPPER_VAL",
            "outSR": "4326",
            "geometryPrecision": "4",
            "maxAllowableOffset": "0.0003",
            "f": "geojson",
        },
        headers={"User-Agent": USER_AGENT},
        timeout=(4.0, 20.0),
    )
    response.raise_for_status()
    payload = response.json()
    if "features" not in payload:  # the service reports errors with a 200 and an error body
        raise ValueError(f"unexpected airspace response: {str(payload)[:120]}")
    return payload


def cells_around(lat: float, lon: float, radius_nm: float) -> list[Cell]:
    """Cells that a circle of radius_nm around the point touches."""
    lat_span = radius_nm / 60.0
    lon_span = lat_span / max(cos(radians(lat)), 0.01)
    south = floor((lat - lat_span) / CELL_DEG) * CELL_DEG
    west = floor((lon - lon_span) / CELL_DEG) * CELL_DEG
    cells = []
    cell_lat = south
    while cell_lat <= lat + lat_span:
        cell_lon = west
        while cell_lon <= lon + lon_span:
            cells.append((cell_lat, (cell_lon + 180) % 360 - 180))
            cell_lon += CELL_DEG
        cell_lat += CELL_DEG
    return cells


class AirspaceStore:
    def __init__(
        self, cache_dir: Path, fetch: Callable[[Cell], dict[str, Any]] | None = None
    ) -> None:
        self._cache_dir = cache_dir
        self._fetch = fetch or _http_fetch
        self._lock = threading.Lock()
        self._cells: dict[Cell, tuple[Airspace, ...]] = {}
        # When each loaded cell was fetched (wall time), and when they were last checked
        # against the calendar: a display left running must notice a new edition too.
        self._fetched_at: dict[Cell, float] = {}
        self._checked = time.monotonic()
        self._pending: set[Cell] = set()
        self._failed: dict[Cell, float] = {}
        self._jobs: queue.Queue[Cell | None] = queue.Queue()
        self._thread = threading.Thread(target=self._work, name="airspace", daemon=True)
        self._stop_event = threading.Event()
        self._fetched = False  # whether the last load went to the network
        self.version = 0  # goes up whenever a cell finishes loading

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        self._jobs.put(None)

    def clear(self) -> None:
        """Delete the cached boundaries on disk. What is loaded stays in use until the
        display restarts; then it is fetched again as needed."""
        try:
            for path in self._cache_dir.glob("*.geojson"):
                path.unlink(missing_ok=True)
        except OSError:
            pass

    def disk_bytes(self) -> int:
        try:
            return sum(p.stat().st_size for p in self._cache_dir.glob("*") if p.is_file())
        except OSError:
            return 0

    def around(self, lat: float, lon: float, radius_nm: float) -> tuple[Airspace, ...]:
        """Airspaces loaded so far in the cells around a point. Cells not yet loaded are
        requested; watch version to know when to ask again."""
        found: dict[int, Airspace] = {}
        check = time.monotonic() - self._checked >= CHECK_LOADED_EVERY_S
        if check:
            self._checked = time.monotonic()
        for cell in cells_around(lat, lon, radius_nm):
            with self._lock:
                loaded = self._cells.get(cell)
                failed_at = self._failed.get(cell)
                retry = failed_at is None or time.monotonic() - failed_at >= RETRY_AFTER_S
                # In hand, but a new edition has come out since: keep showing it, and fetch.
                stale = (
                    check
                    and loaded is not None
                    and out_of_date(self._fetched_at.get(cell, 0.0), time.time())
                )
                if (loaded is None or stale) and cell not in self._pending and retry:
                    self._pending.add(cell)
                    self._jobs.put(cell)
            for airspace in loaded or ():
                found[airspace.ident] = airspace  # one airspace can straddle several cells
        return tuple(found.values())

    def _path(self, cell: Cell) -> Path:
        return self._cache_dir / f"{cell[0]}_{cell[1]}.geojson"

    def _work(self) -> None:
        while True:
            cell = self._jobs.get()
            if cell is None:
                return
            self._fetched = False
            try:
                airspaces = self._load(cell)
            except Exception as exc:  # network, disk or data trouble: try again later
                log.warning("airspace for cell %s failed: %s", cell, exc)
                airspaces = None
            with self._lock:
                self._pending.discard(cell)
                if airspaces is None:
                    self._failed[cell] = time.monotonic()
                else:
                    self._cells[cell] = airspaces
                    try:
                        self._fetched_at[cell] = self._path(cell).stat().st_mtime
                    except OSError:
                        self._fetched_at[cell] = time.time()
                    self._failed.pop(cell, None)
                    self.version += 1
            if self._fetched and self._stop_event.wait(FETCH_SPACING_S):
                return

    def _load(self, cell: Cell) -> tuple[Airspace, ...]:
        path = self._path(cell)
        cached: dict[str, Any] | None = None
        try:
            cached = json.loads(path.read_text())
            if not out_of_date(path.stat().st_mtime, time.time()):
                return tuple(parse_geojson(cached))
        except (OSError, ValueError):
            pass
        self._fetched = True
        try:
            payload = self._fetch(cell)
        except (requests.RequestException, ValueError):
            if cached is None:
                raise
            return tuple(parse_geojson(cached))  # out of date beats nothing
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, separators=(",", ":")))
            tmp.replace(path)
        except OSError as exc:
            log.warning("could not cache airspace at %s: %s", path, exc)
        return tuple(parse_geojson(payload))
