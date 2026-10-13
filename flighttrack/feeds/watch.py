"""A watch on chosen aircraft, wherever they are.

The area poller asks only about the view, so an aircraft drops out of the feed the moment the
map is dragged away from it. This asks the public feed about particular aircraft by hex code,
all in one request: the selected one, and the favorites. A favorite written as a hex code is
used as it is; one written as a registration is turned into a hex code with adsbdb.com (a few
each round, remembered while the display runs); a callsign cannot be, so a favorite callsign
is watched once the display has seen an aircraft flying with it. The answer joins the store
as its own source, merged with the rest like any other.

adsb.lol is asked first and adsb.fi only if it fails, so the watch does not take turns from the
area poller, which asks adsb.fi first and may do so once a second.
"""

import logging
import re
import threading
import time
from collections.abc import Callable

import requests

from flighttrack import favorites as favorites_module
from flighttrack import net
from flighttrack.feeds.base import Aircraft
from flighttrack.feeds.history import hex_for
from flighttrack.feeds.http import USER_AGENT
from flighttrack.feeds.readsb import parse_response
from flighttrack.state import Store

log = logging.getLogger(__name__)

WATCH = "watch"  # the source's name in the store
WATCH_INTERVAL_S = 10.0
MAX_WATCHED = 100  # hex codes in one request
RESOLVES_PER_ROUND = 3  # registrations looked up for their hex codes, each round
SEEN_FLYING_S = 3 * 3600.0  # a favorite callsign is watched this long after it was seen flying
URLS = (
    ("adsb.lol", "https://api.adsb.lol/v2/hex/{}"),
    ("adsb.fi", "https://opendata.adsb.fi/api/v2/hex/{}"),
)
_HEX = re.compile(r"[0-9a-f]{6}")


class Watch:
    """A Feed (for FeedPoller) whose answer is the chosen aircraft, wherever they are."""

    name = WATCH

    def __init__(
        self,
        store: Store,
        session: requests.Session | None = None,
        resolve: Callable[[str], str | None] | None = None,
        clock: Callable[[], float] = time.monotonic,
        pause: Callable[[float], None] = time.sleep,
    ) -> None:
        self.attribution: str | None = None
        self._store = store
        self._session = session or requests.Session()
        self._session.headers["User-Agent"] = USER_AGENT
        self._resolve = resolve or (lambda text: hex_for(text, self._session))
        self._clock = clock
        self._pause = pause
        self._lock = threading.Lock()
        self._selected: str | None = None
        self._favorites: tuple[str, ...] = ()
        self._resolved: dict[str, str | None] = {}  # favorite as written -> hex, None if none

    def set(self, selected: str | None, favorites: list[str]) -> bool:
        """Main thread: what to watch. True if that changed."""
        with self._lock:
            changed = (selected, tuple(favorites)) != (self._selected, self._favorites)
            self._selected, self._favorites = selected, tuple(favorites)
        return changed

    def hexes(self) -> list[str]:
        """Worker thread: the hex codes to ask about, the selected aircraft first. Looks a
        few favorites up for their hex codes on the way, so may block."""
        with self._lock:
            selected, wanted = self._selected, self._favorites
        unknown = [e for e in wanted if e not in self._resolved]
        for index, entry in enumerate(unknown[:RESOLVES_PER_ROUND]):
            if index:
                self._pause(1.0)
            plain = favorites_module.normalize(entry).lower()
            if _HEX.fullmatch(plain):
                self._resolved[entry] = plain
                continue
            try:
                self._resolved[entry] = self._resolve(entry)
            except requests.RequestException as exc:
                log.info("watch: could not look %s up: %s", entry, exc)
                break  # tried again next round
        found = [selected] if selected else []
        found += [h for e in wanted if (h := self._resolved.get(e))]
        # Favorites known only by callsign: watched once seen flying.
        keys = favorites_module.keys(list(wanted))
        if keys:
            now = self._clock()
            for a in self._store.remembered():
                recent = now - a.seen_at <= SEEN_FLYING_S
                if recent and not a.on_ground and favorites_module.is_favorite(a, keys):
                    found.append(a.hex)
        return list(dict.fromkeys(found))[:MAX_WATCHED]

    def fetch(self, lat: float, lon: float, radius_nm: float) -> list[Aircraft]:
        """The chosen aircraft that are being tracked now. No request at all if there are
        none to ask about. Raises if every service fails."""
        hexes = self.hexes()
        if not hexes:
            return []
        trouble: Exception | None = None
        for name, template in URLS:
            try:
                url = template.format(",".join(hexes))
                response = net.get(self._session, url, timeout=(4.0, 8.0))
                response.raise_for_status()
                found = parse_response(response.json(), self._clock())
            except (requests.RequestException, ValueError) as exc:
                trouble = exc
                continue
            self.attribution = name
            return found
        assert trouble is not None
        raise trouble
