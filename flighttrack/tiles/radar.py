"""Weather radar from RainViewer: finding the latest frame. Used where there is no NEXRAD
coverage (see weather.py).

RainViewer publishes an index of recent radar frames; each frame is a tile set under its own
path. Tiles exist up to zoom 7 only (beyond that the service returns a placeholder image), so
closer views scale them up, which suits data this coarse.
"""

from typing import Any

from flighttrack.tiles import TileLayer

INDEX_URL = "https://api.rainviewer.com/public/weather-maps.json"
MAX_ZOOM = 7
LAYER_PREFIX = "radar-"


def latest_layer(index: dict[str, Any]) -> TileLayer:
    """The tile layer for the newest radar frame in an index response. Raises on a bad index."""
    host = index["host"]
    frame = index["radar"]["past"][-1]
    path, time = frame["path"], int(frame["time"])
    if not (isinstance(host, str) and host.startswith("https://") and isinstance(path, str)):
        raise ValueError("unexpected radar index contents")
    # .../256/{z}/{x}/{y}/2/1_1.png: 256 px tiles, color scheme 2, smoothed, snow shown.
    template = f"{host}{path}/256/{{z}}/{{x}}/{{y}}/2/1_1.png"
    return TileLayer(
        f"{LAYER_PREFIX}rainviewer-{time}",
        template,
        MAX_ZOOM,
        "png",
        "RainViewer",
        short_lived=True,
    )
