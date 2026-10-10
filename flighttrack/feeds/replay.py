"""A feed that loops through traffic stored in a file, for offline and deterministic work.

File format: {"center": {"lat": .., "lon": ..}, "interval": seconds, "frames": [response, ...]}
where each frame is a readsb-style response.
"""

import json
import time
from collections.abc import Callable
from importlib import resources
from pathlib import Path

from flighttrack.feeds.base import Aircraft
from flighttrack.feeds.readsb import parse_response


class ReplayFeed:
    name = "replay"
    attribution = None

    def __init__(
        self, path: Path | None = None, clock: Callable[[], float] = time.monotonic
    ) -> None:
        if path is None:
            text = resources.files("flighttrack.feeds").joinpath("data/sample.json").read_text()
        else:
            text = path.read_text()
        data = json.loads(text)
        self.center: tuple[float, float] = (data["center"]["lat"], data["center"]["lon"])
        self._interval = float(data["interval"])
        self._frames: list[dict] = data["frames"]
        if not self._frames or self._interval <= 0:
            raise ValueError("replay file needs at least one frame and a positive interval")
        self._clock = clock
        self._start = clock()

    def fetch(self, lat: float, lon: float, radius_nm: float) -> list[Aircraft]:
        now = self._clock()
        index = int((now - self._start) / self._interval) % len(self._frames)
        return parse_response(self._frames[index], now)
