"""Airports from the bundled OurAirports table: lookup by code, search as you type, and
finding those near a point."""

import csv
import gzip
import io
from bisect import bisect_right
from collections import defaultdict
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from math import cos, floor, radians

from flighttrack.geo import bearing_deg, distance_nm

LARGE, MEDIUM, SMALL, WATER = "L", "M", "S", "W"
_SIZE_RANK = {LARGE: 0, MEDIUM: 1, SMALL: 2, WATER: 3}


@dataclass(frozen=True, slots=True)
class Airport:
    ident: str
    name: str
    place: str
    lat: float
    lon: float
    size: str = SMALL  # LARGE, MEDIUM, SMALL or WATER (a seaplane base)
    elevation_ft: int | None = None
    iata: str = ""

    @property
    def description(self) -> str:
        return f"{self.ident} - {self.name}" + (f" ({self.place})" if self.place else "")

    @property
    def point(self) -> tuple[float, float]:
        return self.lat, self.lon


@dataclass(frozen=True, slots=True)
class _Entry:
    """An airport with what is needed to search it, worked out once."""

    airport: Airport
    codes: tuple[str, ...]  # every code it answers to, upper case, best known first
    words: tuple[str, ...]  # words of its name and place, upper case
    text: str  # name and place together, upper case
    idents: tuple[str, ...]  # its ident and ICAO code, upper case
    local: str  # its national code, upper case, or ""


@lru_cache(maxsize=1)
def _entries() -> tuple[_Entry, ...]:
    raw = resources.files("flighttrack").joinpath("data/airports.csv.gz").read_bytes()
    entries = []
    with io.TextIOWrapper(gzip.GzipFile(fileobj=io.BytesIO(raw)), encoding="utf-8") as text:
        for row in csv.DictReader(text):
            elevation = row.get("elevation_ft") or ""
            airport = Airport(
                row["ident"],
                row["name"],
                row["place"],
                float(row["lat"]),
                float(row["lon"]),
                row.get("size") or SMALL,
                int(float(elevation))
                if elevation.lstrip("-").replace(".", "", 1).isdigit()
                else None,
                row["iata"],
            )
            codes = tuple(
                dict.fromkeys(
                    c.upper() for c in (row["iata"], row["ident"], row["icao"], row["local"]) if c
                )
            )
            name_and_place = f"{row['name']} {row['place']}".upper()
            words = tuple(
                w for w in name_and_place.replace(",", " ").replace("/", " ").split() if w
            )
            idents = tuple(c.upper() for c in (row["ident"], row["icao"]) if c)
            entries.append(
                _Entry(airport, codes, words, name_and_place, idents, row["local"].upper())
            )
    return tuple(entries)


@lru_cache(maxsize=1)
def _by_code() -> dict[str, Airport]:
    """Every code an airport answers to, mapped to the airport."""
    by_priority: list[dict[str, Airport]] = [{}, {}, {}]  # ICAO/ident, then IATA, then local
    for entry in _entries():
        airport = entry.airport
        # The table lists larger airports first; setdefault lets them keep a shared code.
        for code in entry.idents:
            by_priority[0].setdefault(code, airport)
        if airport.iata:
            by_priority[1].setdefault(airport.iata.upper(), airport)
        if entry.local:
            by_priority[2].setdefault(entry.local, airport)
    index: dict[str, Airport] = {}
    for codes in reversed(by_priority):
        index.update(codes)
    return index


def find(code: str | None) -> Airport | None:
    """Airport for an ICAO, IATA or national code (case-insensitive), or None."""
    return _by_code().get(code.strip().upper()) if code and code.strip() else None


@lru_cache(maxsize=1)
def _haystack() -> tuple[str, list[int]]:
    """Every airport's codes, name and place run together into one long string, with where
    each airport's part starts. Finding a query in it is then one fast scan of a string,
    in place of a Python loop over 49,000 airports."""
    parts, starts, at = [], [], 0
    for entry in _entries():
        piece = "\n" + " ".join(entry.codes) + " " + entry.text
        starts.append(at)
        parts.append(piece)
        at += len(piece)
    return "".join(parts), starts


@lru_cache(maxsize=1)
def _by_initial() -> tuple[dict[str, tuple[int, ...]], dict[str, tuple[int, ...]]]:
    """Airports by the first letter of their codes, and by the first letter of the words of
    their names. One or two letters typed can only match at the start of a code or a word."""
    by_code: dict[str, set[int]] = defaultdict(set)
    by_word: dict[str, set[int]] = defaultdict(set)
    for number, entry in enumerate(_entries()):
        for code in entry.codes:
            by_code[code[0]].add(number)
        for word in entry.words:
            by_word[word[0]].add(number)
    return (
        {letter: tuple(sorted(numbers)) for letter, numbers in by_code.items()},
        {letter: tuple(sorted(numbers)) for letter, numbers in by_word.items()},
    )


def _name_candidates(text: str) -> list[int]:
    """Numbers of the airports whose name or place could match what was typed, in table
    order: a superset, found fast."""
    if len(text) < 3:
        return list(_by_initial()[1].get(text[0], ()))
    # Anything that matches contains the first word typed, somewhere in its codes or name.
    first_word = text.split()[0]
    haystack, starts = _haystack()
    found: list[int] = []
    at = haystack.find(first_word)
    while at != -1:
        number = bisect_right(starts, at) - 1
        found.append(number)
        # On to the next airport: one hit in this one is enough.
        nxt = starts[number + 1] if number + 1 < len(starts) else len(haystack)
        at = haystack.find(first_word, nxt)
    return found


@lru_cache(maxsize=256)
def search(
    query: str, near: tuple[float, float] | None = None, limit: int = 5
) -> tuple[Airport, ...]:
    """Airports matching what has been typed so far, best first.

    A code typed in full comes first, then codes that start with the text, then names and
    places with a word starting with it, then names merely containing it. Within each of
    those, bigger airports come before smaller ones, and nearer before farther.
    """
    text = " ".join(query.upper().split())
    if not text:
        return ()
    first_word = text.split()[0]
    entries = _entries()
    scored = []

    def add(number: int, score: int) -> None:
        airport = entries[number].airport
        distance = distance_nm(*near, airport.lat, airport.lon) if near else 0.0
        scored.append((score, _SIZE_RANK.get(airport.size, 2), distance, airport.ident, airport))

    # Codes first. They are few, and a match on a code outranks any match on a name, so with
    # enough of them there is no need to look at names at all.
    on_code = set()
    for number in _by_initial()[0].get(text[0], ()):
        codes = entries[number].codes
        if text in codes:
            add(number, 0)
            on_code.add(number)
        elif any(code.startswith(text) for code in codes):
            add(number, 1)
            on_code.add(number)
    if len(scored) >= limit:
        scored.sort(key=lambda item: item[:4])
        return tuple(item[4] for item in scored[:limit])

    # Then names. The table runs from the largest airports to the smallest, and size is what
    # ranks next after how well the text matched. So once a whole size has been looked at and
    # there are enough of the best kind of name match, nothing later can displace them. That
    # matters for a word like "airport", which nearly every one of them has.
    strong = len(scored)
    size = None
    for number in _name_candidates(text):
        entry = entries[number]
        if entry.airport.size != size:
            if size is not None and strong >= limit:
                break
            size = entry.airport.size
        if number in on_code:
            continue
        if entry.text.startswith(text) or (
            " " not in text and any(word.startswith(text) for word in entry.words)
        ):
            add(number, 2)
            strong += 1
        elif len(text) >= 3 and (text in entry.text or first_word in entry.words):
            add(number, 3 if text in entry.text else 4)
    scored.sort(key=lambda item: item[:4])
    return tuple(item[4] for item in scored[:limit])


def warm() -> None:
    """Build every table now. Meant for a worker thread at start-up, so the first search or
    the first airport tap does not pay for it on the main thread."""
    _by_code()
    _haystack()
    _by_initial()
    _grid()


@lru_cache(maxsize=1)
def _grid() -> dict[tuple[int, int], tuple[Airport, ...]]:
    """Airports bucketed by whole-degree cell."""
    grid: dict[tuple[int, int], list[Airport]] = defaultdict(list)
    for entry in _entries():
        airport = entry.airport
        grid[(floor(airport.lat), floor(airport.lon))].append(airport)
    return {cell: tuple(found) for cell, found in grid.items()}


@lru_cache(maxsize=32)
def near(lat: float, lon: float, radius_nm: float, sizes: str = "LMSW") -> tuple[Airport, ...]:
    """Airports of the given sizes within radius_nm of a point, biggest first."""
    lat_span = radius_nm / 60.0
    lon_span = lat_span / max(cos(radians(lat)), 0.01)
    found = []
    for cell_lat in range(floor(lat - lat_span), floor(lat + lat_span) + 1):
        for cell_lon in range(floor(lon - lon_span), floor(lon + lon_span) + 1):
            wrapped = (cell_lon + 180) % 360 - 180
            for airport in _grid().get((cell_lat, wrapped), ()):
                if airport.size in sizes and (
                    distance_nm(lat, lon, airport.lat, airport.lon) <= radius_nm
                ):
                    found.append(airport)
    found.sort(key=lambda a: (_SIZE_RANK.get(a.size, 2), a.ident))
    return tuple(found)


NEAR_AIRPORT_NM = 5.0  # this close, a position is described as the airport itself


def whereabouts(lat: float, lon: float) -> str:
    """A position in words, by the nearest airport: "KPAO" within NEAR_AIRPORT_NM of any
    airport with a proper code, else "20 NM NE KSJC" from the nearest medium or large one
    (better known than the strip down the road). Empty with none within a few hundred miles."""

    def listed(radius: float, sizes: str) -> list[Airport]:
        return [a for a in near(lat, lon, radius, sizes) if "-" not in a.ident]

    def miles(airport: Airport) -> float:
        return distance_nm(airport.lat, airport.lon, lat, lon)

    close = listed(NEAR_AIRPORT_NM, "LMS")
    if close:
        return min(close, key=miles).ident
    for radius in (25.0, 60.0, 150.0, 400.0):
        found = listed(radius, "LM")
        if found:
            closest = min(found, key=miles)
            way = bearing_deg(closest.lat, closest.lon, lat, lon)
            points = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")
            return f"{miles(closest):.0f} NM {points[round(way / 45) % 8]} {closest.ident}"
    return ""
