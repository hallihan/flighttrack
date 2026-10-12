"""Current weather reports (METARs) for airports, from the US Aviation Weather Center's public
data API. It covers reporting stations worldwide and needs no key.

Reports are fetched on a worker thread when an airport is selected and kept for a few
minutes: a METAR is issued about once an hour, more often when the weather is changing.
"""

import logging
import queue
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import requests

from flighttrack import net
from flighttrack.feeds.http import USER_AGENT

log = logging.getLogger(__name__)

URL = "https://aviationweather.gov/api/data/metar"
FRESH_FOR_S = 300.0
RETRY_AFTER_S = 60.0
HPA_PER_INHG = 33.8639


@dataclass(frozen=True, slots=True)
class Metar:
    station: str
    raw: str
    observed_at: float | None = None  # Unix time of the observation
    category: str | None = None  # VFR, MVFR, IFR or LIFR
    wind_dir: int | str | None = None  # degrees true, or "VRB"
    wind_kt: int | None = None
    gust_kt: int | None = None
    visibility: str | None = None  # statute miles as the report gives it, e.g. "10+" or "3"
    ceiling_ft: int | None = None  # lowest broken or overcast layer; None when there is none
    sky: str | None = None  # overall cover, e.g. "CLR", "SCT", "OVC"
    temp_c: float | None = None
    dewpoint_c: float | None = None
    altimeter_hpa: float | None = None

    @property
    def wind(self) -> str:
        if self.wind_kt is None:
            return "---"
        if self.wind_kt == 0:
            return "Calm"
        direction = "VRB" if isinstance(self.wind_dir, str) else f"{self.wind_dir or 0:03d}°"
        gust = f" G{self.gust_kt}" if self.gust_kt else ""
        return f"{direction} {self.wind_kt}{gust} kt"

    @property
    def altimeter(self) -> str:
        """In the units the station itself reports: inches of mercury or hectopascals."""
        if self.altimeter_hpa is None:
            return "---"
        if any(part.startswith("A") and part[1:].isdigit() for part in self.raw.split()):
            return f"{self.altimeter_hpa / HPA_PER_INHG:.2f} inHg"
        return f"{round(self.altimeter_hpa)} hPa"


def _int(value: Any) -> int | None:
    return round(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


def _float(value: Any) -> float | None:
    is_number = isinstance(value, int | float) and not isinstance(value, bool)
    return float(value) if is_number else None


def parse(record: dict[str, Any]) -> Metar | None:
    """A report from one record of the API's JSON, or None if it has no usable text."""
    raw = record.get("rawOb")
    station = record.get("icaoId")
    if not isinstance(raw, str) or not raw.strip() or not isinstance(station, str):
        return None
    ceilings = [
        base
        for layer in record.get("clouds") or []
        if isinstance(layer, dict)
        and layer.get("cover") in ("BKN", "OVC", "OVX")
        and (base := _int(layer.get("base"))) is not None
    ]
    wind_dir = record.get("wdir")
    visibility = record.get("visib")
    category = record.get("fltCat")
    sky = record.get("cover")
    return Metar(
        station=station,
        raw=raw.strip().removeprefix("METAR ").removeprefix("SPECI "),
        observed_at=_float(record.get("obsTime")),
        category=category if category in ("VFR", "MVFR", "IFR", "LIFR") else None,
        wind_dir="VRB" if wind_dir == "VRB" else _int(wind_dir),
        wind_kt=_int(record.get("wspd")),
        gust_kt=_int(record.get("wgst")),
        visibility=str(visibility) if isinstance(visibility, int | float | str) else None,
        ceiling_ft=min(ceilings) if ceilings else None,
        sky=sky if isinstance(sky, str) else None,
        temp_c=_float(record.get("temp")),
        dewpoint_c=_float(record.get("dewp")),
        altimeter_hpa=_float(record.get("altim")),
    )


def _http_fetch(station: str) -> list[dict[str, Any]]:
    response = net.get(
        requests,
        URL,
        params={"ids": station, "format": "json"},
        headers={"User-Agent": USER_AGENT},
        timeout=(4.0, 10.0),
    )
    response.raise_for_status()
    if response.status_code == 204 or not response.content:
        return []  # the service's way of saying it has no report for that station
    payload = response.json()
    return payload if isinstance(payload, list) else []


# What lookup() returns besides a report.
LOADING = "loading"
NONE = "none"  # the station has no report (most small airfields do not report weather)
FAILED = "failed"


class MetarStore:
    def __init__(
        self,
        fetch: Callable[[str], list[dict[str, Any]]] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._fetch = fetch or _http_fetch
        self._clock = clock
        self._lock = threading.Lock()
        # station -> (when asked, a Metar or NONE or FAILED)
        self._known: dict[str, tuple[float, Metar | str]] = {}
        self._pending: set[str] = set()
        self._jobs: queue.Queue[str | None] = queue.Queue()
        self._thread = threading.Thread(target=self._work, name="metar", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._jobs.put(None)

    def lookup(self, station: str) -> Metar | str:
        """The report for a station if we have it, else LOADING, NONE or FAILED. Asks for a
        new one in the background when ours is missing or getting old; meanwhile the old one
        is still returned."""
        station = station.upper()
        now = self._clock()
        with self._lock:
            known = self._known.get(station)
            if known is None:
                wanted = True
            else:
                age = now - known[0]
                wanted = age >= (RETRY_AFTER_S if known[1] == FAILED else FRESH_FOR_S)
            if wanted and station not in self._pending:
                self._pending.add(station)
                self._jobs.put(station)
        return LOADING if known is None else known[1]

    def _work(self) -> None:
        while True:
            station = self._jobs.get()
            if station is None:
                return
            result: Metar | str
            try:
                records = self._fetch(station)
                reports = [m for m in map(parse, records) if m is not None]
                result = reports[0] if reports else NONE
            except (requests.RequestException, ValueError, TypeError) as exc:
                log.warning("METAR for %s failed: %s", station, exc)
                result = FAILED
            with self._lock:
                previous = self._known.get(station)
                if result == FAILED and previous is not None and isinstance(previous[1], Metar):
                    result = previous[1]  # an old report beats an error message
                self._known[station] = (self._clock(), result)
                self._pending.discard(station)
