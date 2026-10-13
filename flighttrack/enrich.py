"""What the position feed does not say about an aircraft: who operates the flight, where it is
going, what exactly the airframe is, and what it looks like.

Looked up for the selected aircraft only, on a worker thread, a request a second at most.
Two services, neither needing a key:

- adsbdb.com: the route for a callsign, and the airframe (type, owner, a photo) for a hex code;
- airport-data.com: photos for a hex code. Up to two different pictures are kept, the one
  adsbdb knows of first.

A picture of your own beats both: an image file in the `images` folder of the data
directory, named for a registration, a hex code or a type (N123AB.jpg, 4CA1D3.png, B738.jpg).

Answers are kept in memory, and on disk so they survive a restart: the small facts in one
file written at most every ten minutes, each photo once.
"""

import json
import logging
import queue
import re
import shutil
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlsplit

import requests

from flighttrack import cache, net
from flighttrack.cache import MAX_AGE_S
from flighttrack.favorites import normalize
from flighttrack.feeds.http import USER_AGENT

log = logging.getLogger(__name__)

ROUTE_URL = "https://api.adsbdb.com/v0/callsign/{}"
AIRFRAME_URL = "https://api.adsbdb.com/v0/aircraft/{}"
THUMB_URL = "https://airport-data.com/api/ac_thumb.json?m={}&n=2"
MAX_PHOTOS = 2  # shown side by side in the pane
# Pictures are fetched from the address a service gives us. Only from here, and only over
# https: an answer must not be able to point this display at anything else.
PHOTO_HOSTS = ("airport-data.com",)

MIN_SPACING_S = 1.0  # between requests
MAX_PHOTO_BYTES = 1_500_000
MAX_FULL_BYTES = 6_000_000  # the larger version, asked for only when a picture is opened
MAX_FULL_IN_MEMORY = 3
_THUMB_ID = re.compile(r"/thumbnails/\d+/\d+/(\d+)\.jpg$")
FULL_URL = "https://image.airport-data.com/aircraft/{}.jpg"
PAGE = "airport-data.com/aircraft/photo/{}"
MAX_CUSTOM_BYTES = 6_000_000
_IMAGE_SIGNATURES = (b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n")
IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png")

# How long an answer is good for, by what it was.
FRESH_S = {"route": 6 * 3600.0, "airframe": MAX_AGE_S, "photo": MAX_AGE_S, "full": 3600.0}
NONE_FRESH_S = {"route": 3600.0, "airframe": 86400.0, "photo": 7 * 86400.0, "full": 3600.0}
RETRY_FAILED_S = 60.0
SAVE_EVERY_S = 600.0
MAX_KEPT = 3000  # facts kept, and photos on disk
MAX_PHOTOS_IN_MEMORY = 60
CUSTOM_RESCAN_S = 30.0

CUSTOM_CREDIT = "Your image"

# What a lookup returns besides an answer.
LOADING = "loading"
NONE = "none"  # the service knows nothing of it
FAILED = "failed"

# A callsign worth asking a route for: an airline's three letters and a flight number.
# Registrations used as callsigns (N123AB, GABCD) have no route to find.
_AIRLINE_CALLSIGN = re.compile(r"[A-Z]{3}\d[A-Z0-9]{0,4}")


@dataclass(frozen=True, slots=True)
class Place:
    code: str  # the IATA code if it has one, else the ICAO: what goes on the screen
    name: str
    city: str
    lat: float
    lon: float

    @property
    def point(self) -> tuple[float, float]:
        return self.lat, self.lon


@dataclass(frozen=True, slots=True)
class Route:
    airline: str
    origin: Place
    destination: Place


@dataclass(frozen=True, slots=True)
class Airframe:
    type_name: str  # maker and model, e.g. "Boeing 737-8H4"
    owner: str
    photo_url: str = ""  # a thumbnail, if adsbdb had one


@dataclass(frozen=True, slots=True)
class Photo:
    data: bytes
    credit: str  # who to thank, shown beside it
    full_url: str = ""  # where a larger version of the same picture is, if anywhere
    page: str = ""  # the picture's own page at its source, without "https://": for the credit

    @property
    def attribution(self) -> str:
        """The credit in full, for where there is room: who, where from, and its page."""
        who, _, where = self.credit.rpartition(" / ")
        by = f"Photo by {who}, {where}" if who else f"Photo: {where}"
        return f"{by} ({self.page})" if self.page else by


def could_have_a_route(callsign: str | None) -> bool:
    return bool(callsign) and _AIRLINE_CALLSIGN.fullmatch(callsign.strip().upper()) is not None


def _text(value: object, limit: int = 60) -> str:
    return value.strip()[:limit] if isinstance(value, str) else ""


def _place(raw: object) -> Place:
    if not isinstance(raw, dict):
        raise ValueError("no airport")
    lat, lon = float(raw["latitude"]), float(raw["longitude"])
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError("airport out of range")
    code = _text(raw.get("iata_code"), 4) or _text(raw.get("icao_code"), 4)
    if not code:
        raise ValueError("airport without a code")
    return Place(code.upper(), _text(raw.get("name")), _text(raw.get("municipality"), 40), lat, lon)


def parse_route(payload: object) -> Route | None:
    """A route from adsbdb's answer, or None if it has none. Raises ValueError on nonsense."""
    response = payload.get("response") if isinstance(payload, dict) else None
    if not isinstance(response, dict):
        return None  # "unknown callsign" comes as a string
    flight = response.get("flightroute")
    if not isinstance(flight, dict):
        return None
    airline = flight.get("airline")
    name = _text(airline.get("name"), 40) if isinstance(airline, dict) else ""
    try:
        return Route(name, _place(flight.get("origin")), _place(flight.get("destination")))
    except (KeyError, TypeError) as exc:
        raise ValueError(f"bad route: {exc}") from exc


def allowed_photo_url(url: object) -> str:
    """The address, if it is one we are willing to fetch a picture from; else ""."""
    if not isinstance(url, str) or len(url) > 300:
        return ""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    ours = any(host == h or host.endswith("." + h) for h in PHOTO_HOSTS)
    return url if parts.scheme == "https" and ours else ""


def full_size_url(thumbnail_url: str) -> str:
    """Where airport-data keeps the full picture a thumbnail was made from; "" if the
    address is not one of its thumbnails."""
    found = _THUMB_ID.search(urlsplit(thumbnail_url).path) if thumbnail_url else None
    return allowed_photo_url(FULL_URL.format(found.group(1))) if found else ""


def photo_page(thumbnail_url: str) -> str:
    """The page at airport-data that a thumbnail belongs to, as text for a credit."""
    found = _THUMB_ID.search(urlsplit(thumbnail_url).path) if thumbnail_url else None
    return PAGE.format(found.group(1)) if found else ""


def parse_airframe(payload: object) -> Airframe | None:
    response = payload.get("response") if isinstance(payload, dict) else None
    aircraft = response.get("aircraft") if isinstance(response, dict) else None
    if not isinstance(aircraft, dict):
        return None
    maker, model = _text(aircraft.get("manufacturer"), 30), _text(aircraft.get("type"), 30)
    if maker.isupper() and len(maker) > 4:
        maker = maker.title()  # "BOEING" as some records have it; short ones (ATR) are names
    # Some makers' names already begin the model's ("Cessna" / "Cessna 172").
    name = model if model.lower().startswith(maker.lower()) else f"{maker} {model}".strip()
    return Airframe(
        name,
        _text(aircraft.get("registered_owner"), 50),
        allowed_photo_url(aircraft.get("url_photo_thumbnail")),
    )


def parse_thumbs(payload: object) -> list[tuple[str, str]]:
    """(picture address, photographer) for each picture in airport-data's answer."""
    items = payload.get("data") if isinstance(payload, dict) else None
    found = []
    for item in items if isinstance(items, list) else []:
        url = allowed_photo_url(item.get("image")) if isinstance(item, dict) else ""
        if url:
            found.append((url, _text(item.get("photographer"), 30)))
    return found


def _http_json(url: str) -> object | None:
    """The JSON at url; None when the service says it has nothing (404)."""
    response = net.get(requests, url, headers={"User-Agent": USER_AGENT}, timeout=(4.0, 10.0))
    if response.status_code == 404:
        return None
    response.raise_for_status()
    return response.json()


def _http_bytes(url: str, limit: int = MAX_PHOTO_BYTES) -> bytes | None:
    response = net.get(
        requests, url, headers={"User-Agent": USER_AGENT}, timeout=(4.0, 15.0), stream=True
    )
    if response.status_code == 404:
        return None
    response.raise_for_status()
    data = response.raw.read(limit + 1, decode_content=True)
    return data if len(data) <= limit else None


class Enricher:
    def __init__(
        self,
        data_dir: Path,
        fetch_json: Callable[[str], object | None] | None = None,
        fetch_bytes: Callable[..., bytes | None] | None = None,
        clock: Callable[[], float] = time.time,
        spacing_s: float = MIN_SPACING_S,
    ) -> None:
        """clock is wall time: what is kept on disk has to mean something after a restart."""
        self._dir = data_dir / "enrich"
        self._photo_dir = self._dir / "photos"
        self._custom_dir = data_dir / "images"
        self._fetch_json = fetch_json or _http_json
        self._fetch_bytes = fetch_bytes or _http_bytes
        self._clock = clock
        self._spacing = spacing_s
        self._lock = threading.Lock()
        # (kind, key) -> (when asked, the answer or NONE or FAILED). Facts only: no pictures.
        self._known: OrderedDict[tuple[str, str], tuple[float, object]] = OrderedDict()
        # hex -> its pictures, in memory
        self._photos: OrderedDict[str, tuple[Photo, ...]] = OrderedDict()
        self._full: OrderedDict[str, bytes] = OrderedDict()  # "hex:n" -> the larger version
        self._pending: set[tuple[str, str]] = set()
        self._jobs: queue.Queue[tuple[str, str, tuple[str, ...]] | None] = queue.Queue()
        self._thread = threading.Thread(target=self._work, name="enrich", daemon=True)
        self._last_request = float("-inf")
        self._dirty = False
        self._saved_at = clock()
        self._custom: dict[str, Path] = {}
        self._custom_seen = float("-inf")
        self.requests = 0  # for diagnostics
        self._load()

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._jobs.put(None)
        self.save()

    def clear(self) -> None:
        """Forget every answer and every photo, in memory and on disk (not your own
        pictures, which are not a cache). They are looked up again as needed."""
        with self._lock:
            self._known.clear()
            self._photos.clear()
            self._full.clear()
            self._dirty = False
        shutil.rmtree(self._dir, ignore_errors=True)

    def disk_usage(self) -> dict[str, int]:
        """Bytes on disk: the photos, and the facts (routes, operators, types)."""
        photos = cache.dir_bytes(self._photo_dir)
        return {"photos": photos, "details": max(cache.dir_bytes(self._dir) - photos, 0)}

    def disk_bytes(self) -> int:
        return sum(self.disk_usage().values())

    # ---- asking -------------------------------------------------------------------------------

    def _lookup(self, kind: str, key: str, extra: tuple[str, ...] = ()) -> object:
        now = self._clock()
        with self._lock:
            known = self._known.get((kind, key))
            if known is None:
                wanted = True
            else:
                age, answer = now - known[0], known[1]
                if answer == FAILED:
                    wanted = age >= RETRY_FAILED_S
                else:
                    wanted = age >= (NONE_FRESH_S if answer == NONE else FRESH_S)[kind]
            if wanted and (kind, key) not in self._pending:
                self._pending.add((kind, key))
                self._jobs.put((kind, key, extra))
        return LOADING if known is None else known[1]

    def route(self, callsign: str | None) -> Route | str:
        """The route flown under a callsign: a Route, or LOADING, NONE or FAILED."""
        if not could_have_a_route(callsign):
            return NONE
        return self._lookup("route", callsign.strip().upper())

    def airframe(self, hex_code: str) -> Airframe | str:
        return self._lookup("airframe", hex_code.lower())

    def photos(
        self, hex_code: str, registration: str | None = None, type_code: str | None = None
    ) -> tuple[Photo, ...] | str:
        """Pictures of this aircraft: yours if you have supplied one, else up to two
        different ones found for its hex code. A tuple of Photo, or LOADING, NONE or FAILED."""
        key = hex_code.lower()
        state = self._lookup("photo", key, (registration or "", type_code or ""))
        if state in (LOADING, NONE, FAILED):
            return state
        with self._lock:
            found = self._photos.get(key)
            if found is not None:
                self._photos.move_to_end(key)
                return found
            # Known to exist, but not in memory (dropped, or from before a restart).
            if ("photo", key) not in self._pending:
                self._pending.add(("photo", key))
                self._jobs.put(("photo", key, (registration or "", type_code or "")))
        return LOADING

    def full_photo(self, hex_code: str, index: int = 0) -> bytes | str:
        """The larger version of one of this aircraft's pictures, for looking at full
        screen: its bytes, or LOADING, NONE (there is no larger one) or FAILED. Kept in
        memory only."""
        key = f"{hex_code.lower()}:{index}"
        state = self._lookup("full", key)
        if state in (LOADING, NONE, FAILED):
            return state
        with self._lock:
            data = self._full.get(key)
            if data is not None:
                self._full.move_to_end(key)
                return data
            if ("full", key) not in self._pending:  # dropped from memory: fetch it again
                self._pending.add(("full", key))
                self._jobs.put(("full", key, ()))
        return LOADING

    # ---- the worker ---------------------------------------------------------------------------

    def _work(self) -> None:
        while True:
            job = self._jobs.get()
            if job is None:
                return
            kind, key, extra = job
            try:
                answer = getattr(self, f"_find_{kind}")(key, *extra)
            except (requests.RequestException, ValueError, TypeError, KeyError, OSError) as exc:
                log.warning("%s lookup for %s failed: %s", kind, key, exc)
                answer = FAILED
            with self._lock:
                previous = self._known.get((kind, key))
                if answer == FAILED and previous is not None and previous[1] not in (NONE, FAILED):
                    answer = previous[1]  # an old answer beats an error
                self._known[(kind, key)] = (self._clock(), answer)
                self._known.move_to_end((kind, key))
                while len(self._known) > MAX_KEPT:
                    self._known.popitem(last=False)
                self._pending.discard((kind, key))
                self._dirty = True
            if self._clock() - self._saved_at >= SAVE_EVERY_S:
                self.save()

    def _ask(self, fetch: Callable[[str], object], url: str) -> object:
        """One request, no sooner than a second after the last."""
        wait = self._last_request + self._spacing - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._last_request = time.monotonic()
        self.requests += 1
        return fetch(url)

    def _find_route(self, callsign: str) -> Route | str:
        return parse_route(self._ask(self._fetch_json, ROUTE_URL.format(callsign))) or NONE

    def _find_airframe(self, hex_code: str) -> Airframe | str:
        return parse_airframe(self._ask(self._fetch_json, AIRFRAME_URL.format(hex_code))) or NONE

    def _find_photo(self, hex_code: str, registration: str = "", type_code: str = "") -> str:
        """Finds the pictures and keeps them in memory; the answer recorded is only how many
        there are, or NONE."""
        mine = self._custom_photo(hex_code, registration, type_code)
        found = (mine,) if mine is not None else self._stored_photos(hex_code, fresh_only=True)
        if not found:
            # None kept, or those kept are past their thirty days: ask again, and fall back
            # on the old ones if nothing better comes.
            try:
                found = self._fetched_photos(hex_code)
            except (requests.RequestException, ValueError, TypeError, KeyError):
                found = ()
            if found:
                for index in range(MAX_PHOTOS):
                    self._photo_path(hex_code, index).unlink(missing_ok=True)
                for index, photo in enumerate(found):
                    self._store_photo(hex_code, index, photo)
            else:
                found = self._stored_photos(hex_code)
        if not found:
            return NONE
        with self._lock:
            self._photos[hex_code] = found
            self._photos.move_to_end(hex_code)
            while len(self._photos) > MAX_PHOTOS_IN_MEMORY:
                self._photos.popitem(last=False)
        return f"photos:{len(found)}"

    def _find_full(self, key: str) -> str:
        hex_code, _, number = key.partition(":")
        with self._lock:
            found = self._photos.get(hex_code)
        found = found or self._stored_photos(hex_code)
        index = int(number or 0)
        if index >= len(found):
            return NONE
        photo = found[index]
        if photo.credit == CUSTOM_CREDIT:
            data: object = photo.data  # your own file is already as large as it comes
        elif allowed_photo_url(photo.full_url):
            data = self._ask(lambda url: self._fetch_bytes(url, MAX_FULL_BYTES), photo.full_url)
        else:
            return NONE
        if not isinstance(data, bytes) or not data.startswith(_IMAGE_SIGNATURES):
            return NONE
        with self._lock:
            self._full[key] = data
            self._full.move_to_end(key)
            while len(self._full) > MAX_FULL_IN_MEMORY:
                self._full.popitem(last=False)
        return "full"

    def _fetched_photos(self, hex_code: str) -> tuple[Photo, ...]:
        """From both services: the one adsbdb knows of, then any different ones
        airport-data offers, up to MAX_PHOTOS."""
        with self._lock:
            known = self._known.get(("airframe", hex_code))
        airframe = known[1] if known is not None else None
        if not isinstance(airframe, Airframe):
            found = self._find_airframe(hex_code)
            with self._lock:
                self._known[("airframe", hex_code)] = (self._clock(), found)
            airframe = found if isinstance(found, Airframe) else None
        wanted: list[tuple[str, str]] = []  # address and credit, in order, each address once
        if airframe is not None and airframe.photo_url:
            wanted.append((airframe.photo_url, "airport-data.com"))
        thumbs = parse_thumbs(self._ask(self._fetch_json, THUMB_URL.format(hex_code)))
        for url, who in thumbs:
            credit = f"{who} / airport-data.com" if who else "airport-data.com"
            same = next((i for i, (have, _) in enumerate(wanted) if have == url), None)
            if same is None:
                wanted.append((url, credit))
            else:
                wanted[same] = (url, credit)  # the same picture, now with its photographer
        photos: list[Photo] = []
        for url, credit in wanted:
            if len(photos) == MAX_PHOTOS:
                break
            data = self._ask(self._fetch_bytes, url)
            if isinstance(data, bytes) and data.startswith(_IMAGE_SIGNATURES):
                if all(data != other.data for other in photos):  # different addresses, same file
                    photos.append(Photo(data, credit, full_size_url(url), photo_page(url)))
        return tuple(photos)

    # ---- pictures of your own ------------------------------------------------------------------

    def _custom_photo(self, hex_code: str, registration: str, type_code: str) -> Photo | None:
        if time.monotonic() - self._custom_seen >= CUSTOM_RESCAN_S:
            self._custom_seen = time.monotonic()
            try:
                self._custom = {
                    normalize(p.stem): p
                    for p in sorted(self._custom_dir.iterdir())
                    if p.suffix.lower() in IMAGE_SUFFIXES and p.is_file()
                }
            except OSError:
                self._custom = {}
        # The most particular match wins: this airframe, before any of its type.
        for name in (registration, hex_code, type_code):
            path = self._custom.get(normalize(name)) if name else None
            if path is None:
                continue
            try:
                if path.stat().st_size > MAX_CUSTOM_BYTES:
                    log.warning("%s is too big to use as a picture", path.name)
                    continue
                data = path.read_bytes()
            except OSError:
                continue
            if data.startswith(_IMAGE_SIGNATURES):
                return Photo(data, CUSTOM_CREDIT)
            log.warning("%s is not a JPEG or PNG; ignoring it", path.name)
        return None

    # ---- disk ---------------------------------------------------------------------------------

    def _photo_path(self, hex_code: str, index: int) -> Path:
        return self._photo_dir / f"{hex_code}-{index}.img"

    def _stored_photos(self, hex_code: str, fresh_only: bool = False) -> tuple[Photo, ...]:
        """The photos kept on disk for an aircraft. fresh_only leaves out the lot if they
        were fetched more than thirty days ago."""
        found = []
        for index in range(MAX_PHOTOS):
            path = self._photo_path(hex_code, index)
            try:
                raw = path.read_bytes()
                if fresh_only and time.time() - path.stat().st_mtime > MAX_AGE_S:
                    return ()
            except OSError:
                break
            head, _, data = raw.partition(b"\n")
            if not data.startswith(_IMAGE_SIGNATURES):
                break
            credit, full, page, *_ = [*head.decode("utf-8", "replace").split("\t"), "", ""]
            found.append(Photo(data, credit[:60], allowed_photo_url(full), page[:80]))
        return tuple(found)

    def _store_photo(self, hex_code: str, index: int, photo: Photo) -> None:
        try:
            self._photo_dir.mkdir(parents=True, exist_ok=True)
            path = self._photo_path(hex_code, index)
            tmp = path.with_suffix(".tmp")
            head = f"{photo.credit}\t{photo.full_url}\t{photo.page}".replace("\n", " ")
            tmp.write_bytes(head.encode("utf-8") + b"\n" + photo.data)
            tmp.replace(path)
        except OSError as exc:
            log.warning("could not keep the photo of %s: %s", hex_code, exc)

    def _load(self) -> None:
        try:
            raw = json.loads((self._dir / "known.json").read_text())
            for kind, key, when, answer in raw:
                if kind == "route" and isinstance(answer, dict):
                    answer = Route(
                        answer["airline"],
                        Place(**answer["origin"]),
                        Place(**answer["destination"]),
                    )
                elif kind == "airframe" and isinstance(answer, dict):
                    answer = Airframe(**answer)
                elif not isinstance(answer, str) or answer == FAILED:
                    continue
                self._known[(str(kind), str(key))] = (float(when), answer)
        except FileNotFoundError:
            pass
        except (OSError, ValueError, TypeError, KeyError) as exc:
            log.warning("ignoring unreadable enrichment cache: %s", exc)
            self._known.clear()
        try:  # no more photos on disk than the cap: the oldest go
            files = sorted(self._photo_dir.glob("*.img"), key=lambda p: p.stat().st_mtime)
            for path in files[: max(len(files) - MAX_KEPT, 0)]:
                path.unlink(missing_ok=True)
        except OSError:
            pass

    def save(self) -> None:
        """Write the facts to disk if any are new. Called now and then by the worker, and on
        the way out."""
        with self._lock:
            if not self._dirty:
                return
            self._dirty = False
            self._saved_at = self._clock()
            rows = [
                [kind, key, when, asdict(answer) if not isinstance(answer, str) else answer]
                for (kind, key), (when, answer) in self._known.items()
                if answer != FAILED and kind != "full"
            ]
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            tmp = self._dir / "known.tmp"
            tmp.write_text(json.dumps(rows))
            tmp.replace(self._dir / "known.json")
        except OSError as exc:
            log.warning("could not save the enrichment cache: %s", exc)
