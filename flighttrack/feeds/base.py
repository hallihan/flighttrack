"""The aircraft model shared by every feed, and the interface feeds implement."""

from dataclasses import dataclass
from typing import Protocol

from flighttrack.geo import destination

# Aircraft with no position update for this long are dropped from the display.
STALE_AFTER_S = 60.0
# Never extrapolate a position further than this past its last report.
MAX_EXTRAPOLATION_S = 30.0


@dataclass(frozen=True, slots=True)
class Aircraft:
    hex: str
    lat: float
    lon: float
    seen_at: float  # monotonic time at which lat/lon were valid
    callsign: str | None = None
    registration: str | None = None
    type_code: str | None = None
    altitude_ft: int | None = None
    on_ground: bool = False
    ground_speed_kt: float | None = None
    track_deg: float | None = None
    vertical_rate_fpm: int | None = None
    squawk: str | None = None
    emergency: bool = False
    category: str | None = None  # ADS-B emitter category, e.g. "A3" or "A7"

    @property
    def label(self) -> str:
        return self.callsign or self.registration or self.hex.upper()

    def is_stale(self, now: float) -> bool:
        return now - self.seen_at > STALE_AFTER_S

    def position_at(self, now: float) -> tuple[float, float]:
        """Dead-reckoned position, so motion stays smooth between feed updates."""
        if self.ground_speed_kt is None or self.track_deg is None:
            return self.lat, self.lon
        dt = min(max(now - self.seen_at, 0.0), MAX_EXTRAPOLATION_S)
        return destination(self.lat, self.lon, self.track_deg, self.ground_speed_kt * dt / 3600.0)


class Feed(Protocol):
    name: str

    def fetch(self, lat: float, lon: float, radius_nm: float) -> list[Aircraft]:
        """Aircraft within radius_nm of the point. Raises on failure."""
        ...
