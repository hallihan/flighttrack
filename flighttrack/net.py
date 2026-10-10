"""Every request to a public service goes through here, so the display is a polite client.

One limiter, shared by every thread, keeps a minimum gap between requests to each host: the
area poller, the watch, flight lookups, enrichment, weather and map tiles all take their
turn per host, rather than each keeping only its own pace. A caller that would come too soon
waits (it is always on a worker thread). Hosts on the local network (a receiver) are not
limited.
"""

import ipaddress
import threading
import time
from collections.abc import Callable, Iterable
from datetime import date
from typing import Any
from urllib.parse import urlsplit

# The gap between two requests to one host, in seconds. adsb.fi documents one request a
# second; the others ask only for reasonable use, which this is.
SPACING_S: dict[str, float] = {
    "opendata.adsb.fi": 1.1,
    "api.adsb.lol": 1.0,
    "adsb.lol": 1.0,
    "api.adsbdb.com": 1.0,
    "www.airport-data.com": 1.0,
    "airport-data.com": 1.0,
    "aviationweather.gov": 1.0,
    "ipinfo.io": 1.0,
    # With a key (keyed.py):
    "opensky-network.org": 1.0,
    "auth.opensky-network.org": 1.0,
    "aeroapi.flightaware.com": 6.0,  # the Personal tier allows 10 a minute
    "adsbexchange-com1.p.rapidapi.com": 1.0,
}
DEFAULT_SPACING_S = 1.0  # any other public host
# Map and weather tile servers (content delivery networks) serve many small images: a view
# needs dozens at once. Still spaced, so a burst is spread out rather than fired all at once.
TILE_SPACING_S = 0.05


def _is_local(host: str) -> bool:
    name = host.split(":")[0].strip("[]").lower()
    if name == "localhost" or name.endswith(".local") or "." not in name:
        return True
    try:
        address = ipaddress.ip_address(name)
    except ValueError:
        return False
    return address.is_private or address.is_loopback or address.is_link_local


class RateLimiter:
    def __init__(
        self,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.enabled = True
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()
        self._next: dict[str, float] = {}  # host -> the earliest its next request may go
        self.waited_s = 0.0  # in all, for diagnostics
        self._day = ""
        self._counts: dict[str, int] = {}  # host -> requests today, for the Feeds tab

    def count(self, url: str) -> None:
        """Count a request to url's host, for today (local hosts too: it is just a count)."""
        host = urlsplit(url).netloc.lower()
        with self._lock:
            today = date.today().isoformat()
            if today != self._day:
                self._day, self._counts = today, {}
            self._counts[host] = self._counts.get(host, 0) + 1

    def used_today(self, hosts: Iterable[str]) -> int:
        with self._lock:
            if self._day != date.today().isoformat():
                return 0
            return sum(self._counts.get(host, 0) for host in hosts)

    def wait(self, url: str, spacing_s: float | None = None) -> None:
        """Block until a request to url's host may go, and book that turn."""
        host = urlsplit(url).netloc.lower()
        if not self.enabled or not host or _is_local(host):
            return
        gap = spacing_s if spacing_s is not None else SPACING_S.get(host, DEFAULT_SPACING_S)
        with self._lock:
            now = self._clock()
            turn = max(now, self._next.get(host, now))
            self._next[host] = turn + gap
        if turn > now:
            self.waited_s += turn - now
            self._sleep(turn - now)


LIMITER = RateLimiter()


def get(client: Any, url: str, spacing_s: float | None = None, **kwargs: Any) -> Any:
    """client.get(url, **kwargs) (a requests Session, or the requests module), in its turn."""
    LIMITER.wait(url, spacing_s)
    LIMITER.count(url)
    return client.get(url, **kwargs)


def post(client: Any, url: str, spacing_s: float | None = None, **kwargs: Any) -> Any:
    """client.post(url, **kwargs), in its turn."""
    LIMITER.wait(url, spacing_s)
    LIMITER.count(url)
    return client.post(url, **kwargs)
