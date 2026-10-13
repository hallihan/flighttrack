"""Where an aircraft was last seen, when it is not being heard now.

The public feeds answer only for aircraft broadcasting at the moment. For one that has gone
quiet (landed, say) this looks in two places that keep a little history, and the caller takes
whichever saw it last:

- a local readsb/tar1090 receiver, if one is set: tar1090 keeps the last few hours of what it
  heard in `chunks/` beside its `data/aircraft.json`, as gzipped JSON snapshots. On the LAN,
  so no rate limit; it covers only what the antenna heard, but that can be lower down near
  home than the public network hears.
- adsb.lol's daily traces (the files its own map uses): today's under `data/traces/`, earlier
  days under `globe_history/YYYY/MM/DD/`. Data under the Open Database License. Asked one
  request a second, newest first, for a few days back.

Both are found by ICAO hex. A registration is turned into one with adsbdb.com; a callsign
cannot be, so a callsign is only matched in the receiver's chunks, which carry it.

Everything here may block: call it from a worker thread.
"""

import gzip
import json
import re
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import requests

from flighttrack import legs, net
from flighttrack.feeds.base import Aircraft
from flighttrack.feeds.http import USER_AGENT
from flighttrack.legs import TimedPoint

AIRFRAME_URL = "https://api.adsbdb.com/v0/aircraft/{}"
TODAY_URL = "https://adsb.lol/data/traces/{tail}/trace_full_{hex}.json"
RECENT_URL = "https://adsb.lol/data/traces/{tail}/trace_recent_{hex}.json"
DAY_URL = "https://adsb.lol/globe_history/{day:%Y/%m/%d}/traces/{tail}/trace_full_{hex}.json"
DAYS_BACK = 3  # earlier days of adsb.lol history to look through
SPACING_S = 1.0  # between requests to the public services
MAX_CHUNKS = 400  # of a receiver's history; tar1090 keeps about 180 by default (8 hours)
_HEX = re.compile(r"[0-9a-f]{6}")
_TIMEOUT = (4.0, 10.0)


def _session(session: requests.Session | None) -> requests.Session:
    session = session or requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    return session


def _plain(text: str) -> str:
    return text.strip().upper().replace("-", "").replace(" ", "")


def hex_for(text: str, session: requests.Session | None = None) -> str | None:
    """The ICAO hex code for what was typed: itself if it is one, else the registration's,
    from adsbdb.com. None if it is neither (a callsign, most likely)."""
    plain = _plain(text).lower()
    if _HEX.fullmatch(plain):
        return plain
    if not 3 <= len(plain) <= 8 or not plain.isalnum():
        return None
    url = AIRFRAME_URL.format(plain.upper())
    response = net.get(_session(session), url, timeout=_TIMEOUT)
    if response.status_code == 404:
        return None
    response.raise_for_status()
    try:
        found = response.json()["response"]["aircraft"]["mode_s"].lower()
    except (KeyError, TypeError, AttributeError, ValueError):
        return None
    return found if _HEX.fullmatch(found) else None


def _number(value) -> float | None:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


def parse_trace(data: dict, wall_now: float, mono_now: float) -> Aircraft | None:
    """The last position in a readsb trace file, as an Aircraft heard at that time."""
    hex_code = str(data.get("icao", "")).lower()
    base = _number(data.get("timestamp"))
    rows = data.get("trace")
    if not _HEX.fullmatch(hex_code) or base is None or not isinstance(rows, list):
        return None
    callsign = None
    for row in reversed(rows):
        details = row[8] if isinstance(row, list) and len(row) > 8 else None
        if isinstance(details, dict) and str(details.get("flight", "")).strip():
            callsign = str(details["flight"]).strip()
            break
    for row in reversed(rows):
        if not isinstance(row, list) or len(row) < 6:
            continue
        offset, lat, lon = _number(row[0]), _number(row[1]), _number(row[2])
        if offset is None or lat is None or lon is None:
            continue
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            continue
        altitude = row[3]
        rate = _number(row[7]) if len(row) > 7 else None
        return Aircraft(
            hex=hex_code,
            lat=lat,
            lon=lon,
            seen_at=mono_now - max(wall_now - (base + offset), 0.0),
            callsign=callsign,
            registration=str(data["r"]) if data.get("r") else None,
            type_code=str(data["t"]) if data.get("t") else None,
            altitude_ft=round(altitude) if _number(altitude) is not None else None,
            on_ground=altitude == "ground",
            ground_speed_kt=_number(row[4]),
            track_deg=_number(row[5]),
            vertical_rate_fpm=round(rate) if rate is not None else None,
        )
    return None


def adsb_lol_last_seen(
    hex_code: str,
    session: requests.Session | None = None,
    wall: Callable[[], float] = time.time,
    clock: Callable[[], float] = time.monotonic,
    pause: Callable[[float], None] = time.sleep,
) -> Aircraft | None:
    """The last position adsb.lol has for an aircraft, today or up to DAYS_BACK days before.
    None if it has none. Raises on network failure."""
    session = _session(session)
    today = datetime.fromtimestamp(wall(), UTC)
    urls = [TODAY_URL.format(tail=hex_code[-2:], hex=hex_code)] + [
        DAY_URL.format(day=today - timedelta(days=n), tail=hex_code[-2:], hex=hex_code)
        for n in range(1, DAYS_BACK + 1)
    ]
    for index, url in enumerate(urls):
        if index:
            pause(SPACING_S)
        response = net.get(session, url, timeout=_TIMEOUT)
        if response.status_code == 404:
            continue
        response.raise_for_status()
        try:
            found = parse_trace(response.json(), wall(), clock())
        except ValueError:
            found = None
        if found is not None:
            return found
    return None


def chunks_base(receiver_url: str) -> str | None:
    """Where a tar1090 receiver keeps its history, from its aircraft.json address."""
    suffix = "data/aircraft.json"
    return receiver_url[: -len(suffix)] + "chunks/" if receiver_url.endswith(suffix) else None


def _decode(content: bytes) -> dict:
    if content[:2] == b"\x1f\x8b":
        content = gzip.decompress(content)
    data = json.loads(content)
    return data if isinstance(data, dict) else {}


def receiver_last_seen(
    receiver_url: str,
    hex_code: str | None,
    callsign: str | None,
    session: requests.Session | None = None,
    wall: Callable[[], float] = time.time,
    clock: Callable[[], float] = time.monotonic,
) -> Aircraft | None:
    """The last time a local tar1090 receiver heard an aircraft, by hex or callsign, within
    the history it keeps. None if it did not, or keeps none. Raises on network failure."""
    base = chunks_base(receiver_url)
    want_call = _plain(callsign) if callsign else None
    if base is None or not (hex_code or want_call):
        return None
    session = _session(session)
    response = net.get(session, base + "chunks.json", timeout=_TIMEOUT)
    if response.status_code == 404:
        return None
    response.raise_for_status()
    listing = _decode(response.content)
    names = listing.get("chunks_all") or listing.get("chunks") or []
    names = [n for n in names if isinstance(n, str) and re.fullmatch(r"[\w.]+", n)]
    # Newest first: the "current" files, then the chunks by the time in their names.
    current = [n for n in names if n.startswith("current")]
    older = sorted((n for n in names if n.startswith("chunk_")), reverse=True)
    for name in (current + older)[:MAX_CHUNKS]:
        chunk = net.get(session, base + name, timeout=_TIMEOUT)
        if chunk.status_code != 200:
            continue
        try:
            snapshots = _decode(chunk.content).get("files", [])
        except (ValueError, OSError):
            continue
        for snapshot in reversed(snapshots):
            found = _in_snapshot(snapshot, hex_code, want_call, wall(), clock())
            if found is not None:
                return found
    return None


def _in_snapshot(
    snapshot: dict, hex_code: str | None, callsign: str | None, wall_now: float, mono_now: float
) -> Aircraft | None:
    """An aircraft in one tar1090 history snapshot. Rows are [hex, altitude, speed, track,
    lat, lon, seconds since its position, source, callsign, squawk]."""
    now = _number(snapshot.get("now"))
    for row in snapshot.get("aircraft", []) if now is not None else []:
        if not isinstance(row, list) or len(row) < 7:
            continue
        flight = _plain(str(row[8])) if len(row) > 8 and row[8] else None
        if row[0] != hex_code and (callsign is None or flight != callsign):
            continue
        lat, lon, age = _number(row[4]), _number(row[5]), _number(row[6]) or 0.0
        if lat is None or lon is None:
            continue
        altitude = row[1]
        return Aircraft(
            hex=str(row[0]).lower(),
            lat=lat,
            lon=lon,
            seen_at=mono_now - max(wall_now - (now - age), 0.0),
            callsign=flight,
            altitude_ft=round(altitude) if _number(altitude) is not None else None,
            on_ground=altitude == "ground",
            ground_speed_kt=_number(row[2]),
            track_deg=_number(row[3]),
            squawk=str(row[9]) if len(row) > 9 and row[9] else None,
        )
    return None


def last_seen(
    text: str,
    receiver_url: str | None = None,
    known_hex: str | None = None,
    session: requests.Session | None = None,
    pause: Callable[[float], None] = time.sleep,
) -> list[Aircraft]:
    """Every last sighting found of what was typed (hex, registration or callsign): from the
    local receiver's history and from adsb.lol's. The caller takes the newest. known_hex is
    its hex if the caller already knows it (a callsign it has seen, say). Raises only if
    nothing was found and a source could not be asked."""
    session = _session(session)
    found: list[Aircraft] = []
    trouble: Exception | None = None
    try:
        hex_code = known_hex or hex_for(text, session)
    except requests.RequestException as exc:
        hex_code, trouble = None, exc
    if receiver_url:
        try:
            heard = receiver_last_seen(receiver_url, hex_code, _plain(text), session)
            if heard is not None:
                found.append(heard)
                hex_code = hex_code or heard.hex
        except (requests.RequestException, ValueError, OSError) as exc:
            trouble = trouble or exc
    if hex_code:
        try:
            seen = adsb_lol_last_seen(hex_code, session, pause=pause)
            if seen is not None:
                found.append(seen)
        except requests.RequestException as exc:
            trouble = trouble or exc
    if not found and trouble is not None:
        raise trouble
    return found


def _trace_points(data: object, offset: float) -> list[TimedPoint]:
    """The points of a readsb trace file, with monotonic times (offset from wall clock)."""
    base = _number(data.get("timestamp")) if isinstance(data, dict) else None
    rows = data.get("trace") if isinstance(data, dict) else None
    if base is None or not isinstance(rows, list):
        return []
    points: list[TimedPoint] = []
    for row in rows:
        if not isinstance(row, list) or len(row) < 4:
            continue
        t, lat, lon = _number(row[0]), _number(row[1]), _number(row[2])
        if t is None or lat is None or lon is None:
            continue
        altitude = row[3]
        height = None if altitude == "ground" or _number(altitude) is None else round(altitude)
        points.append((lat, lon, height, base + t + offset))
    return points


def flight_trace(
    hex_code: str,
    session: requests.Session | None = None,
    wall: Callable[[], float] = time.time,
    clock: Callable[[], float] = time.monotonic,
) -> list[TimedPoint]:
    """Everywhere adsb.lol has an aircraft in the last day: its full trace and its recent
    one (the full one is written only every so often), as one track, oldest first, with
    monotonic times. Which of it is the current flight is for legs.py to say. Empty if
    there is no trace. Raises on network failure."""
    session = _session(session)
    offset = clock() - wall()  # from wall-clock seconds to monotonic ones
    found: list[list[TimedPoint]] = []
    for url in (TODAY_URL, RECENT_URL):
        response = net.get(session, url.format(tail=hex_code[-2:], hex=hex_code), timeout=_TIMEOUT)
        if response.status_code == 404:
            continue
        response.raise_for_status()
        try:
            found.append(_trace_points(response.json(), offset))
        except ValueError:
            continue
    return legs.merged(*found)


def receiver_track(
    receiver_url: str,
    hex_code: str,
    session: requests.Session | None = None,
    wall: Callable[[], float] = time.time,
    clock: Callable[[], float] = time.monotonic,
) -> list[TimedPoint]:
    """Everywhere a local tar1090 receiver heard an aircraft, in the hours of history it
    keeps, oldest first, with monotonic times. On the LAN: no rate limit. Empty if it keeps
    no history. Raises on network failure."""
    base = chunks_base(receiver_url)
    if base is None:
        return []
    session = _session(session)
    response = net.get(session, base + "chunks.json", timeout=_TIMEOUT)
    if response.status_code == 404:
        return []
    response.raise_for_status()
    listing = _decode(response.content)
    names = listing.get("chunks_all") or listing.get("chunks") or []
    names = [n for n in names if isinstance(n, str) and re.fullmatch(r"[\w.]+", n)]
    offset = clock() - wall()
    points: list[TimedPoint] = []
    for name in names[-MAX_CHUNKS:]:
        chunk = net.get(session, base + name, timeout=_TIMEOUT)
        if chunk.status_code != 200:
            continue
        try:
            snapshots = _decode(chunk.content).get("files", [])
        except (ValueError, OSError):
            continue
        for snapshot in snapshots:
            now = _number(snapshot.get("now")) if isinstance(snapshot, dict) else None
            for row in snapshot.get("aircraft", []) if now is not None else []:
                if not isinstance(row, list) or len(row) < 7 or row[0] != hex_code:
                    continue
                lat, lon, age = _number(row[4]), _number(row[5]), _number(row[6]) or 0.0
                if lat is None or lon is None:
                    continue
                altitude = row[1]
                height = None if altitude == "ground" else _number(altitude)
                points.append(
                    (lat, lon, None if height is None else round(height), now - age + offset)
                )
    return legs.merged(points)
