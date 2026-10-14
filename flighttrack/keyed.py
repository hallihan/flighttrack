"""The services that need a key, used when one is given in Settings, Feeds.

- OpenSky Network (a free account; OAuth2 client credentials): the selected aircraft's
  current flight so far (tracks/all, experimental, 4 credits), when adsb.lol has no trace of
  it; and where an aircraft last landed (flights/aircraft, the last two days, filled in by a
  nightly batch, so yesterday and before), for finding a flight that is not live. A free
  account has 4,000 credits a day per kind of request.
- FlightAware AeroAPI (Personal tier, a key): the selected aircraft's actual flight, its
  origin, destination and operator (flights/{ident}), in place of adsbdb's expected route.
  $0.005 a request against a free $5 a month ($10 for feeders), so at most AEROAPI_DAILY a
  day, each answer kept AEROAPI_KEEP_S, and the Personal tier's 10 a minute (net.SPACING_S).
- ADS-B Exchange (a Community API key, through RapidAPI): positions for the area, only when
  adsb.fi and adsb.lol both fail, within its 10,000 requests a month (ADSBX_DAILY a day).

Each has a Usage: requests today against its cap, and how the last went, for the Feeds tab.
Everything here may block: worker threads only.
"""

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import requests

from flighttrack import airports, net
from flighttrack.feeds.base import Aircraft
from flighttrack.feeds.http import USER_AGENT, plain_error

OPENSKY_TOKEN_URL = (
    "https://auth.opensky-network.org/auth/realms/opensky-network/protocol/openid-connect/token"
)
OPENSKY_API = "https://opensky-network.org/api"
OPENSKY_DAILY = 1000  # requests of each kind: well inside 4,000 credits
AEROAPI = "https://aeroapi.flightaware.com/aeroapi"
AEROAPI_DAILY = 30  # 30 x $0.005 x 30 days = $4.50, inside the free $5 a month
AEROAPI_KEEP_S = 30 * 60
ADSBX_HOST = "adsbexchange-com1.p.rapidapi.com"
ADSBX_URL = f"https://{ADSBX_HOST}/v2/lat/{{lat}}/lon/{{lon}}/dist/{{dist}}/"
ADSBX_DAILY = 320  # 10,000 a month, a little to spare
METERS_TO_FEET = 3.28084
_TIMEOUT = (4.0, 10.0)


def _wall_time(text: object) -> float | None:
    """Seconds since the epoch from an ISO 8601 time such as AeroAPI gives, or None."""
    if not isinstance(text, str) or not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


class OverBudget(Exception):
    """Today's requests to a service are used up."""


class Usage:
    """How much of a service has been used today, and how the last request went."""

    def __init__(self, daily_cap: int, wall: Callable[[], float] = time.time) -> None:
        self.daily_cap = daily_cap
        self._wall = wall
        self._lock = threading.Lock()
        self._day = ""
        self.used = 0
        self.last_at: float | None = None  # wall-clock time of the last answer
        self.last_error: str | None = None

    def take(self) -> None:
        """Count a request about to be made, or raise OverBudget if today's are gone."""
        with self._lock:
            today = date.fromtimestamp(self._wall()).isoformat()
            if today != self._day:
                self._day, self.used = today, 0
            if self.used >= self.daily_cap:
                raise OverBudget(f"today's {self.daily_cap} requests are used up")
            self.used += 1

    def answered(self, error: str | None = None) -> None:
        self.last_at, self.last_error = self._wall(), error

    def lines(self) -> list[tuple[str, bool, bool]]:
        """For the settings pages: (words, good, bad), as for the receiver."""
        if self.last_at is None:
            return [(f"Not asked yet. At most {self.daily_cap} requests a day.", False, False)]
        clock = time.strftime("%H:%M", time.localtime(self.last_at))
        if self.last_error:
            head = (f"Last asked {clock}: failed. {self.last_error}", False, True)
        else:
            head = (f"Last asked {clock}: OK.", True, False)
        return [head, (f"{self.used} of {self.daily_cap} requests today.", False, False)]


def _describe_error(exc: Exception) -> str:
    response = getattr(exc, "response", None)
    code = getattr(response, "status_code", None)
    try:
        oauth = response.json().get("error") if response is not None else None
    except (ValueError, AttributeError):
        oauth = None
    if oauth == "invalid_client":
        return (
            "OpenSky did not accept the client ID and secret together: check both against "
            "your OpenSky account page."
        )
    if code in (401, 403):
        return "The key was refused."
    if code == 429:
        return "Asked too often: the service's limit is reached."
    return plain_error(str(exc))


class OpenSky:
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        session: requests.Session | None = None,
        wall: Callable[[], float] = time.time,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.credentials = (client_id, client_secret)
        self._session = session or requests.Session()
        self._session.headers["User-Agent"] = USER_AGENT
        self._wall, self._clock = wall, clock
        self._token: tuple[str, float] | None = None  # (token, monotonic expiry)
        self._lock = threading.Lock()
        self.usage = Usage(OPENSKY_DAILY, wall)

    def _bearer(self, fresh: bool = False) -> str:
        with self._lock:
            if fresh or self._token is None or self._clock() > self._token[1]:
                client_id, secret = self.credentials
                response = net.post(
                    self._session,
                    OPENSKY_TOKEN_URL,
                    data={
                        "grant_type": "client_credentials",
                        "client_id": client_id,
                        "client_secret": secret,
                    },
                    timeout=_TIMEOUT,
                )
                response.raise_for_status()
                answer = response.json()
                life = float(answer.get("expires_in", 1800))
                self._token = (answer["access_token"], self._clock() + max(life - 60, 30))
            return self._token[0]

    def _get(self, path: str, params: dict[str, Any]) -> Any | None:
        """The JSON answer, or None for 404 (OpenSky's "nothing"). Raises on failure."""
        self.usage.take()
        try:
            for attempt in range(2):
                headers = {"Authorization": f"Bearer {self._bearer(fresh=attempt > 0)}"}
                response = net.get(
                    self._session,
                    OPENSKY_API + path,
                    params=params,
                    headers=headers,
                    timeout=_TIMEOUT,
                )
                if response.status_code == 401 and attempt == 0:
                    continue  # the token ran out: a new one, once
                if response.status_code == 404:
                    self.usage.answered()
                    return None
                response.raise_for_status()
                self.usage.answered()
                return response.json()
        except (requests.RequestException, ValueError, KeyError) as exc:
            self.usage.answered(_describe_error(exc))
            raise
        return None

    def track(self, hex_code: str) -> list[tuple[float, float, int | None, float]]:
        """The aircraft's current flight so far, as flight_trace gives it (lat, lon, altitude
        in feet or None on the ground, monotonic time), oldest first."""
        answer = self._get("/tracks/all", {"icao24": hex_code.lower(), "time": 0})
        path = answer.get("path") if isinstance(answer, dict) else None
        if not isinstance(path, list):
            return []
        offset = self._clock() - self._wall()
        points = []
        for row in path:
            if not isinstance(row, list) or len(row) < 6:
                continue
            t, lat, lon, height, _, ground = row[:6]
            if not all(isinstance(v, int | float) for v in (t, lat, lon)):
                continue
            feet = None if ground or not isinstance(height, int | float) else height
            points.append(
                (lat, lon, None if feet is None else round(feet * METERS_TO_FEET), t + offset)
            )
        return points

    def last_landing(self, hex_code: str) -> Aircraft | None:
        """Where the aircraft last landed in the two days up to yesterday's batch: at its
        arrival airport, when it was last seen. None if OpenSky has no such flight."""
        now = int(self._wall())
        answer = self._get(
            "/flights/aircraft", {"icao24": hex_code.lower(), "begin": now - 2 * 86400, "end": now}
        )
        flights = [f for f in answer or [] if isinstance(f, dict)]
        flights = [f for f in flights if isinstance(f.get("lastSeen"), int)]
        if not flights:
            return None
        last = max(flights, key=lambda f: f["lastSeen"])
        airport = airports.find(last.get("estArrivalAirport"))
        if airport is None:
            return None
        callsign = (last.get("callsign") or "").strip() or None
        return Aircraft(
            hex_code.lower(),
            airport.lat,
            airport.lon,
            self._clock() - (self._wall() - last["lastSeen"]),
            callsign=callsign,
            on_ground=True,
        )


@dataclass(frozen=True)
class Flight:
    """What AeroAPI says of an aircraft's flight: who flies it, from where to where."""

    operator: str
    origin: tuple[str, str] | None  # code and city
    destination: tuple[str, str] | None
    status: str
    flight_id: str = ""  # AeroAPI's own id for the flight, for its track
    origin_icao: str = ""
    took_off: float | None = None  # wall-clock time it left the ground


LOADING = "loading"


class AeroApi:
    def __init__(
        self,
        key: str,
        session: requests.Session | None = None,
        wall: Callable[[], float] = time.time,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.key = key
        self._session = session or requests.Session()
        self._session.headers["User-Agent"] = USER_AGENT
        self._clock = clock
        self._wall = wall
        self._lock = threading.Lock()
        self._known: dict[str, tuple[float, Flight | None]] = {}
        self._pending: set[str] = set()
        self.usage = Usage(AEROAPI_DAILY, wall)

    def flight(self, ident: str) -> Flight | None | str:
        """Main thread: what is known of a flight (by callsign or registration): a Flight,
        None if AeroAPI knows none, or LOADING while it is asked, on a worker thread. An
        answer is kept AEROAPI_KEEP_S; a failure is asked again after a few minutes."""
        ident = ident.strip().upper()
        now = self._clock()
        with self._lock:
            known = self._known.get(ident)
            if known is not None and now < known[0]:
                return known[1]
            if ident not in self._pending:
                self._pending.add(ident)
                threading.Thread(target=self._ask, args=(ident,), daemon=True).start()
        return known[1] if known is not None else LOADING

    def _ask(self, ident: str) -> None:
        keep = AEROAPI_KEEP_S
        try:
            found = self.fetch(ident)
        except OverBudget:
            found, keep = None, 3600
        except (requests.RequestException, ValueError):
            found, keep = None, 300
        with self._lock:
            self._known[ident] = (self._clock() + keep, found)
            self._pending.discard(ident)

    def fetch(self, ident: str) -> Flight | None:
        """Ask AeroAPI (blocks). The flight under way, else the latest to have left."""
        self.usage.take()
        try:
            response = net.get(
                self._session,
                f"{AEROAPI}/flights/{requests.utils.quote(ident)}",
                headers={"x-apikey": self.key},
                timeout=_TIMEOUT,
            )
            if response.status_code == 404:
                self.usage.answered()
                return None
            response.raise_for_status()
            flights = response.json().get("flights") or []
        except (requests.RequestException, ValueError, AttributeError) as exc:
            self.usage.answered(_describe_error(exc))
            raise
        self.usage.answered()
        flights = [f for f in flights if isinstance(f, dict)]
        flying = [f for f in flights if f.get("actual_off") and not f.get("actual_on")]
        departed = [f for f in flights if f.get("actual_off")]
        chosen = flying[0] if flying else departed[0] if departed else None
        if chosen is None:
            return None

        def end(place: object) -> tuple[str, str] | None:
            if not isinstance(place, dict):
                return None
            code = place.get("code_iata") or place.get("code_icao") or place.get("code") or ""
            return (str(code), str(place.get("city") or "")) if code else None

        origin = chosen.get("origin") if isinstance(chosen.get("origin"), dict) else {}
        return Flight(
            str(chosen.get("operator") or ""),
            end(chosen.get("origin")),
            end(chosen.get("destination")),
            str(chosen.get("status") or ""),
            str(chosen.get("fa_flight_id") or ""),
            str(origin.get("code_icao") or ""),
            _wall_time(chosen.get("actual_off")),
        )

    def track(self, ident: str) -> list[tuple[float, float, int | None, float]]:
        """The flight an aircraft is on (by registration or callsign), as far as AeroAPI
        has it, oldest first, with monotonic times; it starts on the ground at the airport
        it took off from when that is known. Blocks: two requests, within the daily cap."""
        found = self.fetch(ident.strip().upper())
        if found is None:
            return []
        offset = self._clock() - self._wall()
        points: list[tuple[float, float, int | None, float]] = []
        airport = airports.find(found.origin_icao) if found.origin_icao else None
        if airport is not None and found.took_off is not None:
            points.append((airport.lat, airport.lon, None, found.took_off + offset))
        if not found.flight_id:
            return points
        self.usage.take()
        try:
            response = net.get(
                self._session,
                f"{AEROAPI}/flights/{requests.utils.quote(found.flight_id)}/track",
                headers={"x-apikey": self.key},
                timeout=_TIMEOUT,
            )
            response.raise_for_status()
            positions = response.json().get("positions") or []
        except (requests.RequestException, ValueError, AttributeError) as exc:
            self.usage.answered(_describe_error(exc))
            raise
        self.usage.answered()
        for row in positions:
            if not isinstance(row, dict):
                continue
            lat, lon, when = (
                row.get("latitude"),
                row.get("longitude"),
                _wall_time(row.get("timestamp")),
            )
            if not isinstance(lat, int | float) or not isinstance(lon, int | float) or when is None:
                continue
            hundreds = row.get("altitude")
            feet = (
                round(hundreds * 100)
                if isinstance(hundreds, int | float) and hundreds > 0
                else None
            )
            points.append((float(lat), float(lon), feet, when + offset))
        return sorted(points, key=lambda p: p[3])


class AdsbxFeed:
    """ADS-B Exchange's positions for an area, readsb JSON, within a daily budget."""

    name = "adsbx"
    attribution = "ADS-B Exchange"

    def __init__(self, key: str, session: requests.Session | None = None) -> None:
        from flighttrack.feeds.http import ReadsbHttpFeed

        self._feed = ReadsbHttpFeed(
            "adsbx",
            ADSBX_URL,
            attribution=self.attribution,
            session=session,
            headers={"x-rapidapi-key": key, "x-rapidapi-host": ADSBX_HOST},
        )
        self.usage = Usage(ADSBX_DAILY)

    def fetch(self, lat: float, lon: float, radius_nm: float) -> list[Aircraft]:
        self.usage.take()
        try:
            found = self._feed.fetch(lat, lon, min(radius_nm, 250.0))
        except (requests.RequestException, ValueError) as exc:
            self.usage.answered(_describe_error(exc))
            raise
        self.usage.answered()
        return found


# Every service the display asks, for the running count on the Feeds tab: its name, its hosts,
# and what it allows where that is known (as of October 2026; check before relying on it).
PROVIDERS: tuple[tuple[str, tuple[str, ...], str], ...] = (
    ("adsb.fi", ("opendata.adsb.fi",), "a request a second"),
    ("adsb.lol", ("api.adsb.lol", "adsb.lol"), "no published limit"),
    ("OpenSky Network", ("opensky-network.org", "auth.opensky-network.org"),
     f"4,000 credits a day with a free account; {OPENSKY_DAILY:,} a day here"),
    ("FlightAware AeroAPI", ("aeroapi.flightaware.com",),
     f"$5 a month free, 10 a minute; {AEROAPI_DAILY} a day here"),
    ("ADS-B Exchange", (ADSBX_HOST,), f"10,000 a month; {ADSBX_DAILY} a day here"),
    ("adsbdb.com", ("api.adsbdb.com",), "no published limit"),
    ("airport-data.com", ("airport-data.com", "www.airport-data.com", "image.airport-data.com"),
     "no published limit"),
    ("Aviation Weather Center", ("aviationweather.gov",), "no published limit"),
    ("ipinfo.io", ("ipinfo.io",), "no published limit"),
    ("Esri maps", ("server.arcgisonline.com", "services.arcgisonline.com"),
     "no published limit for this use"),
    ("FAA charts and airspace", ("tiles.arcgis.com", "services6.arcgis.com"),
     "no published limit"),
    ("Iowa Mesonet radar", ("mesonet.agron.iastate.edu",), "no published limit"),
    ("RainViewer", ("api.rainviewer.com", "tilecache.rainviewer.com"), "no published limit"),
    ("NASA GIBS clouds", ("gibs.earthdata.nasa.gov",), "no published limit"),
    ("Open-Meteo elevation", ("api.open-meteo.com",), "10,000 a day; once a minute at most here"),
)  # fmt: skip


def usage_lines() -> list[str]:
    """One line a service: its requests today, and what it allows."""
    return [
        f"{name}: {net.LIMITER.used_today(hosts):,} today ({allows})"
        for name, hosts, allows in PROVIDERS
    ]
