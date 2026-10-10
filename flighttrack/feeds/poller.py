"""Background thread that polls one feed and publishes results to the store."""

import logging
import threading
import time
from collections.abc import Callable

from flighttrack.feeds.base import Feed
from flighttrack.feeds.coverage import QueryPlan
from flighttrack.state import Store

log = logging.getLogger(__name__)

MAX_BACKOFF_S = 60.0
# Never ask a provider more often than this, however impatient the UI is.
MIN_SPACING_S = 1.1


class FeedPoller(threading.Thread):
    def __init__(
        self,
        feed: Feed,
        store: Store,
        plan: Callable[[], QueryPlan],
        initial_delay_s: float = 0.0,
    ) -> None:
        """plan returns what to ask the feed for and how often; it is re-read before every
        request, so it follows the range the user has selected. initial_delay_s holds back
        the first request, for a poller that replaces one which may have only just asked."""
        super().__init__(name=f"feed-{feed.name}", daemon=True)
        self._feed = feed
        self._store = store
        self._plan = plan
        self._interval_s = 5.0
        self._initial_delay_s = initial_delay_s
        self._failures = 0
        self._stop_event = threading.Event()
        self._wake_event = threading.Event()

    def poll_once(self) -> None:
        plan = self._plan()
        self._interval_s = plan.interval_s
        try:
            aircraft = self._feed.fetch(plan.lat, plan.lon, plan.radius_nm)
        except Exception as exc:  # a feed failure must never kill the thread
            if self._failures == 0:
                log.warning("feed %s failed: %s", self._feed.name, exc)
            self._failures += 1
            self._store.set_error(self._feed.name, str(exc))
        else:
            if self._failures:
                log.info("feed %s recovered after %d failures", self._feed.name, self._failures)
            self._failures = 0
            # Which provider answered (a fallback feed's, or the watch's): the coverage map
            # is kept for each.
            heard_by = getattr(self._feed, "attribution", None) or self._feed.name
            self._store.set_aircraft(self._feed.name, aircraft, time.monotonic(), heard_by)

    def next_wait_s(self) -> float:
        """Normal interval, doubling with each consecutive failure so we don't hammer a
        provider that is down or rate-limiting us."""
        return min(self._interval_s * 2 ** min(self._failures, 10), MAX_BACKOFF_S)

    def run(self) -> None:
        self._stop_event.wait(self._initial_delay_s)
        while not self._stop_event.is_set():
            started = time.monotonic()
            self.poll_once()
            wait_s = self.next_wait_s()
            if self._wake_event.wait(wait_s):
                # Woken early: still keep the minimum spacing between requests.
                spacing = min(MIN_SPACING_S, wait_s)
                self._stop_event.wait(max(0.0, spacing - (time.monotonic() - started)))
            self._wake_event.clear()

    def wake(self) -> None:
        """Poll again soon instead of waiting out the interval (the plan has changed)."""
        self._wake_event.set()

    def stop(self) -> None:
        self._stop_event.set()
        self._wake_event.set()
