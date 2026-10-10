"""Thread-safe state shared between worker threads and the UI."""

import threading
import time
from collections import OrderedDict, deque
from collections.abc import Callable
from dataclasses import dataclass, field

from flighttrack import legs
from flighttrack.feeds.base import Aircraft
from flighttrack.feeds.merge import merge
from flighttrack.geo import distance_nm
from flighttrack.legs import TimedPoint

# One point of an aircraft's track: where it was, and how high.
TrackPoint = tuple[float, float, int | None]
MAX_TRACK_POINTS = 600
# A new track point is recorded once the aircraft has moved this far.
MIN_TRACK_STEP_NM = 0.05
# The last report of this many aircraft is remembered after they leave the feed, so that a
# flight can be found where it was last seen (the public feeds keep no history).
MAX_REMEMBERED = 5000
# Track from before what the display is recording now (fetched for the selected aircraft,
# kept from before a restart, or recorded but pushed out of the recording): at most this
# many points, thinned evenly, keeping the first (where it took off) and the last.
MAX_PAST_POINTS = 900


@dataclass(frozen=True, slots=True)
class SourceStatus:
    count: int = 0  # aircraft in the last successful fetch
    updated_at: float | None = None  # monotonic time of the last successful fetch
    error: str | None = None  # set while the most recent fetch failed
    checked_at: float | None = None  # monotonic time of the last answer, good or bad


# Sources that answer about particular aircraft, not about the area on the map: the watch on
# chosen aircraft and a flight just looked up. They must not make the area's data look fresh.
AUXILIARY_SOURCES = frozenset({"watch", "search"})


@dataclass(frozen=True, slots=True)
class FeedSnapshot:
    aircraft: tuple[Aircraft, ...] = ()  # all sources merged, one entry per airframe
    sources: dict[str, SourceStatus] = field(default_factory=dict)

    def _area_sources(self) -> list[SourceStatus]:
        main = [s for name, s in self.sources.items() if name not in AUXILIARY_SOURCES]
        return main or list(self.sources.values())

    @property
    def updated_at(self) -> float | None:
        """Most recent successful fetch from any source about the area (not the watch)."""
        times = [s.updated_at for s in self._area_sources() if s.updated_at is not None]
        return max(times, default=None)

    @property
    def all_failing(self) -> bool:
        area = self._area_sources()
        return bool(area) and all(s.error for s in area)


class Store:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by_source: dict[str, tuple[Aircraft, ...]] = {}
        self._status: dict[str, SourceStatus] = {}
        self._snapshot = FeedSnapshot()
        self._tracks: dict[str, deque[TrackPoint]] = {}
        # Tracks as last handed out, so that asking again costs nothing until one grows.
        self._track_copies: dict[str, tuple[TrackPoint, ...]] = {}
        # Aircraft whose tracks outlast them in the feed: the selected one, and those pinned.
        self._kept: frozenset[str] = frozenset()
        # The latest report of every aircraft seen, most recently seen last.
        self._remembered: OrderedDict[str, Aircraft] = OrderedDict()
        # When each recorded point was heard, alongside the track.
        self._stamps: dict[str, deque[float]] = {}
        # What came before the recording, with times.
        self._past: dict[str, list[TimedPoint]] = {}
        # Tracks kept from before the display restarted, joined on when their aircraft
        # are heard again.
        self._saved: dict[str, list[TimedPoint]] = {}
        # Called with every answer, outside the lock, on the thread that gave it:
        # (provider, aircraft, time). The App learns how low each provider hears from it.
        self.on_answer: Callable[[str, list[Aircraft], float], None] | None = None

    def set_aircraft(
        self, source: str, aircraft: list[Aircraft], now: float, heard_by: str | None = None
    ) -> None:
        """A source's answer. heard_by is the provider that gave it, if not the source
        itself (a feed that falls back from one to another): handed on to on_answer."""
        with self._lock:
            self._by_source[source] = tuple(aircraft)
            self._status[source] = SourceStatus(len(aircraft), now, None, now)
            self._publish()
        if self.on_answer is not None:
            self.on_answer(heard_by or source, aircraft, now)

    def set_error(self, source: str, message: str, now: float | None = None) -> None:
        """Record a failed fetch. The source's last good aircraft are kept until they go stale."""
        with self._lock:
            previous = self._status.get(source, SourceStatus())
            when = time.monotonic() if now is None else now
            self._status[source] = SourceStatus(previous.count, previous.updated_at, message, when)
            self._publish()

    def _publish(self) -> None:
        merged = tuple(merge(self._by_source.values()))
        self._snapshot = FeedSnapshot(merged, dict(self._status))
        self._extend_tracks(merged)
        remembered = self._remembered
        for a in merged:
            remembered[a.hex] = a
            remembered.move_to_end(a.hex)
        while len(remembered) > MAX_REMEMBERED:
            remembered.popitem(last=False)

    def _extend_tracks(self, aircraft: tuple[Aircraft, ...]) -> None:
        """Remember where every aircraft has been, so a track is there the moment one is
        selected. Tracks are dropped when their aircraft leaves the feed, unless kept."""
        current = set()
        for a in aircraft:
            current.add(a.hex)
            track = self._tracks.get(a.hex)
            point = (a.lat, a.lon, None if a.on_ground else a.altitude_ft)
            if track is None:
                self._tracks[a.hex] = deque([point], maxlen=MAX_TRACK_POINTS)
                self._stamps[a.hex] = deque([a.seen_at], maxlen=MAX_TRACK_POINTS)
                saved = self._saved.pop(a.hex, None)
                if saved:
                    self._join_past(a.hex, saved)
                continue
            if distance_nm(track[-1][0], track[-1][1], a.lat, a.lon) < MIN_TRACK_STEP_NM:
                continue
            stamps = self._stamps[a.hex]
            if len(track) == MAX_TRACK_POINTS:
                # Full: the oldest point moves to the past, so a long flight keeps its start.
                past = self._past.setdefault(a.hex, [])
                past.append((*track[0], stamps[0]))
                if len(past) > MAX_PAST_POINTS * 3 // 2:
                    self._past[a.hex] = _thinned(past)
            track.append(point)
            stamps.append(max(a.seen_at, stamps[-1]))
            self._track_copies.pop(a.hex, None)
        self._drop_tracks(current)

    def _drop_tracks(self, current: set[str]) -> None:
        for hex_code in self._tracks.keys() - current - self._kept:
            del self._tracks[hex_code]
            self._track_copies.pop(hex_code, None)
            self._stamps.pop(hex_code, None)
            self._past.pop(hex_code, None)

    def keep_tracks(self, hex_codes: frozenset[str]) -> None:
        """Keep these aircraft's tracks when they leave the feed, and stop keeping any others
        (whose tracks then go, unless their aircraft are still in the feed)."""
        with self._lock:
            if hex_codes != self._kept:
                self._kept = hex_codes
                self._drop_tracks({a.hex for a in self._snapshot.aircraft})

    def set_past(self, hex_code: str, points: list[TimedPoint]) -> None:
        """Where an aircraft was before (lat, lon, altitude, monotonic time), from anywhere:
        merged with what is already known of its past, and the part of it that is the same
        flight as what the display is recording goes in front of its track. What is from an
        earlier flight is left out (legs.py decides; the services' traces can be hours
        behind, and a hole in one may be a landing nobody saw)."""
        with self._lock:
            if hex_code in self._tracks:
                self._join_past(hex_code, points)

    def _join_past(self, hex_code: str, points: list[TimedPoint]) -> None:
        recorded = self._recorded(hex_code)
        start = recorded[0][3]
        before = legs.merged(self._past.get(hex_code, []), [p for p in points if p[3] < start])
        first = legs.flight_start(before + recorded, len(before))
        self._past[hex_code] = _thinned(before[first:])
        self._track_copies.pop(hex_code, None)

    def _recorded(self, hex_code: str) -> list[TimedPoint]:
        return [
            (*p, t) for p, t in zip(self._tracks[hex_code], self._stamps[hex_code], strict=True)
        ]

    def history(self, hex_code: str) -> list[TimedPoint]:
        """Everything known of where an aircraft has been, with times, oldest first."""
        with self._lock:
            if hex_code not in self._tracks:
                return []
            return self._past.get(hex_code, []) + self._recorded(hex_code)

    def export(self, since: float) -> dict[str, list[TimedPoint]]:
        """The tracks of the aircraft heard since then, with times: to keep over a restart."""
        with self._lock:
            return {
                h: self._past.get(h, []) + self._recorded(h)
                for h, stamps in self._stamps.items()
                if stamps[-1] >= since
            }

    def restore(self, saved: dict[str, list[TimedPoint]]) -> None:
        """Tracks from before a restart: each joins its aircraft's track when it is heard
        again, if it is the same flight."""
        with self._lock:
            self._saved = dict(saved)
            for hex_code in self._saved.keys() & self._tracks.keys():
                self._join_past(hex_code, self._saved.pop(hex_code))

    def track(self, hex_code: str) -> tuple[TrackPoint, ...]:
        """Positions of one aircraft, oldest first: any fetched from before the display saw
        it, then those it recorded. The same tuple is returned until the track grows, so a
        caller can tell cheaply that nothing has changed."""
        with self._lock:
            copy = self._track_copies.get(hex_code)
            if copy is None:
                past = tuple(p[:3] for p in self._past.get(hex_code, ()))
                copy = past + tuple(self._tracks.get(hex_code, ()))
                if copy:
                    self._track_copies[hex_code] = copy
            return copy

    def remembered(self) -> list[Aircraft]:
        """The last report of every aircraft seen, in the feed or not, most recent last."""
        with self._lock:
            return list(self._remembered.values())

    def feed(self) -> FeedSnapshot:
        with self._lock:
            return self._snapshot


def _thinned(points: list[TimedPoint]) -> list[TimedPoint]:
    """At most MAX_PAST_POINTS, evenly spaced, keeping the first and the last."""
    if len(points) <= MAX_PAST_POINTS:
        return points
    step = (len(points) - 1) / (MAX_PAST_POINTS - 1)
    return [points[round(i * step)] for i in range(MAX_PAST_POINTS)]
