"""Choosing the weather layers for where the display is: rain radar and cloud cover.

Radar. Over the contiguous United States we use the Iowa State Mesonet's NEXRAD composite,
which is drawn in the standard US weather-radar colors (greens, yellows, reds). Elsewhere we
fall back to RainViewer, which is worldwide but has its own color scheme.

Clouds. NASA GIBS publishes GeoColor imagery from the geostationary weather satellites: true
color by day, infrared-derived by night. Blended so that only its bright parts add to the
map, it reads as cloud cover. Coverage is the Americas and the Pacific rim; elsewhere there is
no cloud layer.

Both kinds of tile are replaced every few minutes under the same URL (radar) or a new time
(clouds), so each refresh gets its own layer name and therefore its own cache directory.
"""

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

import requests

from flighttrack import net
from flighttrack.feeds.http import USER_AGENT
from flighttrack.tiles import TileLayer
from flighttrack.tiles.radar import INDEX_URL, latest_layer

log = logging.getLogger(__name__)

RADAR_PREFIX = "radar-"
CLOUD_PREFIX = "clouds-"
RADAR_SLOT_S = 300
CLOUD_SLOT_S = 1200
# GIBS imagery appears some time after it is taken; ask for a time safely in the past.
CLOUD_DELAY_S = 2400
CHECK_EVERY_S = 30.0
RAINVIEWER_REFRESH_S = 300.0

IEM_RADAR_URL = (
    "https://mesonet.agron.iastate.edu/cache/tile.py/1.0.0/nexrad-n0q-900913/{z}/{x}/{y}.png"
)
GIBS_URL = (
    "https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/{satellite}_GeoColor/default/{time}"
    "/GoogleMapsCompatible_Level7/{{z}}/{{y}}/{{x}}.png"
)

# Radar color keys for the on-screen legend: (dBZ, color), lightest rain first.
NWS_COLORS: tuple[tuple[int, tuple[int, int, int]], ...] = (
    (10, (1, 159, 244)),
    (15, (3, 0, 244)),
    (20, (2, 253, 2)),
    (25, (1, 197, 1)),
    (30, (0, 142, 0)),
    (35, (253, 248, 2)),
    (40, (229, 188, 0)),
    (45, (253, 149, 0)),
    (50, (253, 0, 0)),
    (55, (212, 0, 0)),
    (60, (188, 0, 0)),
    (65, (248, 0, 253)),
)
RAINVIEWER_COLORS: tuple[tuple[int, tuple[int, int, int]], ...] = (
    (10, (206, 192, 135)),
    (15, (136, 221, 238)),
    (20, (0, 163, 224)),
    (25, (0, 119, 170)),
    (30, (0, 85, 136)),
    (35, (255, 238, 0)),
    (40, (255, 170, 0)),
    (45, (255, 68, 0)),
    (50, (193, 0, 0)),
    (55, (255, 170, 255)),
    (60, (255, 119, 255)),
    (65, (255, 255, 255)),
)
# What those reflectivities mean in everyday terms: (from dBZ, to dBZ, label).
LEGEND_BANDS: tuple[tuple[int, int, str], ...] = (
    (10, 20, "Drizzle"),
    (20, 35, "Light"),
    (35, 45, "Moderate"),
    (45, 55, "Heavy"),
    (55, 70, "Hail"),
)


def in_nexrad_coverage(lat: float, lon: float) -> bool:
    """Roughly the contiguous United States, which is what the NEXRAD composite covers."""
    return 24.0 <= lat <= 50.0 and -126.0 <= lon <= -66.0


def cloud_satellite(lon: float) -> str | None:
    """The GIBS GeoColor product that sees this longitude, if any."""
    if -115.0 <= lon <= -20.0:
        return "GOES-East_ABI"
    if lon < -115.0 or lon >= 165.0:
        return "GOES-West_ABI"
    if 80.0 <= lon < 165.0:
        return "Himawari_AHI"
    return None


def nexrad_layer(now: float) -> TileLayer:
    slot = int(now // RADAR_SLOT_S)
    return TileLayer(
        f"{RADAR_PREFIX}nexrad-{slot}", IEM_RADAR_URL, 9, "png", "IEM", short_lived=True
    )


def cloud_layer(lon: float, now: float) -> TileLayer | None:
    satellite = cloud_satellite(lon)
    if satellite is None:
        return None
    slot = int((now - CLOUD_DELAY_S) // CLOUD_SLOT_S) * CLOUD_SLOT_S
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(slot))
    url = GIBS_URL.format(satellite=satellite, time=stamp)
    return TileLayer(f"{CLOUD_PREFIX}{slot}", url, 7, "png", "NASA", short_lived=True)


@dataclass(frozen=True, slots=True)
class Weather:
    radar: TileLayer | None = None
    clouds: TileLayer | None = None
    radar_colors: tuple[tuple[int, tuple[int, int, int]], ...] = NWS_COLORS


class WeatherSources(threading.Thread):
    """Keeps the current weather layers up to date for a location that may change."""

    def __init__(
        self,
        location: Callable[[], tuple[float, float]],
        on_change: Callable[[str, str], None] | None = None,
        session: requests.Session | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        """location returns the (lat, lon) to choose sources for. on_change(prefix, keep) is
        called when a layer is replaced, so the old one's cached tiles can be deleted."""
        super().__init__(name="weather", daemon=True)
        self._location = location
        self._on_change = on_change
        self._session = session or requests.Session()
        self._clock = clock
        self._rainviewer: TileLayer | None = None
        self._rainviewer_checked = float("-inf")
        self.current = Weather()
        self._stop_event = threading.Event()

    def _rainviewer_layer(self, now: float) -> TileLayer | None:
        if now - self._rainviewer_checked >= RAINVIEWER_REFRESH_S:
            self._rainviewer_checked = now
            try:
                response = net.get(
                    self._session, INDEX_URL, headers={"User-Agent": USER_AGENT}, timeout=(4.0, 8.0)
                )
                response.raise_for_status()
                self._rainviewer = latest_layer(response.json())
            except (requests.RequestException, KeyError, IndexError, ValueError, TypeError) as exc:
                log.warning("radar index refresh failed: %s", exc)
        return self._rainviewer

    def refresh(self) -> None:
        now = self._clock()
        lat, lon = self._location()
        if in_nexrad_coverage(lat, lon):
            radar, colors = nexrad_layer(now), NWS_COLORS
        else:
            radar, colors = self._rainviewer_layer(now), RAINVIEWER_COLORS
        new = Weather(radar, cloud_layer(lon, now), colors)
        old, self.current = self.current, new
        if self._on_change is not None:
            for prefix, before, after in (
                (RADAR_PREFIX, old.radar, new.radar),
                (CLOUD_PREFIX, old.clouds, new.clouds),
            ):
                if after is not None and (before is None or before.name != after.name):
                    self._on_change(prefix, after.name)

    def run(self) -> None:
        while not self._stop_event.is_set():
            self.refresh()
            self._stop_event.wait(CHECK_EVERY_S)

    def stop(self) -> None:
        self._stop_event.set()
