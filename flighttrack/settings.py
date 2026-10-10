"""User settings, persisted as JSON in the data directory (outside the repo)."""

import json
import logging
import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from flighttrack import secrets_store, themes

log = logging.getLogger(__name__)

RANGES_NM: tuple[float, ...] = (1, 2.5, 5, 10, 25, 30, 40, 50, 100, 250)
DEFAULT_RANGE_NM = 10.0
MAX_FAVORITE_AIRPORTS = 100
# The WX button cycles through these.
# Settings that are a fraction of full scale, with the lowest value each may take. The floors
# stop a slider from making the screen or a layer disappear entirely.
FRACTION_SETTINGS: dict[str, float] = {
    "brightness": 0.1,
    "map_brightness": 0.2,
    "weather_opacity": 0.1,
    "ring_opacity": 0.0,  # 0 hides the rings altogether, which is a fair thing to want
    "feature_opacity": 0.0,
}


def data_dir() -> Path:
    override = os.environ.get("FLIGHTTRACK_DATA_DIR")
    return Path(override) if override else Path.home() / ".local" / "share" / "flighttrack"


# What "Reset all settings" leaves alone: where home is, the user's lists of favorites, and
# the address of their receiver. Those are the user's own data, typed in; the rest are
# adjustments, which is what a reset is for.
KEPT_ON_RESET: tuple[str, ...] = (
    "home_airport",
    "favorite_airports",
    "home_lat",
    "home_lon",
    "favorites",
    "pinned_favorites",
    "local_receiver_url",
    "opensky_client_id",
    "opensky_client_secret",
    "flightaware_key",
    "adsbx_key",
    "portal_password",
)


# The color themes, with their names (themes.py has the colors).
THEMES: dict[str, str] = themes.NAMES

# The maps on offer, in the order a long press on MAP steps through them, with their names.
MAP_STYLES: dict[str, str] = {
    "satellite": "Satellite",
    "chart": "VFR chart",
    "street": "Street map",
}


@dataclass
class Settings:
    # ICAO ident of the airport the display calls home: the favorite airport chosen most
    # recently. Takes precedence over home_lat/lon.
    home_airport: str | None = None
    favorite_airports: list[str] = field(default_factory=list)  # ICAO idents
    home_lat: float | None = None
    home_lon: float | None = None
    range_nm: float = DEFAULT_RANGE_NM
    # aircraft.json URL of a readsb/dump1090 receiver on the local network, if there is one
    local_receiver_url: str | None = None
    show_map: bool = True
    map_style: str = "satellite"  # one of MAP_STYLES
    theme: str = "dark"  # one of THEMES
    show_radar: bool = True  # rain radar
    show_clouds: bool = True  # satellite cloud cover
    show_radar_key: bool = False  # the strip explaining the radar colors
    show_airspace: bool = True
    show_runways: bool = True
    # Legend groups of traffic switched off with the pills in the top bar (theme.LEGEND keys).
    hidden_traffic: list[str] = field(default_factory=list)
    favorites: list[str] = field(default_factory=list)  # registrations, callsigns or hex codes
    # Favorites whose tracks are always drawn, as written in favorites.
    pinned_favorites: list[str] = field(default_factory=list)
    # Optional keys for services that give more with one (secrets_store.KEY_FIELDS). Kept as
    # typed, shown only masked.
    opensky_client_id: str | None = None
    opensky_client_secret: str | None = None
    flightaware_key: str | None = None
    adsbx_key: str | None = None
    # The web page's password, as a salted hash (secrets_store); None: the web page shows only
    # the preview of the screen.
    portal_password: str | None = None
    # Where the map was last dragged to; None when it is centered on home.
    view_lat: float | None = None
    view_lon: float | None = None
    day_night: bool = True  # shade the map, and dim the screen, by the sun's position
    brightness: float = 1.0  # screen backlight, as a fraction of full
    map_brightness: float = 0.7  # how bright the satellite imagery is in daylight
    weather_opacity: float = 0.75
    ring_opacity: float = 0.25  # of the range rings and their labels
    # Of what is drawn on the map to describe the ground: boundary lines and place names (they
    # come as one layer), and runways.
    feature_opacity: float = 0.8

    @property
    def home(self) -> tuple[float, float] | None:
        if self.home_lat is None or self.home_lon is None:
            return None
        return self.home_lat, self.home_lon

    def step_range(self, direction: int) -> None:
        """Move to the next larger (+1) or smaller (-1) range, stopping at the ends."""
        index = RANGES_NM.index(self.range_nm) + direction
        self.range_nm = RANGES_NM[min(max(index, 0), len(RANGES_NM) - 1)]


def _validated(raw: dict) -> Settings:
    known = {f.name for f in fields(Settings)}
    settings = Settings(**{k: v for k, v in raw.items() if k in known})
    if settings.range_nm not in RANGES_NM:
        settings.range_nm = DEFAULT_RANGE_NM
    for lat_name, lon_name in (("home_lat", "home_lon"), ("view_lat", "view_lon")):
        lat, lon = getattr(settings, lat_name), getattr(settings, lon_name)
        numeric = all(isinstance(v, int | float) and not isinstance(v, bool) for v in (lat, lon))
        if not (numeric and -90 <= lat <= 90 and -180 <= lon <= 180):
            setattr(settings, lat_name, None)
            setattr(settings, lon_name, None)
    code = settings.home_airport
    valid_code = isinstance(code, str) and 0 < len(code.strip()) <= 10
    settings.home_airport = code.strip().upper() if valid_code else None
    airports = settings.favorite_airports if isinstance(settings.favorite_airports, list) else []
    cleaned = [
        a.strip().upper() for a in airports if isinstance(a, str) and 0 < len(a.strip()) <= 10
    ]
    if settings.home_airport and settings.home_airport not in cleaned:
        cleaned.insert(0, settings.home_airport)  # home is always one of the favorites
    settings.favorite_airports = list(dict.fromkeys(cleaned))[:MAX_FAVORITE_AIRPORTS]
    url = settings.local_receiver_url
    if not (isinstance(url, str) and url.startswith(("http://", "https://"))):
        settings.local_receiver_url = None
    defaults = Settings()
    if settings.map_style not in MAP_STYLES:
        settings.map_style = defaults.map_style
    if settings.theme not in THEMES:
        settings.theme = defaults.theme
    entries = settings.favorites if isinstance(settings.favorites, list) else []
    settings.favorites = [e for e in entries if isinstance(e, str) and 0 < len(e) <= 12][:200]
    for name in ("opensky_client_id", "opensky_client_secret", "flightaware_key", "adsbx_key"):
        value = getattr(settings, name)
        if not (isinstance(value, str) and 0 < len(value) <= 200 and value.isprintable()):
            setattr(settings, name, None)
    if not secrets_store.is_hash(settings.portal_password):
        settings.portal_password = None
    pinned = settings.pinned_favorites if isinstance(settings.pinned_favorites, list) else []
    settings.pinned_favorites = [e for e in pinned if e in settings.favorites]
    for name in (
        "show_map",
        "show_radar",
        "show_clouds",
        "show_radar_key",
        "show_airspace",
        "show_runways",
        "day_night",
    ):
        if not isinstance(getattr(settings, name), bool):
            setattr(settings, name, getattr(defaults, name))
    for name, low in FRACTION_SETTINGS.items():
        value = getattr(settings, name)
        if isinstance(value, bool) or not isinstance(value, int | float):
            setattr(settings, name, getattr(defaults, name))
        else:
            setattr(settings, name, min(max(float(value), low), 1.0))
    return settings


# Keys and the web page's password hash are not kept in settings.json, which is the file one
# might copy or show to someone, but beside it in a file only the display's user can read.
SECRET_FIELDS: tuple[str, ...] = (
    "opensky_client_id",
    "opensky_client_secret",
    "flightaware_key",
    "adsbx_key",
    "portal_password",
)
SECRETS_FILE = "secrets.json"


def load(path: Path) -> Settings:
    """Read settings, and the secrets beside them, falling back to defaults if the file is
    missing or unreadable. Secrets come only from their own file."""
    try:
        raw = json.loads(path.read_text())
        if not isinstance(raw, dict):
            raise ValueError("settings file is not a JSON object")
    except FileNotFoundError:
        raw = {}
    except (OSError, ValueError, TypeError) as exc:
        log.warning("ignoring unreadable settings at %s: %s", path, exc)
        raw = {}
    raw = {k: v for k, v in raw.items() if k not in SECRET_FIELDS}
    raw.update(_load_secrets(path.parent / SECRETS_FILE))
    try:
        return _validated(raw)
    except (ValueError, TypeError) as exc:
        log.warning("ignoring unreadable settings at %s: %s", path, exc)
        return Settings()


def _load_secrets(path: Path) -> dict:
    try:
        raw = json.loads(path.read_text())
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        log.warning("ignoring unreadable secrets at %s: %s", path, exc)
        return {}
    if not isinstance(raw, dict):
        return {}
    return {name: raw[name] for name in SECRET_FIELDS if name in raw}


def save(settings: Settings, path: Path) -> None:
    """Write atomically, so a power cut can't leave a half-written file: the settings to
    path, the secrets to SECRETS_FILE beside it, readable by this user only, as is the
    directory they are in."""
    path.parent.mkdir(parents=True, exist_ok=True)
    _private(path.parent, 0o700)
    data = asdict(settings)
    secret = {name: data.pop(name) for name in SECRET_FIELDS}
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    os.replace(tmp, path)
    _write_private(path.parent / SECRETS_FILE, json.dumps(secret, indent=2) + "\n")


def make_private(directory: Path) -> None:
    """Make the data directory this user's alone (it holds the secrets), creating it."""
    directory.mkdir(parents=True, exist_ok=True)
    _private(directory, 0o700)
    secrets_path = directory / SECRETS_FILE
    if secrets_path.exists():
        _private(secrets_path, 0o600)


def _private(path: Path, mode: int) -> None:
    try:
        path.chmod(mode)
    except OSError as exc:
        log.warning("could not make %s private: %s", path, exc)


def _write_private(path: Path, text: str) -> None:
    """Write a file that only this user can read, atomically, and never readable by others
    even for a moment: it is created with those permissions, not changed to them after."""
    tmp = path.with_suffix(".tmp")
    try:
        tmp.unlink()
    except FileNotFoundError:
        pass
    descriptor = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)
