"""Live feeds over HTTP: the public ADS-B APIs and a receiver on the local network.

All of them return readsb-style JSON, so one class covers them.
"""

import logging
import time
from collections.abc import Callable, Sequence
from math import ceil

import requests

from flighttrack import __version__, net
from flighttrack.feeds.base import Aircraft, Feed
from flighttrack.feeds.readsb import parse_response

log = logging.getLogger(__name__)

USER_AGENT = f"flighttrack/{__version__} (hobby air-traffic display)"

# (name shown as attribution, URL template). Tried in order; both are free and need no key.
# adsb.fi allows one request per second, personal non-commercial use, attribution required.
PUBLIC_PROVIDERS: tuple[tuple[str, str], ...] = (
    ("adsb.fi", "https://opendata.adsb.fi/api/v3/lat/{lat}/lon/{lon}/dist/{dist}"),
    ("adsb.lol", "https://api.adsb.lol/v2/lat/{lat}/lon/{lon}/dist/{dist}"),
)

LOCAL_INTERVAL_S = 1.0


class ReadsbHttpFeed:
    def __init__(
        self,
        name: str,
        url_template: str,
        *,
        attribution: str | None = None,
        timeout: tuple[float, float] = (4.0, 8.0),
        session: requests.Session | None = None,
        clock: Callable[[], float] = time.monotonic,
        headers: dict[str, str] | None = None,
    ) -> None:
        """headers: sent with every request, as a key some services want."""
        self.name = name
        self.attribution = attribution
        self._headers = headers or {}
        self._url_template = url_template
        self._timeout = timeout
        self._session = session or requests.Session()
        self._session.headers["User-Agent"] = USER_AGENT
        self._clock = clock

    def url(self, lat: float, lon: float, radius_nm: float) -> str:
        return (
            self._url_template.replace("{lat}", f"{lat:.4f}")
            .replace("{lon}", f"{lon:.4f}")
            .replace("{dist}", str(ceil(radius_nm)))
        )

    def fetch(self, lat: float, lon: float, radius_nm: float) -> list[Aircraft]:
        extra = {"headers": self._headers} if self._headers else {}
        response = net.get(
            self._session, self.url(lat, lon, radius_nm), timeout=self._timeout, **extra
        )
        response.raise_for_status()
        return parse_response(response.json(), self._clock())


class FallbackFeed:
    """Tries each feed in order and returns the first answer."""

    def __init__(self, name: str, feeds: Sequence[Feed]) -> None:
        if not feeds:
            raise ValueError("FallbackFeed needs at least one feed")
        self.name = name
        self._feeds = feeds
        self.attribution: str | None = getattr(feeds[0], "attribution", None)

    def fetch(self, lat: float, lon: float, radius_nm: float) -> list[Aircraft]:
        last_error: Exception | None = None
        for feed in self._feeds:
            try:
                aircraft = feed.fetch(lat, lon, radius_nm)
            except Exception as exc:
                log.warning("feed %s failed: %s", feed.name, exc)
                last_error = exc
            else:
                self.attribution = getattr(feed, "attribution", None)
                return aircraft
        assert last_error is not None
        raise last_error


def public_feed(extra: Sequence[Feed] = ()) -> FallbackFeed:
    """The public providers in turn, then any extra (ADS-B Exchange, with a key) last."""
    return FallbackFeed(
        "public",
        [ReadsbHttpFeed(name, url, attribution=name) for name, url in PUBLIC_PROVIDERS]
        + list(extra),
    )


LOCAL = "local"  # the name of the local receiver's feed, as a source in the store


def plain_error(message: str) -> str:
    """What went wrong with a request, in a few plain words. The libraries' own messages
    run to several lines of class names; this is for showing to the person who typed the
    address."""
    text = message or ""
    lowered = text.lower()

    def has(*phrases: str) -> bool:
        return any(phrase in lowered for phrase in phrases)

    if has("nameresolution", "failed to resolve", "name or service"):
        return "That name was not found on the network."
    if has("connection refused"):
        return "Nothing is answering at that address (connection refused)."
    if has("timed out", "timeout"):
        return "No answer in time."
    if has("no route to host", "network is unreachable"):
        return "That address cannot be reached from here."
    if has("ssl", "certificate"):
        return "The secure connection failed."
    if has("client error", "server error"):
        meanings = {
            "404": "nothing at that address",
            "401": "not allowed",
            "403": "not allowed",
            "429": "asked too often",
        }
        for code in ("401", "403", "404", "429", "500", "502", "503"):
            if code in text:
                meaning = meanings.get(code, "the receiver had a problem")
                return f"The receiver answered with an error: {code} ({meaning})."
    if has("json", "expecting value", "unexpected"):
        return "It answered, but not with aircraft.json."
    return (text[:90] + "...") if len(text) > 90 else (text or "It failed.")


def local_feed(url: str) -> ReadsbHttpFeed:
    return ReadsbHttpFeed(LOCAL, url, timeout=(2.0, 3.0))


# Looking one aircraft up, wherever in the world it is. Same service, same limits.
_LOOKUP = "https://opendata.adsb.fi/api/v2"
_LOOKUP_SPACING_S = 1.1


def flight_lookups(text: str) -> list[str]:
    """The URLs to try, in order, for something typed as a flight: it may be a callsign, a
    registration or a hex code, and nothing says which."""
    query = "".join(ch for ch in text.upper() if ch.isalnum() or ch == "-")
    plain = query.replace("-", "")
    if len(plain) < 3:
        return []
    urls = [f"{_LOOKUP}/callsign/{plain}", f"{_LOOKUP}/registration/{query}"]
    if len(plain) == 6 and all(ch in "0123456789ABCDEF" for ch in plain):
        urls.insert(0, f"{_LOOKUP}/hex/{plain.lower()}")
    return urls


def find_flight(
    text: str,
    session: requests.Session | None = None,
    clock: Callable[[], float] = time.monotonic,
    pause: Callable[[float], None] = time.sleep,
) -> Aircraft | None:
    """Find an aircraft by callsign, registration or hex among everything the public feed is
    tracking. Makes up to three requests a second apart, so call it from a worker thread.
    None if it is not in the air (or not being received) just now. Raises on network failure."""
    session = session or requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    for index, url in enumerate(flight_lookups(text)):
        if index:
            pause(_LOOKUP_SPACING_S)
        response = net.get(session, url, timeout=(4.0, 8.0))
        response.raise_for_status()
        found = parse_response(response.json(), clock())
        if found:
            return found[0]
    return None
