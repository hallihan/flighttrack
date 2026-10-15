"""Entry point and main loop."""

import argparse
import gzip
import io
import json
import logging
import os
import shutil
import signal
import socket
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from math import cos, hypot, radians
from pathlib import Path

import pygame

from flighttrack import airports as airports_module
from flighttrack import cache as cache_module
from flighttrack import enrich as enrich_module
from flighttrack import favorites as favorites_module
from flighttrack import keyed, legs, secrets_store
from flighttrack import location as location_module
from flighttrack import metar as metar_module
from flighttrack import reach as reach_module
from flighttrack import runways as runways_module
from flighttrack import settings as settings_module
from flighttrack.airports import Airport
from flighttrack.airspace import Airspace, AirspaceStore
from flighttrack.enrich import Enricher
from flighttrack.feeds import history as history_module
from flighttrack.feeds.base import STALE_AFTER_S, Aircraft
from flighttrack.feeds.coverage import QueryPlan, plan_query
from flighttrack.feeds.http import LOCAL as LOCAL_FEED
from flighttrack.feeds.http import (
    LOCAL_INTERVAL_S,
    find_flight,
    local_feed,
    plain_error,
    public_feed,
)
from flighttrack.feeds.poller import MIN_SPACING_S, FeedPoller
from flighttrack.feeds.replay import ReplayFeed
from flighttrack.feeds.watch import WATCH_INTERVAL_S, Watch
from flighttrack.geo import bearing_deg, distance_nm
from flighttrack.geo.mercator import shifted, zoom_for_range
from flighttrack.geo.sun import daylight
from flighttrack.location import Location
from flighttrack.metar import MetarStore
from flighttrack.platform.backlight import find_backlight
from flighttrack.platform.power import Power
from flighttrack.platform.wifi import Wifi
from flighttrack.portal import DEFAULT_PORT, PortalServer, PortalState, create_app
from flighttrack.settings import FRACTION_SETTINGS, RANGES_NM
from flighttrack.state import FeedSnapshot, Store
from flighttrack.tiles import CHART, IMAGERY, PLACES, STREET
from flighttrack.tiles.store import TileStore
from flighttrack.tiles.weather import CLOUD_PREFIX, RADAR_PREFIX, WeatherSources
from flighttrack.ui import bars, panel, theme
from flighttrack.ui import format as fmt
from flighttrack.ui.basemap import MARGIN_PX, Basemap, LayerSpec
from flighttrack.ui.go import GoScreen, match_flights
from flighttrack.ui.keyboard import Keyboard
from flighttrack.ui.layout import compute_layout
from flighttrack.ui.overlays import (
    AIRSPACE_LABEL_RANGE_NM,
    Overlay,
    airport_painter,
    airspace_painter,
    reach_painter,
    runway_painter,
)
from flighttrack.ui.photo import PhotoScreen
from flighttrack.ui.scope import (
    AIRPORT_TAP_PX,
    TAP_RADIUS_PX,
    WIDE_RANGE_NM,
    Drawn,
    LabelMemory,
    Scene,
    aircraft_near,
    draw_scope,
    look,
    resolve_tap,
    screen_xy,
    tap_radius,
)
from flighttrack.ui.screens import SettingsScreen
from flighttrack.ui.widgets import Button, FilterPill, GlassButton, IconButton, Pill, Toggle

log = logging.getLogger(__name__)

WINDOW_SIZE = (800, 480)
FRAME_RATE = 30
RANGE_BUTTON_PX = 52
FILTER_GAP_PX = 12  # between the map's chips in the top bar and the traffic filters
# A press that moves further than this before release is a drag, not a tap.
TAP_SLOP_PX = 16
# Data older than this is flagged as stale in the status bar.
STALE_WARNING_S = 20.0
# The selected aircraft's pane says it is not being updated once it has gone this long.
SILENT_AIRCRAFT_S = 30.0
WHEREABOUTS_S = 2.0  # how often the favorite aircraft's last positions are worked out again
TRACE_AGAIN_S = 600.0  # a selected aircraft's earlier track is asked for again after this
PAID_TRACE_AGAIN_S = 3600.0  # and of FlightAware, which is paid by the request
TRACKS_FILE = "tracks.json.gz"  # the tracks kept over a restart
REACH_FILE = "reach.json.gz"  # how low the feeds hear aircraft, learned over time
REACH_SAVE_S = 3600.0  # it is written this often, and on the way out
REACH_REPAINT_S = 30.0  # while diagnostics show it, its tint is repainted at most this often
GROUND_ASK_S = 60.0  # the height of the ground under it is asked for at most this often
ADDRESS_REFRESH_S = 30.0
BACKLIGHT_POLL_S = 1.0  # how often to look for a brightness change made outside the app
# While following an aircraft the map scale is only refreshed once it has gone this far north
# or south, so the picture is not rebuilt every frame.
FOLLOW_RESCALE_DEG = 0.5
NETWORK_REFRESH_S = 15.0  # how often the name of the Wi-Fi network in use is re-read

# Satellite imagery brightness (each channel, out of 255) in full daylight and at night, before
# the user's map-brightness setting is applied. Night is dimmer and bluer.
MAP_DAY_SHADE = (215, 215, 215)
MAP_NIGHT_SHADE = (95, 108, 140)
DAYLIGHT_STEPS = 16  # shading changes in this many steps, so dusk doesn't recompose every frame
DAYLIGHT_REFRESH_S = 60.0
# With day/night effects on, the screen itself dims to this fraction of its setting at night.
NIGHT_BACKLIGHT = 0.5
# Cloud imagery darker than this adds nothing to the map; only bright cloud shows.
CLOUD_THRESHOLD = 70
CLOUD_STRENGTH = 0.6  # of the weather opacity

RUNWAY_RANGE_NM = 5  # runways are drawn at this range or closer
AIRSPACE_RANGE_NM = 50  # airspace boundaries likewise
# With nobody touching it the screen is redrawn this often: nothing on it moves a pixel a
# second, and the full rate only buys heat. A touch brings the full rate back at once.
IDLE_FRAME_RATE = 10
ACTIVE_FOR_S = 1.5  # how long after the last touch the full rate is kept
HOME_HEART_MIN_S = 0.6  # the second tap to un-favorite the home airport must come after this
HOME_HEART_MAX_S = 5.0  # and before this
MAP_UNAVAILABLE_AFTER_S = 25.0
# At 100 NM and beyond an aircraft takes several seconds to cross a pixel, so its position
# (and its place in the order) is worked out this often, not every frame.
POSITION_HOLD_S = 0.5
CHART_ZOOM_SLACK = 1.5  # the chart is shown this many levels further out than its tiles go
FLASH_S = 2.5  # how long a notice of a change stays on the map
PREFETCH_AFTER_S = 1.0  # the view must be still this long before the map round it is loaded
STORAGE_REFRESH_S = 30.0
PREVIEW_EVERY_S = 1.0  # the web page's picture of the screen is renewed this often...
PREVIEW_WANTED_FOR_S = 6.0  # ...for this long after it was last asked for
WEATHER_PREFIXES = (RADAR_PREFIX, CLOUD_PREFIX)  # names of the tile layers that are weather
# A changed setting is written to disk within this long, not at once: see App._save_settings.
SETTINGS_SAVE_DELAY_S = 300.0


def photo_credit(photos: tuple[enrich_module.Photo, ...]) -> str:
    """One line crediting every picture shown: the photographers, then where they are from."""
    people, places = [], []
    for photo in photos:
        who, _, where = photo.credit.rpartition(" / ")
        if who and who not in people:
            people.append(who)
        if where not in places:
            places.append(where)
    return " / ".join(part for part in (", ".join(people), ", ".join(places)) if part)


def _warm_tables() -> None:
    airports_module.warm()
    runways_module.warm()


def _by_distance(drawn: Drawn) -> float:
    return drawn.distance_nm


_TOUCH_EVENTS = (
    pygame.MOUSEBUTTONDOWN,
    pygame.MOUSEBUTTONUP,
    pygame.MOUSEMOTION,
    pygame.FINGERDOWN,
    pygame.KEYDOWN,
)


def local_address() -> str | None:
    """This machine's LAN address. Opens no connection: UDP connect only picks a route."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("192.0.2.1", 9))
            return sock.getsockname()[0]
    except OSError:
        return None


class App:
    def __init__(
        self,
        *,
        fullscreen: bool,
        data_dir: Path,
        replay: bool,
        replay_file: Path | None = None,
        locate: Callable[[], Location | None] | None = None,
        portal_port: int | None = None,
        tile_store: TileStore | None = None,
        airspace: AirspaceStore | None = None,
        wifi: Wifi | None = None,
        metar: MetarStore | None = None,
        enrich: Enricher | None = None,
        power: Power | None = None,
        find_flight: Callable[[str], Aircraft | None] | None = None,
        find_history: Callable[[str, str | None, str | None], list[Aircraft]] | None = None,
        find_trace: Callable[[str], list[tuple[float, float, int | None, float]]] | None = None,
        find_receiver_track: Callable[[str, str], list[tuple[float, float, int | None, float]]]
        | None = None,
        find_ground: Callable[[list], dict] | None = None,
        keyed_services: bool = False,
        save_delay_s: float = 0.0,
    ) -> None:
        """locate estimates the location when the user has not chosen one (it may block; it
        runs on a worker thread). portal_port starts the settings portal. tile_store supplies
        map and weather tiles, airspace the airspace boundaries, wifi the means to change
        network, metar the airport weather reports. All default to off, which is what tests
        want; main() turns them on.

        save_delay_s is how long a changed setting may wait before it is written to disk.
        Zero writes at once."""
        # Initialize only what we use: the mixer can stall start-up when there is no audio sink.
        pygame.display.init()
        pygame.font.init()
        if fullscreen:
            self.screen = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
            pygame.mouse.set_visible(False)
        else:
            self.screen = pygame.display.set_mode(WINDOW_SIZE)
        pygame.display.set_caption("FlightTrack")

        self.fonts = theme.Fonts.load()
        self.clock = pygame.time.Clock()
        self.layout = compute_layout(*self.screen.get_size())

        self.data_dir = data_dir
        self.prefetched_tiles = 0  # for diagnostics
        self.settings_path = data_dir / "settings.json"
        settings_module.make_private(data_dir)  # keys and the password hash are kept there
        self.settings = settings_module.load(self.settings_path)
        # Guards self.settings and its file: the portal thread changes them too.
        self._settings_lock = threading.Lock()
        self._save_delay_s = save_delay_s
        self._unsaved_since: float | None = None  # when the oldest unwritten change was made
        self.settings_writes = 0
        self._reconfigure = threading.Event()  # set by other threads; acted on by the main loop
        # Likewise: settings were changed from the web page, and the controls must catch up.
        self._changed_elsewhere = threading.Event()
        self._reset_elsewhere = False
        # The services that need a key (keyed.py), used when one is set; off in tests.
        self.keyed_services = keyed_services
        self._opensky: keyed.OpenSky | None = None
        self._aeroapi: keyed.AeroApi | None = None
        self.adsbx: keyed.AdsbxFeed | None = None
        self._refresh_favorite_keys()

        self._force_demo = replay or replay_file is not None
        self._replay_file = replay_file
        self._locate = locate
        self._estimate: Location | None = None  # last answer from locate()
        self._locating = False
        self._started = False
        self.store = Store()
        self.pollers: list[FeedPoller] = []
        # How low the feeds hear aircraft, place by place: learned from every feed answer.
        self.reach = reach_module.Reach()
        self._answered: dict[str, float] = {}  # provider -> when it last answered
        self._reach_saved_at = time.monotonic()
        self._reach_shown: tuple[int, float] = (-1, float("-inf"))
        self._reach_overlay_cache: Overlay | None = None
        threading.Thread(
            target=self.reach.load, args=(data_dir / REACH_FILE,), name="reach", daemon=True
        ).start()
        # Where the map has been dragged to, or None when it is centered on home. Everything on
        # screen (rings, distances, the nearest list, weather, the feed query) is about the
        # center of the view, wherever that is.
        self.view_center: tuple[float, float] | None = None
        self._view_restored = False
        self.configure_feeds()

        self.tile_store = tile_store
        self.airspace = airspace
        self.basemap = Basemap(tile_store, self.layout.view.size)
        self.weather: WeatherSources | None = None
        if tile_store is not None:
            self.weather = WeatherSources(lambda: self.center, tile_store.forget_layers)
        self._airspace_memo: tuple[tuple, tuple[Airspace, ...]] = ((), ())

        self.portal: PortalServer | None = None
        if portal_port is not None:
            self.portal = PortalServer(
                create_app(
                    self.portal_state,
                    self.apply_changes,
                    flights=self.known_aircraft,
                    reset=self.request_reset,
                    storage=lambda: self.storage(time.monotonic()),
                    preview=self.preview,
                    receiver_status=self.receiver_status,
                    clear_caches=self.clear_caches,
                    secret_key=portal_secret(data_dir),
                ),
                portal_port,
            )
            self.portal.start()

        self.wifi = wifi or Wifi(available=False)
        self.metar = metar
        self.enrich = enrich  # routes, airframes and photos for the selected aircraft
        self._photo_surfaces: OrderedDict[tuple[str, int], pygame.Surface | None] = OrderedDict()
        self.power = power or Power(available=False)
        self.find_flight = find_flight  # looks a flight up beyond the map; may block
        # Where a flight not heard now was last seen (text, receiver URL, hex if known); may block.
        self.find_history = find_history
        # The selected aircraft's flight so far, from before the display saw it; may block.
        self.find_trace = find_trace
        # The same from a local receiver's history (receiver URL, hex); may block.
        self.find_receiver_track = find_receiver_track
        # The height of the ground in coverage map cells, for showing it above the ground.
        self.find_ground = find_ground
        self._ground_asked_at = float("-inf")
        self._ground_busy = False
        self._traces_asked: dict[str, float] = {}  # hex -> when its trace was asked for
        self._paid_traces_asked: dict[str, float] = {}  # the same, of FlightAware
        self._departures: dict[tuple, str] = {}  # (hex, first point) -> its words
        self.selected_airport: Airport | None = None
        self.network_name: str | None = None  # the Wi-Fi network in use, kept up to date
        self._network_stop = threading.Event()
        # A full-screen page over the map, when one is open.
        self.page: SettingsScreen | GoScreen | PhotoScreen | None = None
        self.selected_hex: str | None = None
        self.show_diagnostics = False
        self.positions: dict[str, tuple[int, int]] = {}
        self.list_targets: list[tuple[pygame.Rect, str]] = []
        self.photo_targets: list[pygame.Rect] = []  # the thumbnails in the aircraft pane
        # When a tap lands on several things at once: what they were, to choose from.
        self.candidates: list[tuple[str, str | Airport]] = []
        self.candidate_targets: list[tuple[pygame.Rect, Aircraft | Airport]] = []
        self._press_pos: tuple[int, int] | None = None
        self._panning = False
        self.last_pointer = "none yet"
        self.finger_events = 0
        self.backlight = find_backlight()
        self._backlight_level: float | None = None
        self._backlight_written: float | None = None  # what the panel was last set to, by us
        self._backlight_checked = float("-inf")
        self.following = False  # keeping the selected aircraft in the middle of the view
        self._active_until = 0.0  # full frame rate until then
        self._waiting: list[pygame.event.Event] = []  # events picked up while idling
        self._anchor: tuple[float, float] | None = None  # where the overlays were chosen for
        self._heart_armed_at: float | None = None  # first tap on the home airport's heart
        self._look_key: tuple | None = None
        self._looks_for: tuple[Aircraft, ...] | None = None
        self._looks: dict[str, tuple] = {}
        self._moving_key: tuple | None = None
        self._with_lost_key: tuple | None = None
        self._with_lost: list[Drawn] = []
        self._whereabouts: tuple[tuple[str, ...], float, dict[str, str]] | None = None
        self.password_saving = False  # a new web page password is being hashed
        self._pinned_here: frozenset[str] = frozenset()  # favorites pinned in settings, on the map
        self._moving: list[Drawn] = []
        self._label_rects: tuple[tuple, tuple[pygame.Rect, ...]] = ((), ())
        self._flash: tuple[str, float] | None = None  # a notice on the map, and until when
        self._prefetched: tuple | None = None  # the view the map was last pre-loaded for
        self._prefetch_due: tuple[tuple, float] | None = None  # a view, and since when it is still
        # A picture of the screen for the web page's Preview tab: made only while one is
        # being asked for, by the main loop, and handed to the portal thread as bytes.
        self._preview: bytes | None = None
        self._preview_wanted = float("-inf")
        self._preview_at = float("-inf")
        self._storage: dict | None = None  # sizes for the settings page
        self._storage_at = float("-inf")
        self._storage_busy = False
        self._labels = LabelMemory()  # where each label was last put, for steadiness
        self._map_missing_since: float | None = None
        self._daylight = 1.0
        self._daylight_checked = float("-inf")
        self._address: str | None = None
        self._address_checked_at = float("-inf")
        log.info(
            "display %dx%d, video driver %s, backlight %s",
            *self.screen.get_size(),
            pygame.display.get_driver(),
            self.backlight.name if self.backlight else "not found",
        )
        self._build_controls()
        # Lets the diagnostics be read from a screenshot when nobody is there to tap.
        if os.environ.get("FLIGHTTRACK_DIAGNOSTICS") == "1":
            self.show_diagnostics = True

    # ---- controls -------------------------------------------------------------------------

    def _build_controls(self) -> None:
        view, side, top = self.layout.view, self.layout.panel, self.layout.top
        b = RANGE_BUTTON_PX
        y = view.bottom - b - 6
        # The buttons that sit on the map are glass: what is under them still shows.
        self.range_down = GlassButton(
            pygame.Rect(view.left + 6, y, b, b), "-", lambda: self.step(-1)
        )
        self.range_up = GlassButton(
            pygame.Rect(view.left + 12 + b, y, b, b), "+", lambda: self.step(1)
        )
        self.home_button = GlassButton(
            pygame.Rect(view.left + 18 + 2 * b, y, 76, b), "HOME", self.recenter
        )
        self.back_button = Button(
            pygame.Rect(
                side.left + panel.PADDING,
                side.bottom - theme.MIN_TOUCH_PX - panel.PADDING,
                side.width - 2 * panel.PADDING,
                theme.MIN_TOUCH_PX,
            ),
            "Back to list",
            self.show_list,
        )

        # GO and settings sit at the lower right of the map, balancing the range buttons.
        self.settings_button = IconButton(
            pygame.Rect(view.right - 6 - b, y, b, b), "", self.open_settings
        )
        self.go_button = GlassButton(
            pygame.Rect(view.right - 12 - 2 * b, y, b, b), "GO", self.open_go
        )

        # The row of chips after the title in the top bar: what the map shows...
        x = bars.title_right(self.fonts) + 6
        chip = 50
        self.map_toggle = Toggle(
            pygame.Rect(x, top.top, chip, top.height), "MAP", self.toggle_map, True
        )
        self.map_toggle.on_hold = self.next_map_style  # a long press: the next kind of map
        x += chip
        # Weather is two things, each with its own chip: rain radar and cloud cover.
        self.radar_toggle = Toggle(
            pygame.Rect(x, top.top, chip, top.height), "RAD", lambda: self.flip("show_radar"), True
        )
        x += chip
        self.cloud_toggle = Toggle(
            pygame.Rect(x, top.top, chip, top.height), "CLD", lambda: self.flip("show_clouds"), True
        )
        x += chip
        self.airspace_toggle = Toggle(
            pygame.Rect(x, top.top, 76, top.height), "AIRSPC", self.toggle_airspace, True
        )
        x += 76
        # ...and last of them the radar's color key.
        self.key_toggle = Toggle(
            pygame.Rect(x, top.top, chip, top.height),
            "KEY",
            lambda: self.flip("show_radar_key"),
            False,
        )
        x += chip + FILTER_GAP_PX
        # ...and, set apart, the legend of aircraft colors, each entry a switch that hides
        # that traffic.
        self.filter_pills: dict[str, FilterPill] = {}
        for group, color, label in theme.LEGEND:
            width = FilterPill.width_for(self.fonts.body, label)  # the font it is drawn in
            self.filter_pills[group] = FilterPill(
                pygame.Rect(x, top.top, width, top.height),
                label,
                color,
                lambda group=group: self.toggle_traffic(group),
            )
            x += width
        self.controls_right = x

        # Diagnostics has its own small pill at the end of the bottom bar. Its tap target
        # reaches a little above the bar, into the gap under the panel's button.
        bottom = self.layout.bottom
        face = pygame.Rect(bottom.right - 58, bottom.top + 5, 50, bottom.height - 10)
        reach = pygame.Rect(bottom.right - 66, bottom.top - 10, 66, bottom.height + 10)
        self.diagnostics_pill = Pill(reach, face, "DIAG", self.toggle_diagnostics)

        self._sync_controls()

    def _sync_controls(self) -> None:
        """Make every control show the setting it stands for."""
        s = self.settings
        self.range_down.enabled = s.range_nm != RANGES_NM[0]
        self.range_up.enabled = s.range_nm != RANGES_NM[-1]
        self.map_toggle.active = s.show_map
        self.radar_toggle.active = s.show_radar
        self.cloud_toggle.active = s.show_clouds
        # The key explains the radar's colors: with no radar there is nothing to explain.
        self.key_toggle.enabled = s.show_radar
        self.key_toggle.active = s.show_radar and s.show_radar_key
        self.diagnostics_pill.active = self.show_diagnostics
        self.airspace_toggle.active = s.show_airspace
        self.hidden_traffic = frozenset(s.hidden_traffic)
        for group, pill in self.filter_pills.items():
            pill.active = group not in self.hidden_traffic

    @property
    def buttons(self) -> list[Button]:
        """The buttons currently on screen."""
        shown: list[Button] = [
            self.range_down,
            self.range_up,
            self.go_button,
            self.settings_button,
            *self.filter_pills.values(),
            self.diagnostics_pill,
        ]
        if self.view_center is not None:
            shown.append(self.home_button)
        if self.tile_store is not None:
            shown += [self.map_toggle, self.radar_toggle, self.cloud_toggle, self.key_toggle]
        if self.airspace is not None:
            shown.append(self.airspace_toggle)
        if self.selected_hex or self.selected_airport or self.candidates or self.show_diagnostics:
            shown.append(self.back_button)
        return shown

    # ---- settings -------------------------------------------------------------------------

    def _change(self, name: str, value: object, save: bool = True) -> None:
        with self._settings_lock:
            setattr(self.settings, name, value)
            if save:
                self._save_settings()
        self._sync_controls()

    def _save_settings(self) -> None:
        """Note that the settings have changed and must be written. Caller holds the settings
        lock.

        On the display the write waits (SETTINGS_SAVE_DELAY_S): this runs from an SD card, and
        a session of adjusting things then costs one write, not dozens. What that risks is
        the last few minutes of changes if the power is pulled; a normal stop, restart or
        shut-down writes first (flush_settings)."""
        if self._save_delay_s <= 0:
            self._write_settings()
        elif self._unsaved_since is None:
            self._unsaved_since = time.monotonic()

    def _write_settings(self) -> None:
        """Caller holds the settings lock."""
        self._unsaved_since = None
        try:
            settings_module.save(self.settings, self.settings_path)
            self.settings_writes += 1
        except OSError as exc:
            log.warning("could not save settings: %s", exc)

    def flush_settings(self, older_than_s: float = 0.0) -> bool:
        """Write the settings now if a change is waiting (and has waited that long)."""
        with self._settings_lock:
            since = self._unsaved_since
            if since is None or time.monotonic() - since < older_than_s:
                return False
            self._write_settings()
            return True

    def save_settings(self) -> None:
        with self._settings_lock:
            self._save_settings()

    def reset_settings(self) -> None:
        """Put every adjustment back to how a new display has it: brightness, opacities,
        what the map shows, hidden traffic, the range, and where the map was dragged to.
        Home, the favorite airports and aircraft, and the receiver address are kept."""
        self._reset_values()
        self._after_reset()

    def _reset_values(self) -> None:
        with self._settings_lock:
            fresh = settings_module.Settings()
            for name in settings_module.KEPT_ON_RESET:
                setattr(fresh, name, getattr(self.settings, name))
            self.settings = fresh
            self._save_settings()

    def _after_reset(self) -> None:
        self.following = False
        self.view_center = None
        self._zoom_latitude = self.center[0]
        self._sync_controls()
        self._wake_pollers()  # the range, and so the area asked about, may have changed

    def flip(self, name: str) -> None:
        self._change(name, not getattr(self.settings, name))

    def toggle_map(self) -> None:
        self.flip("show_map")

    def chart_in_range(self) -> bool:
        """Whether the view is close enough in for the chart: further out than this its
        tiles, which stop at level 8, would be needed by the hundred."""
        return self.zoom() >= CHART.min_zoom - CHART_ZOOM_SLACK

    def set_theme(self, name: str) -> None:
        if name in settings_module.THEMES:
            self._change("theme", name)

    def _follow_theme(self) -> None:
        """Main thread, every frame: put the theme setting in force when it has changed
        (on the settings page here, or on the web page). The map is composed afresh, for its
        background color."""
        if self.settings.theme != theme.NAME:
            theme.apply(self.settings.theme)
            self.basemap.refresh()

    def set_map_style(self, style: str) -> None:
        if style in settings_module.MAP_STYLES:
            self._change("map_style", style)

    def next_map_style(self) -> None:
        """A long press on MAP: the next kind of map, turned on if it was off. Says which
        on the map for a moment, since the chip itself does not change."""
        styles = list(settings_module.MAP_STYLES)
        style = styles[(styles.index(self.settings.map_style) + 1) % len(styles)]
        with self._settings_lock:
            self.settings.map_style, self.settings.show_map = style, True
            self._save_settings()
        self._sync_controls()
        words = f"MAP: {settings_module.MAP_STYLES[style].upper()}"
        if style == "chart" and not self.chart_in_range():
            words += " (ZOOM IN TO SEE IT)"
        self._flash = (words, time.monotonic() + FLASH_S)
        self._active_until = max(self._active_until, time.monotonic() + FLASH_S)

    def toggle_airspace(self) -> None:
        self.flip("show_airspace")

    def toggle_traffic(self, group: str) -> None:
        """Hide or show one legend group of traffic (a pill in the top bar)."""
        hidden = set(self.settings.hidden_traffic) ^ {group}
        # Kept in the legend's order, so the saved file does not depend on the order of taps.
        self._change("hidden_traffic", [key for key, _, _ in theme.LEGEND if key in hidden])

    def toggle_diagnostics(self) -> None:
        self.show_diagnostics = not self.show_diagnostics
        self._sync_controls()

    def set_fraction(self, name: str, value: float) -> None:
        """A slider moved. Applied at once; written to disk when the finger lifts."""
        self._change(name, value, save=False)

    def step(self, direction: int) -> None:
        with self._settings_lock:
            before = self.settings.range_nm
            self.settings.step_range(direction)
            changed = self.settings.range_nm != before
            if changed:
                self._save_settings()
        self._sync_controls()
        if changed:
            self._wake_pollers()  # the wider view needs data now, not at the next interval

    def open_settings(self) -> None:
        """Take over the whole screen with the settings page."""
        self.page = SettingsScreen(self.screen.get_size(), self.close_page, self, self.wifi)

    def open_go(self) -> None:
        """Take over the screen with the page for going to an airport."""
        self.page = self.go_page(self.close_page)

    def go_page(self, close: Callable[[], None], adding: bool = False) -> GoScreen:
        """The GO page; or, adding, the same page for adding a favorite airport: airports
        only, and its key, ADD, adds the one chosen without moving home."""
        if adding:
            return GoScreen(
                self.screen.get_size(),
                close,
                self.center,
                self.favorite_airports,
                lambda: self.settings.home_airport,
                lambda airport, _: self.add_favorite_airport(airport),
                done_label="ADD",
                scope="airports",
            )
        return GoScreen(
            self.screen.get_size(),
            close,
            self.center,
            self.favorite_airports,
            lambda: self.settings.home_airport,
            self.go_to,
            here=self.current_location,
            go_here=self.go_here,
            flights=lambda: [a for a in self.store.feed().aircraft if not a.on_ground],
            go_flight=self.go_to_flight,
            find_flight=None if self.find_flight is None else self.find_flight_anywhere,
            favorite_aircraft=lambda: list(self.settings.favorites),
        )

    def add_aircraft_page(self, close: Callable[[], None]) -> GoScreen:
        """The GO page's search, for adding a favorite aircraft: aircraft only, those on the
        map and those seen lately, or what is typed; its key, ADD, adds it."""

        return GoScreen(
            self.screen.get_size(),
            close,
            self.center,
            lambda: [],
            lambda: self.settings.home_airport,
            lambda airport, _: None,
            flights=self.known_aircraft,
            done_label="ADD",
            scope="aircraft",
            add_aircraft=self.add_favorite_aircraft,
        )

    def favorite_whereabouts(self) -> dict[str, str]:
        """Where each favorite aircraft was last known to be, in words, by the nearest airport,
        with how long ago: "20 NM NE KPAO, 2h ago", or "KPAO, now". Only those this display
        has had a report of (from the area, the watch or a lookup). Worked out at most every
        few seconds: the settings page asks every frame."""
        now = time.monotonic()
        entries = tuple(self.settings.favorites)
        cached = self._whereabouts
        if cached is not None and cached[0] == entries and now - cached[1] < WHEREABOUTS_S:
            return cached[2]
        by_key = {favorites_module.normalize(e): e for e in entries}
        latest: dict[str, Aircraft] = {}
        for a in self.store.remembered():
            for name in (a.registration, a.callsign, a.hex):
                entry = by_key.get(favorites_module.normalize(name)) if name else None
                if entry is not None and (entry not in latest or latest[entry].seen_at < a.seen_at):
                    latest[entry] = a
        words = {}
        for entry, a in latest.items():
            place = airports_module.whereabouts(a.lat, a.lon)
            ago = now - a.seen_at
            when = "now" if ago < 60 else f"{fmt.age(ago)} ago"
            words[entry] = f"{place}, {when}" if place else when
        self._whereabouts = (entries, now, words)
        return words

    def known_aircraft(self) -> list[Aircraft]:
        """Every aircraft in the feed, then those seen lately, newest first: what the searches
        for adding a favorite aircraft offer, here and on the web page."""
        live = list(self.store.feed().aircraft)
        seen = {a.hex for a in live}
        return live + [a for a in reversed(self.store.remembered()) if a.hex not in seen]

    def set_key(self, field: str, text: str) -> str | None:
        """Keep a service's key as typed (empty removes it). Returns what is wrong, if it is."""
        if field not in secrets_store.KEY_FIELDS:
            return "Not a key this display keeps."
        key, error = secrets_store.check_key(text)
        if error:
            return error
        with self._settings_lock:
            changed = getattr(self.settings, field) != key
            setattr(self.settings, field, key)
            self._save_settings()
        if changed and field == "adsbx_key":
            self._reconfigure.set()  # it is one of the area feeds
        self._changed_elsewhere.set()
        return None

    def opensky(self) -> "keyed.OpenSky | None":
        """The OpenSky client, if a client ID and secret are set (made again if they change)."""
        s = self.settings
        credentials = (s.opensky_client_id, s.opensky_client_secret)
        if not self.keyed_services or not all(credentials):
            return None
        if self._opensky is None or self._opensky.credentials != credentials:
            self._opensky = keyed.OpenSky(*credentials)
        return self._opensky

    def aeroapi(self) -> "keyed.AeroApi | None":
        """The FlightAware AeroAPI client, if a key is set (made again if it changes)."""
        key = self.settings.flightaware_key
        if not self.keyed_services or not key:
            return None
        if self._aeroapi is None or self._aeroapi.key != key:
            self._aeroapi = keyed.AeroApi(key)
        return self._aeroapi

    def service_lines(self, group: str) -> list[tuple[str, bool, bool]]:
        """How a keyed service's last request went and how much of today's it has used, for
        the Feeds tab (and the web page's): nothing until its key is set."""
        client = {
            "OpenSky Network": self._opensky if self.opensky() else None,
            "FlightAware AeroAPI": self._aeroapi if self.aeroapi() else None,
            "ADS-B Exchange": self.adsbx,
        }.get(group)
        return client.usage.lines() if client is not None else []

    def set_portal_password(self, text: str) -> str | None:
        """Set the web page's password (empty removes it: the web page then shows only the
        preview). Signs everyone out of the web page. Returns what is wrong, if it is.
        Hashing takes most of a second on the Pi, so it is done on a worker thread, and
        password_saving says so meanwhile."""
        error = secrets_store.password_problem(text)
        if error:
            return error

        def work() -> None:
            stored = secrets_store.hash_password(text) if text else None
            with self._settings_lock:
                self.settings.portal_password = stored
                self._save_settings()
            self.password_saving = False

        self.password_saving = True
        threading.Thread(target=work, name="password", daemon=True).start()
        return None

    def add_favorite_aircraft(self, entry: str) -> None:
        """Add an aircraft to the favorites, as written, unless it is one already."""
        with self._settings_lock:
            listed = self.settings.favorites
            key = favorites_module.normalize(entry)
            full = len(listed) >= favorites_module.MAX_FAVORITES
            if full or any(favorites_module.normalize(e) == key for e in listed):
                return
            listed.append(entry)
            self._refresh_favorite_keys()
            self._save_settings()

    def remove_favorite_aircraft(self, entry: str) -> None:
        """Take an aircraft out of the favorites, and its pin with it."""
        with self._settings_lock:
            if entry in self.settings.favorites:
                self.settings.favorites.remove(entry)
                self._refresh_favorite_keys()
                self._save_settings()

    def open_photo(self, index: int = 0) -> None:
        """Fill the screen with one of the selected aircraft's pictures."""
        hex_code = self.selected_hex
        if self.enrich is None or hex_code is None:
            return
        aircraft = next((a for a in self.store.feed().aircraft if a.hex == hex_code), None)
        pictures = self.enrich.photos(hex_code)
        if aircraft is None or not isinstance(pictures, tuple) or index >= len(pictures):
            return
        self.page = PhotoScreen(
            self.screen.get_size(),
            self.close_page,
            aircraft.label,
            pictures,
            lambda n: self.enrich.full_photo(hex_code, n),
            index,
        )

    def close_page(self) -> None:
        self.page = None
        self._press_pos = None
        self._sync_controls()

    def show_list(self) -> None:
        self.stop_following()
        self.selected_hex = None
        self.selected_airport = None
        self.candidates = []
        self.show_diagnostics = False
        self._sync_controls()

    # ---- location and feeds ---------------------------------------------------------------

    def configure_feeds(self) -> None:
        """Work out where home is and (re)build the feeds to match. Main thread only.

        Called at start-up and again whenever the location or the feed settings change. The
        first time a real location is known the map goes back to where it was last dragged;
        after any later change it is centered on home.
        """
        for poller in self.pollers:
            poller.stop()
        self.store = Store()
        self.store.on_answer = self._on_answer
        self.pollers = []
        self.selected_hex = None
        self.watch: Watch | None = None  # the selected aircraft and favorites, wherever they are
        # The last word on the selected and pinned aircraft, kept for when they drop out of
        # the feed: they are then drawn where they were last heard of, grayed.
        self._kept: dict[str, Aircraft] = {}
        self._lost: frozenset[str] = frozenset()
        # Replacement pollers wait a moment so two requests never land back to back.
        delay = MIN_SPACING_S if self._started else 0.0

        with self._settings_lock:
            chosen = location_module.configured_location(self.settings)
            receiver_url = self.settings.local_receiver_url
        self.location: Location | None = None if self._force_demo else (chosen or self._estimate)
        # With no location there is nowhere to ask about, so show the demo until there is one.
        self.demo = self.location is None
        if self.demo:
            demo_feed = ReplayFeed(self._replay_file)
            self.home = demo_feed.center
            self.pollers.append(FeedPoller(demo_feed, self.store, self.public_plan, delay))
            self.attribution_source = None
        else:
            self.home = self.location.point
            key = self.settings.adsbx_key if self.keyed_services else None
            self.adsbx = keyed.AdsbxFeed(key) if key else None
            self.attribution_source = public_feed([self.adsbx] if self.adsbx else [])
            self.pollers.append(
                FeedPoller(self.attribution_source, self.store, self.public_plan, delay)
            )
            if receiver_url:
                self.pollers.append(
                    FeedPoller(local_feed(receiver_url), self.store, self.local_plan, delay)
                )
            self.watch = Watch(self.store)
            self.pollers.append(FeedPoller(self.watch, self.store, self.watch_plan, delay))
        log.info(
            "home %s, feeds %s",
            f"{self.location.label} ({self.location.source})" if self.location else "unknown: demo",
            ", ".join(p.name for p in self.pollers),
        )
        if self._started:
            for poller in self.pollers:
                poller.start()

        self.view_center = None
        if not self.demo:
            saved = (self.settings.view_lat, self.settings.view_lon)
            if not self._view_restored and None not in saved:
                self.view_center = saved
            else:
                self._set_view(None)  # also forgets a saved position that no longer applies
            self._view_restored = True
        self._zoom_latitude = self.center[0]

        # Asked for even when a home airport is set: "Current Location" is always on offer
        # among the favorites, and it needs to say where that is.
        needs_estimate = self._estimate is None and not self._force_demo
        if needs_estimate and self._locate is not None and not self._locating:
            self._locating = True
            threading.Thread(target=self._run_locate, name="locate", daemon=True).start()

    def _run_locate(self) -> None:
        try:
            self._estimate = self._locate()
        except Exception as exc:  # never let a lookup failure kill the thread silently
            log.warning("location lookup failed: %s", exc)
        finally:
            self._locating = False
        with self._settings_lock:
            chosen = location_module.configured_location(self.settings)
        if self._estimate is not None and chosen is None:
            self._reconfigure.set()  # it is where home is; with a home chosen, nothing moves

    def portal_state(self) -> PortalState:
        """Called from the portal thread."""
        with self._settings_lock:
            snapshot = settings_module.Settings(**vars(self.settings))
            snapshot.favorites = list(self.settings.favorites)
            snapshot.favorite_airports = list(self.settings.favorite_airports)
        location, here = self.location, self._estimate
        categories = {}
        for ident in snapshot.favorite_airports:
            report = self.metar.lookup(ident) if self.metar is not None else None
            if getattr(report, "category", None):
                categories[ident] = report.category
        return PortalState(
            snapshot,
            (location.place or location.label) if location else None,
            location.source if location else None,
            (location.lat, location.lon) if location else None,
            (here.place or here.label) if here else None,
            categories,
            self.favorite_whereabouts(),
            {group: self.service_lines(group) for group, _, _ in secrets_store.KEY_GROUPS},
            keyed.usage_lines(),
        )

    def apply_changes(self, changes: dict[str, object]) -> None:
        """Apply settings submitted through the portal. Called from the portal thread."""
        with self._settings_lock:
            feeds_changed = any(
                name in changes and getattr(self.settings, name) != changes[name]
                for name in ("home_airport", "local_receiver_url", "adsbx_key")
            )
            for name, value in changes.items():
                setattr(self.settings, name, value)
            self._refresh_favorite_keys()
            self._save_settings()
        if feeds_changed:
            self._reconfigure.set()
        self._changed_elsewhere.set()

    def request_reset(self) -> None:
        """Reset all settings, asked for from the web page. Called from the portal thread:
        the values change here and now, and the main loop does the rest."""
        self._reset_values()
        self._reset_elsewhere = True
        self._changed_elsewhere.set()

    def catch_up(self) -> None:
        """Main loop: bring the controls and the view into line with settings changed from
        the web page."""
        if not self._changed_elsewhere.is_set():
            return
        self._changed_elsewhere.clear()
        if self._reset_elsewhere:
            self._reset_elsewhere = False
            self._after_reset()
        self._sync_controls()

    # ---- airports ------------------------------------------------------------------------

    def current_location(self) -> Location | None:
        """Where the internet address says the display is, once that is known. Always the
        first of the favorites, and never removable."""
        return self._estimate

    def current_location_label(self) -> str:
        here = self._estimate
        if here is None:
            return "Current Location (not known yet)"
        return f"Current Location ({here.place})" if here.place else "Current Location"

    def current_location_is_home(self) -> bool:
        return self.location is not None and self.location.source == "ip"

    def go_here(self, make_home: bool) -> None:
        """Go to the current location; with make_home, make it home again, which is to say
        have no home airport. The favorite airports stay as they are."""
        here = self._estimate
        if here is None:
            return
        self._select(None)
        if not make_home:
            self._set_view(here.point)
            self._wake_pollers()
            return
        with self._settings_lock:
            changed = self.settings.home_airport is not None
            self.settings.home_airport = None
            self._save_settings()
        if changed:
            self._reconfigure.set()
        else:
            self.recenter()

    def favorite_airports(self) -> list[Airport]:
        """The user's favorite airports, in the order they were added."""
        found = (airports_module.find(ident) for ident in self.settings.favorite_airports)
        return [airport for airport in found if airport is not None]

    def select_airport(self, airport: Airport | None) -> None:
        self._select(airport)

    def go_to(self, airport: Airport, add_to_favorites: bool) -> None:
        """Show an airport. With add_to_favorites, also save it as a favorite and make it home."""
        if add_to_favorites:
            self.choose_favorite_airport(airport)
        elif airport.ident == self.settings.home_airport:
            self.recenter()
        else:
            self._set_view(airport.point)
            self._wake_pollers()
        self.select_airport(airport)

    def go_to_flight(self, aircraft: Aircraft, add_to_favorites: bool) -> None:
        """Show a flight: center on it, select it and follow it. It may be one already on the
        map, one just found on the other side of the world, in which case it is put on the
        map here and now and the feed takes over once it is asked about that area, or one
        remembered from earlier: then it is shown where it was last heard of, as not
        updating, and followed, so the view goes with it if it is heard again."""
        if add_to_favorites:
            with self._settings_lock:
                if not favorites_module.is_favorite(aircraft, self.favorite_keys):
                    # By registration if known: a callsign belongs to a flight, not an airframe.
                    self.settings.favorites.append(aircraft.registration or aircraft.label)
                    self._refresh_favorite_keys()
                    self._save_settings()
        now = time.monotonic()
        live = not aircraft.is_stale(now)
        if live and all(a.hex != aircraft.hex for a in self.store.feed().aircraft):
            self.store.set_aircraft("search", [aircraft], now)
        self._select(aircraft.hex)
        if aircraft.hex not in self._kept or self._kept[aircraft.hex].seen_at < aircraft.seen_at:
            self._kept[aircraft.hex] = aircraft  # so it is shown even if never heard here
        self._with_lost_key = None
        self._set_view(aircraft.position_at(now))
        self.following = True
        self._wake_pollers()

    def choose_favorite_airport(self, airport: Airport) -> None:
        """Make a favorite airport home, adding it to the favorites if it is not one yet. The
        favorite chosen most recently is always home."""
        with self._settings_lock:
            if airport.ident not in self.settings.favorite_airports:
                self.settings.favorite_airports.append(airport.ident)
            changed = self.settings.home_airport != airport.ident
            self.settings.home_airport = airport.ident
            self._save_settings()
        if changed:
            self._reconfigure.set()
        else:
            self.recenter()

    def heart_armed(self) -> bool:
        """Whether a first tap on the home airport's heart is waiting for its second."""
        armed = self._heart_armed_at
        if armed is not None and time.monotonic() - armed > HOME_HEART_MAX_S:
            self._heart_armed_at = armed = None
        return armed is not None

    def tap_heart(self, airport: Airport) -> None:
        """The heart in the airport pane. For the home airport, taking it out of the favorites
        also moves home somewhere else, so that needs a second, deliberate tap: not one that
        follows instantly (a double tap), and not one long afterward."""
        is_home = airport.ident == self.settings.home_airport
        if not is_home:
            self.toggle_favorite_airport(airport)
        elif not self.heart_armed():
            self._heart_armed_at = time.monotonic()
        elif time.monotonic() - self._heart_armed_at >= HOME_HEART_MIN_S:
            self._heart_armed_at = None
            self.toggle_favorite_airport(airport)

    def add_favorite_airport(self, airport: Airport) -> None:
        """Add an airport to the favorites, if it is not one; home does not move."""
        if airport.ident not in self.settings.favorite_airports:
            self.toggle_favorite_airport(airport)

    def toggle_favorite_airport(self, airport: Airport) -> None:
        """The heart: add an airport to the favorites, or take it out. Adding one this way
        does not move home. Removing the home airport hands home to the favorite added most
        recently before it, or back to the estimate if none are left."""
        with self._settings_lock:
            favorites = self.settings.favorite_airports
            home_changed = False
            if airport.ident in favorites:
                favorites.remove(airport.ident)
                if self.settings.home_airport == airport.ident:
                    self.settings.home_airport = favorites[-1] if favorites else None
                    home_changed = True
            else:
                favorites.append(airport.ident)
            self._save_settings()
        if home_changed:
            self._reconfigure.set()

    def _px_to_nm(self, pixels: float) -> float:
        return pixels * self.settings.range_nm / self.layout.ring_radius

    def anchor(self) -> tuple[float, float]:
        """The point the map's overlays (airports, runways, airspace) are chosen around.

        It trails the center of the view, moving only once the view has gone a fair way from
        it, and the overlays reach a margin beyond the view. So what is painted into the map
        stays the same through a drag or while following, and the map need not be rebuilt.
        """
        center = self.center
        slack = self._px_to_nm(MARGIN_PX * 0.5)
        if self._anchor is None or distance_nm(*self._anchor, *center) > slack:
            self._anchor = (round(center[0], 3), round(center[1], 3))
        return self._anchor

    def overlay_radius(self) -> float:
        """How far from the anchor overlays are wanted: the view, plus the map's margin."""
        return self.settings.range_nm * self.layout.corner_factor + self._px_to_nm(MARGIN_PX * 1.6)

    def visible_airports(self) -> tuple[Airport, ...]:
        """The airports to mark on the map: every strip up to 10 NM, only the large and
        medium ones to 50 NM, only the large beyond, and the favorites always."""
        range_nm = self.settings.range_nm
        sizes = "LMSW" if range_nm <= 10 else ("LM" if range_nm <= 50 else "L")
        lat, lon = self.anchor()
        radius = self.overlay_radius()
        shown = airports_module.near(lat, lon, radius, sizes)
        listed = {airport.ident for airport in shown}
        extra = tuple(
            a
            for a in self.favorite_airports()
            if a.ident not in listed and distance_nm(lat, lon, a.lat, a.lon) <= radius
        )
        return shown + extra

    def _airport_at_home(self) -> bool:
        """Whether one of the airports marked on the map is where home is. Its marker then
        says so, and a second one on top of it would only smudge its name."""
        lat, lon = self.home
        return any(
            abs(a.lat - lat) < 0.01 and abs(a.lon - lon) < 0.01 for a in self.visible_airports()
        )

    def _label_obstacles(self) -> tuple[pygame.Rect, ...]:
        """Where aircraft labels must not go: over the airport idents painted into the map,
        or under the buttons that sit on it. Reworked out only when the view moves."""
        marked = self.visible_airports()
        zoom, view = self.zoom(), self.layout.view
        key = (marked, self.center, zoom, self.view_center is not None)
        if key != self._label_rects[0]:
            small = self.fonts.small
            rects = [
                self.range_down.rect,
                self.range_up.rect,
                self.go_button.rect,
                self.settings_button.rect,
            ]
            if self.view_center is not None:
                rects.append(self.home_button.rect)
            for airport in marked:
                x, y = screen_xy(self.center, airport.lat, airport.lon, zoom, view)
                if view.collidepoint(x, y):
                    width, height = small.size(airport.ident)
                    rects.append(pygame.Rect(x - width // 2 - 2, y - 8, width + 4, height + 22))
            self._label_rects = (key, tuple(rects))
        return self._label_rects[1]

    def airports_near(self, pos: tuple[int, int]) -> list[tuple[float, Airport]]:
        """Airports whose markers are within reach of a tap, as (distance in pixels, airport)."""
        zoom, view = self.zoom(), self.layout.view
        reach = tap_radius(AIRPORT_TAP_PX, self.settings.range_nm)
        found = []
        for airport in self.visible_airports():
            x, y = screen_xy(self.center, airport.lat, airport.lon, zoom, view)
            off = hypot(x - pos[0], y - pos[1])
            if off <= reach:
                found.append((off, airport))
        return found

    def _select(self, target: Aircraft | Airport | str | None) -> None:
        """Select an aircraft (or its hex), an airport, or nothing."""
        hex_code = target.hex if isinstance(target, Aircraft) else target
        self._heart_armed_at = None
        if not (isinstance(hex_code, str) and hex_code == self.selected_hex):
            self.stop_following()  # following is of one aircraft; a new choice ends it
        self.candidates = []
        self.show_diagnostics = False
        if isinstance(target, Airport):
            self.selected_airport, self.selected_hex = target, None
        else:
            self.selected_airport = None
            self.selected_hex = target.hex if isinstance(target, Aircraft) else target
        self._sync_controls()

    @property
    def center(self) -> tuple[float, float]:
        """The middle of the map view: home, unless the map has been dragged."""
        return self.view_center or self.home

    def public_plan(self) -> QueryPlan:
        """What to ask a public feed for: enough to fill the view at the selected range."""
        return plan_query(self.center, self.settings.range_nm, self.layout.corner_factor)

    def watch_plan(self) -> QueryPlan:
        """The watch asks about particular aircraft, not an area: only its interval counts."""
        return QueryPlan(*self.home, 0.0, WATCH_INTERVAL_S)

    def _update_watch(self) -> None:
        """Main thread: keep the watch on the selected aircraft and the favorites, and ask
        at once when that changes, so a newly selected one is kept up to date even if the
        map is dragged away from it straight after."""
        if self.watch is not None and self.watch.set(self.selected_hex, self.settings.favorites):
            for poller in self.pollers:
                if poller.name == "feed-watch":
                    poller.wake()

    def local_plan(self) -> QueryPlan:
        """A local receiver reports everything it hears, whatever we ask for."""
        return QueryPlan(*self.home, 0.0, LOCAL_INTERVAL_S)

    def _wake_pollers(self) -> None:
        for poller in self.pollers:
            poller.wake()

    # ---- panning --------------------------------------------------------------------------

    def zoom(self) -> float:
        """The map zoom at which the selected range spans the outer ring.

        It depends on latitude, which a drag changes. To keep the scale steady under the
        finger, the latitude it uses is only brought up to date when a drag ends.
        """
        return zoom_for_range(self._zoom_latitude, self.settings.range_nm, self.layout.ring_radius)

    def toggle_favorite_aircraft(self, aircraft: Aircraft) -> None:
        """The heart in the aircraft pane: add it to the favorite aircraft (by registration if
        known: a callsign belongs to a flight, not an airframe), or take out every favorite
        that matches it."""
        with self._settings_lock:
            entries = self.settings.favorites
            if favorites_module.is_favorite(aircraft, self.favorite_keys):
                mine = favorites_module.keys(
                    [v for v in (aircraft.registration, aircraft.callsign, aircraft.hex) if v]
                )
                entries[:] = [e for e in entries if favorites_module.normalize(e) not in mine]
            else:
                entries.append(aircraft.registration or aircraft.label)
            self._refresh_favorite_keys()
            self._save_settings()

    def _refresh_favorite_keys(self) -> None:
        """Settings lock held: bring the favorite and pinned keys up to date with the lists,
        and forget pins on favorites that are no longer favorites."""
        s = self.settings
        s.pinned_favorites = [e for e in s.pinned_favorites if e in s.favorites]
        self.favorite_keys = favorites_module.keys(s.favorites)
        self.pinned_keys = favorites_module.keys(s.pinned_favorites)

    def is_pinned(self, aircraft: Aircraft) -> bool:
        """Whether its track stays on the map: a favorite with its pin on. Only favorites
        are pinned, so a pinned aircraft can always be found to unpin (the settings pages
        list them)."""
        return favorites_module.is_favorite(aircraft, self.pinned_keys)

    def can_pin(self, aircraft: Aircraft) -> bool:
        return favorites_module.is_favorite(aircraft, self.favorite_keys)

    def find_flight_anywhere(self, text: str) -> Aircraft | None:
        """Worker thread: a flight among everything the public feed tracks now; failing that,
        the latest of where it was last seen: by this display, by the local receiver in the
        hours it keeps, or by adsb.lol in the last few days. Raises only if nothing was found
        and something could not be asked."""
        trouble: Exception | None = None
        try:
            live = self.find_flight(text) if self.find_flight is not None else None
        except Exception as exc:
            live, trouble = None, exc
        if live is not None:
            return live
        candidates, _ = match_flights(text, self.store.remembered())
        if self.find_history is not None:
            with self._settings_lock:
                receiver = self.settings.local_receiver_url
            known = max(candidates, key=lambda a: a.seen_at).hex if candidates else None
            try:
                candidates += self.find_history(text, receiver, known)
            except Exception as exc:
                trouble = trouble or exc
        opensky = self.opensky()
        if opensky is not None:
            # Where it last landed, from yesterday's flights and before.
            try:
                hex_code = candidates[0].hex if candidates else history_module.hex_for(text)
                landed = opensky.last_landing(hex_code) if hex_code else None
                if landed is not None:
                    candidates.append(landed)
            except Exception as exc:
                trouble = trouble or exc
        if candidates:
            return max(candidates, key=lambda a: a.seen_at)
        if trouble is not None:
            raise trouble
        return None

    def toggle_pinned_favorite(self, entry: str) -> None:
        """Pin a favorite's track, or unpin it: from the settings pages."""
        with self._settings_lock:
            s = self.settings
            if entry in s.pinned_favorites:
                s.pinned_favorites.remove(entry)
            elif entry in s.favorites:
                s.pinned_favorites.append(entry)
            self._refresh_favorite_keys()
            self._save_settings()

    def tap_pin(self, aircraft: Aircraft) -> None:
        """The pin in the aircraft pane: on a favorite, the same switch as on the settings
        pages. On anything else it does nothing (it is drawn dimmed): make it a favorite
        first."""
        if not self.can_pin(aircraft):
            return
        mine = favorites_module.keys(
            [v for v in (aircraft.registration, aircraft.callsign, aircraft.hex) if v]
        )
        on = self.is_pinned(aircraft)
        with self._settings_lock:
            s = self.settings
            if on:
                s.pinned_favorites = [
                    e for e in s.pinned_favorites if favorites_module.normalize(e) not in mine
                ]
            else:
                s.pinned_favorites += [
                    e
                    for e in s.favorites
                    if favorites_module.normalize(e) in mine and e not in s.pinned_favorites
                ]
            self._refresh_favorite_keys()
            self._save_settings()

    def toggle_following(self) -> None:
        if self.following:
            self.stop_following()
        elif self.selected_hex is not None:
            self.following = True

    def stop_following(self) -> None:
        """Leave follow mode. The map stays where the aircraft had got to."""
        if self.following:
            self.following = False
            self._set_view(self.view_center)
            self._wake_pollers()

    def pan(self, dx: int, dy: int) -> None:
        """Drag the map by this many pixels."""
        self.stop_following()  # the user has taken the map back
        self.view_center = shifted(self.center, -dx, -dy, self.zoom())

    def _set_view(self, center: tuple[float, float] | None) -> None:
        """Settle the view on a center (None for home) and remember it across restarts."""
        self.view_center = center
        self._zoom_latitude = self.center[0]
        lat, lon = center if center is not None else (None, None)
        with self._settings_lock:
            if (self.settings.view_lat, self.settings.view_lon) != (lat, lon):
                self.settings.view_lat, self.settings.view_lon = lat, lon
                self._save_settings()

    def recenter(self) -> None:
        self.following = False
        self._set_view(None)
        self._wake_pollers()

    # ---- input ----------------------------------------------------------------------------

    def handle_tap(self, pos: tuple[int, int]) -> None:
        """A tap that no control claimed."""
        if self.layout.view.collidepoint(pos):
            # A tap on one aircraft or airport selects it; on empty map, clears the selection;
            # on several things close together, lists them in the panel to choose from.
            # The reach shrinks with the icons at wide ranges, and one target clearly nearer
            # than the rest is taken without asking; see resolve_tap.
            reach = tap_radius(TAP_RADIUS_PX, self.settings.range_nm)
            near: list[tuple[float, object]] = list(aircraft_near(self.positions, pos, reach))
            near += self.airports_near(pos)
            verdict, targets = resolve_tap(near)
            if verdict == "choose":
                self._select(None)
                self.candidates = [
                    ("airport" if isinstance(target, Airport) else "aircraft", target)
                    for target in targets
                ]
            else:
                self._select(targets[0] if targets else None)
        elif self.candidates and not self.show_diagnostics:
            for rect, target in self.candidate_targets:
                if rect.collidepoint(pos):
                    self._select(target)
                    break
        elif self.selected_airport is not None and not self.show_diagnostics:
            if panel.heart_rect(self.layout.panel).collidepoint(pos):
                self.tap_heart(self.selected_airport)
        elif self.selected_hex is not None and not self.show_diagnostics:
            buttons = panel.aircraft_buttons(self.layout.panel)
            if buttons.eye.collidepoint(pos):
                self.toggle_following()
            elif buttons.pin.collidepoint(pos) and self.selected_hex in self._kept:
                self.tap_pin(self._kept[self.selected_hex])
            elif buttons.heart.collidepoint(pos) and self.selected_hex in self._kept:
                self.toggle_favorite_aircraft(self._kept[self.selected_hex])
            for index, rect in enumerate(self.photo_targets):
                if rect.collidepoint(pos):
                    self.open_photo(index)
        elif self.selected_hex is None and not self.show_diagnostics:
            for rect, hex_code in self.list_targets:
                if rect.collidepoint(pos):
                    self.selected_hex = hex_code
                    break
        self._sync_controls()

    def _controls_handle(self, event: pygame.event.Event) -> bool:
        return any([button.handle_event(event) for button in self.buttons])

    def handle_event(self, event: pygame.event.Event) -> bool:
        """Returns False when the app should quit."""
        if event.type == pygame.QUIT:
            return False
        if self.page is not None:
            self.page.handle_event(event)
            return True
        if event.type == pygame.KEYDOWN:
            if event.key in (pygame.K_ESCAPE, pygame.K_q):
                return False
            if event.key in (pygame.K_PLUS, pygame.K_EQUALS, pygame.K_KP_PLUS):
                self.step(1)
            elif event.key in (pygame.K_MINUS, pygame.K_KP_MINUS):
                self.step(-1)
        if event.type == pygame.FINGERDOWN:
            self.finger_events += 1
        if event.type in (pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP) and event.button == 1:
            down = event.type == pygame.MOUSEBUTTONDOWN
            source = "touch" if getattr(event, "touch", False) else "mouse"
            self.last_pointer = f"{source} {'down' if down else 'up'} {event.pos[0]},{event.pos[1]}"
            consumed = self._controls_handle(event)
            if down:
                self._press_pos = None if consumed else event.pos
                self._panning = False
            elif self._panning:
                self._panning = False
                self._press_pos = None
                self._set_view(self.view_center)
                self._wake_pollers()  # fetch traffic for the area now in view
            elif self._press_pos is not None:
                moved = hypot(event.pos[0] - self._press_pos[0], event.pos[1] - self._press_pos[1])
                self._press_pos = None
                if not consumed and moved <= TAP_SLOP_PX:
                    self.handle_tap(event.pos)
        elif event.type == pygame.MOUSEMOTION and event.buttons[0]:
            if self._controls_handle(event) or self._press_pos is None:
                return True
            if not self._panning:
                far = hypot(event.pos[0] - self._press_pos[0], event.pos[1] - self._press_pos[1])
                # Only a drag that began on the map moves it.
                if far > TAP_SLOP_PX and self.layout.view.collidepoint(self._press_pos):
                    self._panning = True
                    self._press_pos = event.pos
            else:
                self.pan(event.pos[0] - self._press_pos[0], event.pos[1] - self._press_pos[1])
                self._press_pos = event.pos
        return True

    # ---- display state --------------------------------------------------------------------

    def address(self, now: float) -> str | None:
        if now - self._address_checked_at > ADDRESS_REFRESH_S:
            self._address = local_address()
            self._address_checked_at = now
        return self._address

    def refresh_network(self) -> None:
        """Re-read which Wi-Fi network is in use. Runs a command, so not on the main thread."""
        self.network_name = self.wifi.current() if self.wifi.available else None

    def _watch_network(self) -> None:
        while not self._network_stop.is_set():
            self.refresh_network()
            self._network_stop.wait(NETWORK_REFRESH_S)

    def network_summary(self, now: float) -> str:
        """The network in use with this device's address, for the settings page."""
        address = self.address(now)
        if self.network_name and address:
            return f"{self.network_name} ({address})"
        return self.network_name or address or "Not connected"

    def portal_url(self, now: float) -> str | None:
        address = self.address(now)
        if address is None or self.portal is None or self.portal.port is None:
            return None
        return f"http://{address}" + ("" if self.portal.port == 80 else f":{self.portal.port}")

    def daylight_now(self, now: float) -> float:
        """How light it is at the center of the view, 0 (night) to 1 (day), in coarse steps.
        1 if the effect is switched off."""
        if not self.settings.day_night:
            return 1.0
        if now - self._daylight_checked > DAYLIGHT_REFRESH_S:
            self._daylight_checked = now
            exact = daylight(*self.center, time.time())
            self._daylight = round(exact * DAYLIGHT_STEPS) / DAYLIGHT_STEPS
        return self._daylight

    def update_backlight(self, now: float) -> None:
        """Keep the screen at the brightness the settings and the time of day call for, and
        keep the setting in step with the screen when something else changes it."""
        night = NIGHT_BACKLIGHT + (1 - NIGHT_BACKLIGHT) * self.daylight_now(now)
        usable = self.backlight is not None and self.backlight.writable

        # Something other than this app changed the backlight (a button on the display, say):
        # take that as the user's wish and move the setting to match.
        if usable and self._backlight_written is not None:
            if now - self._backlight_checked >= BACKLIGHT_POLL_S:
                self._backlight_checked = now
                actual = self.backlight.read_fraction()
                if actual is not None and abs(actual - self._backlight_written) > 0.006:
                    floor = FRACTION_SETTINGS["brightness"]
                    self._change("brightness", min(max(actual / night, floor), 1.0))
                    self._backlight_written = actual
                    self._backlight_level = round(self.settings.brightness * night, 2)
                    return

        level = round(self.settings.brightness * night, 2)
        if level != self._backlight_level:
            self._backlight_level = level
            if usable:
                self._backlight_written = self.backlight.set_fraction(level)

    def map_layers(self, now: float) -> tuple[LayerSpec, ...]:
        """The tile layers to draw behind the aircraft, bottom first."""
        if self.tile_store is None:
            return ()
        s = self.settings
        layers: list[LayerSpec] = []
        if s.show_map:
            light = self.daylight_now(now)
            shade = tuple(
                round((night + (day - night) * light) * s.map_brightness)
                for day, night in zip(MAP_DAY_SHADE, MAP_NIGHT_SHADE, strict=True)
            )
            if s.map_style == "street":
                layers.append(LayerSpec(STREET, shade=shade))
            else:
                layers.append(LayerSpec(IMAGERY, shade=shade))
            if s.map_style == "chart" and self.chart_in_range():
                # Over the imagery, not in place of it: where there is no chart (outside
                # the United States, or a tile not yet loaded) the map is not blank.
                layers.append(LayerSpec(CHART, shade=shade, optional=True))
        if self.weather is not None:
            weather = self.weather.current
            if s.show_clouds and weather.clouds is not None:
                strength = round(255 * s.weather_opacity * CLOUD_STRENGTH)
                layers.append(
                    LayerSpec(
                        weather.clouds,
                        shade=(strength,) * 3,
                        add_above=CLOUD_THRESHOLD,
                        family="clouds",
                    )
                )
            if s.show_radar and weather.radar is not None:
                layers.append(
                    LayerSpec(weather.radar, alpha=round(255 * s.weather_opacity), family="radar")
                )
        if s.show_map and s.map_style == "satellite":
            # Names and boundaries go on top of the weather so they stay readable. The
            # chart and the street map carry their own.
            layers.append(LayerSpec(PLACES, alpha=round(255 * s.feature_opacity)))
        return tuple(layers)

    def _airspaces(self) -> tuple[Airspace, ...]:
        """Airspace around the view, recomputed only when more has loaded or the view moved."""
        assert self.airspace is not None
        lat, lon = self.anchor()
        radius = self.overlay_radius() + 10
        key = (self.airspace.version, lat, lon, radius)
        if key != self._airspace_memo[0]:
            self._airspace_memo = (key, self.airspace.around(lat, lon, radius))
        return self._airspace_memo[1]

    def overlays(self) -> tuple[Overlay, ...]:
        """Vector layers painted into the map: airspace, then runways on top."""
        s = self.settings
        found: list[Overlay] = []
        if s.show_airspace and self.airspace is not None and s.range_nm <= AIRSPACE_RANGE_NM:
            airspaces = self._airspaces()
            if airspaces:
                labels = s.range_nm <= AIRSPACE_LABEL_RANGE_NM
                key = ("airspace", self.airspace.version, len(airspaces), labels)
                found.append(Overlay(key, airspace_painter(airspaces, self.fonts, labels)))
        if s.show_runways and s.range_nm <= RUNWAY_RANGE_NM:
            nearby = runways_module.near(*self.anchor(), self.overlay_radius() + 3)
            if nearby:
                opacity = s.feature_opacity
                painter = runway_painter(nearby, self.fonts, opacity)
                found.append(Overlay(("runways", nearby, opacity), painter))
        if self.show_diagnostics:
            found.append(self._reach_overlay())
        marked = self.visible_airports()
        if marked:
            favorites = frozenset(s.favorite_airports)
            selected = self.selected_airport.ident if self.selected_airport else None
            fade = round(s.feature_opacity, 2)
            key = ("airports", marked, favorites, s.home_airport, selected, fade, theme.NAME)
            painter = airport_painter(marked, self.fonts, favorites, s.home_airport, selected, fade)
            found.append(Overlay(key, painter))
        return tuple(found)

    def attribution(
        self,
        now: float,
        layers: tuple[LayerSpec, ...] | None = None,
        overlays: tuple[Overlay, ...] | None = None,
    ) -> str:
        """Credits for whatever is on screen, for the status bar. The frame passes in the
        layers and overlays it has already worked out."""
        credits = []
        source = getattr(self.attribution_source, "attribution", None)
        if source:
            credits.append(source)
        for spec in self.map_layers(now) if layers is None else layers:
            if spec.layer.attribution not in credits:
                credits.append(spec.layer.attribution)
        found = self.overlays() if overlays is None else overlays
        if any(overlay.key[0] == "airspace" for overlay in found) and "FAA" not in credits:
            credits.append("FAA")
        return " | ".join(credits)

    def _network_note(self, now: float) -> tuple[str, theme.Color]:
        """What the footer says about the connection."""
        if self.network_name:
            return f"Wi-Fi: {self.network_name}", theme.TEXT_DIM
        if self.address(now) is None:
            return "No network", theme.STATUS_STALE
        return ("No Wi-Fi", theme.STATUS_STALE) if self.wifi.available else ("", theme.TEXT_DIM)

    def diagnostics(self, now: float) -> list[str]:
        if self.backlight is None:
            backlight = "not found"
        else:
            access = "writable" if self.backlight.writable else "read-only"
            backlight = f"{self.backlight.name} ({access})"
        width, height = self.screen.get_size()
        plan = self.public_plan()
        lines = [
            f"Surface: {width} x {height}",
            f"Video: {pygame.display.get_driver()}",
            f"Session: {os.environ.get('XDG_SESSION_TYPE', 'unknown')}",
            f"Frame rate: {self.clock.get_fps():.0f} fps",
            f"Pointer: {self.last_pointer}",
            f"Finger events: {self.finger_events}",
            f"Backlight: {backlight}",
            f"Home: {self.location.label if self.location else 'demo'}",
            f"  {self.home[0]:.3f}, {self.home[1]:.3f}",
            f"Daylight: {self.daylight_now(now):.2f}",
            f"Portal: port {self.portal.port}"
            if self.portal and self.portal.port
            else "Portal: off",
            f"Query: {plan.radius_nm:.0f} NM every {plan.interval_s:.0f}s",
            # How much of the coverage map (the tint while this is open) is learned: places
            # with reports, with a floor, and with the height of their ground.
            "Coverage: {1} of {0}, {2} AGL".format(*self.reach.counts()),
        ]
        lines += [f"  {name}: {count}" for name, count in self.reach.source_counts().items()]
        for name, status in self.store.feed().sources.items():
            if status.updated_at is None:
                state = "no data yet"
            else:
                state = f"{status.count} ac, {now - status.updated_at:.0f}s ago"
            lines.append(f"Feed {name}: {state}")
            if status.error:
                lines.append(f"  {status.error[:34]}")
        if self.tile_store is not None:
            used, most = self.tile_store.memory_used
            lines.append(f"Tiles in memory: {used / 1e6:.0f} of {most / 1e6:.0f} MB")
            store = self.tile_store
            lines.append(f"Tile disk reads {store.disk_reads}, writes {store.disk_writes}")
            lines.append(f"Tiles fetched {store.fetches}, pre-loaded {self.prefetched_tiles}")
        if self.enrich is not None:
            lines.append(f"Aircraft lookups: {self.enrich.requests}")
        waiting = " (one waiting)" if self._unsaved_since is not None else ""
        lines.append(f"Settings writes: {self.settings_writes}{waiting}")
        return lines

    def _status(self, feed: FeedSnapshot, now: float) -> tuple[theme.Color, str]:
        if feed.updated_at is None:
            color = theme.STATUS_DOWN if feed.all_failing else theme.STATUS_STALE
            return color, "No data yet" if feed.all_failing else "Connecting..."
        age = now - feed.updated_at
        stamp = time.strftime("%H:%M:%S", time.localtime(time.time() - age))
        if feed.all_failing:
            return theme.STATUS_DOWN, f"Feed down since {stamp}"
        if age > STALE_WARNING_S:
            return theme.STATUS_STALE, f"Stale: updated {stamp}"
        return theme.STATUS_OK, f"Updated {stamp}"

    # ---- drawing --------------------------------------------------------------------------

    def draw(self, now: float) -> None:
        self._follow_theme()
        layout = self.layout
        feed = self.store.feed()
        range_nm = self.settings.range_nm
        self.update_backlight(now)
        self._update_watch()
        if self.page is not None:
            self.page.draw(self.screen, self.fonts, now)
            return

        if self.following:
            followed = next((a for a in feed.aircraft if a.hex == self.selected_hex), None)
            if followed is None or followed.is_stale(now):
                # Not being heard: stay on where it was last heard of, still following, so
                # the view goes with it again if it is heard again.
                followed = self._kept.get(self.selected_hex or "")
            if followed is None:
                self.stop_following()
            else:
                self.view_center = followed.position_at(now)
                if abs(self.view_center[0] - self._zoom_latitude) > FOLLOW_RESCALE_DEG:
                    self._zoom_latitude = self.view_center[0]

        # Old data must not pass for live: gray the traffic and say so across the map.
        data_age = None if feed.updated_at is None else now - feed.updated_at
        stale, banner = False, None
        if not self.demo:
            if data_age is None and feed.all_failing:
                banner = ("No aircraft data. Is the network up?", theme.STATUS_DOWN)
            elif data_age is not None and data_age > STALE_WARNING_S:
                stale = True
                color = theme.STATUS_DOWN if feed.all_failing else theme.STATUS_STALE
                banner = (f"Aircraft data is {fmt.age(data_age)} old", color)

        # Everything still fresh, with its distance from the center of the view, nearest first.
        center = self.center
        live = self._tracked(feed, center, now, stale)
        # Counts and the list are about what is flying; ground traffic only shows on the map.
        in_range = [
            (d.aircraft, d.distance_nm)
            for d in live
            if d.distance_nm <= range_nm and not d.aircraft.on_ground
        ]
        # The map also has the selected and pinned aircraft that have dropped out of the feed.
        tracked = self._with_lost_aircraft(live, center)
        chosen = next((d for d in tracked if d.aircraft.hex == self.selected_hex), None)
        selected = (chosen.aircraft, chosen.distance_nm) if chosen else None
        details = self._details(chosen.aircraft) if chosen else None
        if selected is None:
            self.stop_following()
            self.selected_hex = None  # never seen, so nothing to show of it
        self.store.keep_tracks(frozenset(self._kept))
        self._ask_trace(now)
        self._reach_upkeep(now)

        self.basemap.pump()
        layers, overlays = self.map_layers(now), self.overlays()
        background, offset = None, (0, 0)
        if layers or overlays:
            background, offset = self.basemap.render(
                self.center, self.zoom(), layers, now, overlays
            )

        # No fill of the whole screen: the bars, the map and the panel cover it between them.
        scene = Scene(
            home=self.home,
            center=center,
            zoom=self.zoom(),
            range_nm=range_nm,
            ring_opacity=self.settings.ring_opacity,
            selected_hex=self.selected_hex,
            favorites=self.favorite_keys,
            tracks=tuple(
                (hex_code, self.store.track(hex_code))
                for hex_code in sorted(self._pinned_here | {self.selected_hex} - {None})
            ),
            background=background,
            background_offset=offset,
            stale=stale,
            banner=banner,
            highlighted=frozenset(t for kind, t in self.candidates if kind == "aircraft"),
            feature_opacity=self.settings.feature_opacity,
            mark_home=not self._airport_at_home(),
            obstacles=self._label_obstacles(),
            note=self._map_words(now, chosen),
        )
        self.positions = draw_scope(
            self.screen,
            layout.view,
            layout.ring_radius,
            self.fonts,
            scene,
            tracked,
            now,
            self._labels,
        )
        # A label sweeping from one side of its aircraft to the other needs the full frame
        # rate until it gets there, touched or not.
        self._active_until = max(self._active_until, self._labels.busy_until)
        self._draw_map_note(now)
        self._prefetch_map(now, layers)
        s = self.settings
        beside = self.home_button if self.view_center is not None else self.range_up
        left = beside.rect.right + 8
        right = self.go_button.rect.left - 8
        area = pygame.Rect(left, layout.view.bottom - 44, right - left, 38)
        if self.show_diagnostics:
            bars.draw_reach_legend(self.screen, area, self.fonts)
        elif s.show_radar and s.show_radar_key and self.weather is not None:
            bars.draw_weather_legend(
                self.screen, area, self.fonts, self.weather.current.radar_colors
            )

        self.list_targets = []
        self.candidate_targets = []
        self.photo_targets = []
        if self.show_diagnostics:
            body = panel.draw_frame(self.screen, layout.panel, self.fonts, "DIAGNOSTICS")
            panel.draw_lines(self.screen, body, self.fonts, self.diagnostics(now))
        elif self.candidates:
            by_hex = {d.aircraft.hex: d.aircraft for d in tracked}
            listed = [
                target if kind == "airport" else by_hex[target]
                for kind, target in self.candidates
                if kind == "airport" or target in by_hex  # an aircraft may have gone since
            ]
            body = panel.draw_frame(self.screen, layout.panel, self.fonts, "WHICH ONE?")
            body.height = self.back_button.rect.top - 6 - body.top  # keep clear of the button
            self.candidate_targets = panel.draw_candidates(
                self.screen,
                body,
                self.fonts,
                listed,
                self.favorite_keys,
                frozenset(self.settings.favorite_airports),
            )
        elif self.selected_airport is not None and selected is None:
            airport = self.selected_airport
            body = panel.draw_frame(self.screen, layout.panel, self.fonts, "AIRPORT")
            report = self.metar.lookup(airport.ident) if self.metar else metar_module.NONE
            panel.draw_airport(
                self.screen,
                layout.panel,
                body,
                self.fonts,
                airport,
                distance_nm(*center, *airport.point),
                bearing_deg(*center, *airport.point),
                airport.ident in self.settings.favorite_airports,
                airport.ident == self.settings.home_airport,
                report,
                time.time(),
                self.heart_armed(),
            )
        elif selected is not None:
            aircraft, dist = selected
            # No heading: its row holds the pane's buttons.
            body = panel.draw_frame(self.screen, layout.panel, self.fonts, "")
            body.height = self.back_button.rect.top - 6 - body.top  # keep clear of the button
            # Distance and bearing are from the center of the display, like everything else.
            # While following, the aircraft *is* the center, so they would always read
            # zero: then they are given from home instead, and labeled so.
            origin = self.home if self.following else center
            position = aircraft.position_at(now)
            dist = distance_nm(*origin, *position) if self.following else dist
            bearing = bearing_deg(*origin, *position)
            silent = now - aircraft.seen_at
            heard = aircraft.hex not in self._lost and silent <= SILENT_AIRCRAFT_S
            self.photo_targets = panel.draw_selected(
                self.screen,
                body,
                self.fonts,
                aircraft,
                dist,
                bearing,
                now,
                self.favorite_keys,
                self.following,
                panel.aircraft_buttons(layout.panel),
                "HOME" if self.following else "DST",
                details,
                self.is_pinned(aircraft),
                None if heard else silent,
                self.can_pin(aircraft),
            )
        else:
            body = panel.draw_frame(self.screen, layout.panel, self.fonts, "NEAREST")
            self.list_targets = panel.draw_nearest(
                self.screen, body, self.fonts, in_range, self.favorite_keys
            )

        age = None if feed.updated_at is None else now - feed.updated_at
        bars.draw_top(
            self.screen, layout.top, self.fonts, self.controls_right, len(in_range), age, self.demo
        )
        status_color, status_text = self._status(feed, now)
        bars.draw_bottom(
            self.screen,
            layout.bottom,
            self.fonts,
            f"RANGE {range_nm:g} NM",
            status_text,
            status_color,
            self.attribution(now, layers, overlays),
            *self._network_note(now),
            right_edge=self.diagnostics_pill.face.left - 10,
        )

        for button in self.buttons:
            button.tick()
            button.draw(self.screen, self.fonts.body)

    def _ask_trace(self, now: float) -> None:
        """Main thread: gather the selected aircraft's flight so far, once (again after a
        while), on a worker thread, from every source there is; it goes in front of the
        track the display recorded, as far back as it is the same flight (legs.py). The free
        sources are asked first; the keyed ones only while the track does not yet reach
        back to where it took off, FlightAware (paid by the request) last and seldom."""
        hex_code = self.selected_hex
        if self.find_trace is None or hex_code is None:
            return
        asked = self._traces_asked.get(hex_code)
        if asked is not None and now - asked < TRACE_AGAIN_S:
            return
        self._traces_asked[hex_code] = now
        with self._settings_lock:
            receiver = self.settings.local_receiver_url
        store, find, opensky, aeroapi = self.store, self.find_trace, self.opensky(), self.aeroapi()
        find_local = self.find_receiver_track if receiver else None
        plane = self._kept.get(hex_code) or next(
            (a for a in store.feed().aircraft if a.hex == hex_code), None
        )
        ident = plane and (plane.registration or plane.callsign)
        last_paid = self._paid_traces_asked.get(hex_code)
        if (
            aeroapi is not None
            and ident
            and (last_paid is None or now - last_paid >= PAID_TRACE_AGAIN_S)
        ):
            self._paid_traces_asked[hex_code] = now
        else:
            aeroapi = None

        def took_off() -> bool:
            return legs.took_off(store.history(hex_code), self._low_beside)

        def ask(name: str, fetch: Callable[[], list]) -> None:
            try:
                points = fetch()
            except Exception as exc:  # no trace is no loss: the track starts where we saw it
                log.info("no earlier track for %s from %s: %s", hex_code, name, exc)
                return
            if points:
                store.set_past(hex_code, points)

        def work() -> None:
            ask("adsb.lol", lambda: find(hex_code))
            if find_local is not None:
                ask("the local receiver", lambda: find_local(receiver, hex_code))
            if opensky is not None and not took_off():
                ask("OpenSky", lambda: opensky.track(hex_code))
            if aeroapi is not None and not took_off():
                ask("FlightAware", lambda: aeroapi.track(ident))

        threading.Thread(target=work, name="trace", daemon=True).start()

    def _low_beside(self, point: legs.TimedPoint) -> bool:
        """Whether a track point is low beside an airport, down where the providers hearing
        now stop hearing aircraft there."""
        found = reach_module.low_beside(self.reach, *point[:3], self._hearing())
        return found is not None

    def _hearing(self) -> set[str]:
        """The providers that have answered lately: those whose floors count now."""
        now = time.monotonic()
        return {p for p, at in list(self._answered.items()) if now - at < STALE_AFTER_S}

    def _on_answer(self, provider: str, aircraft: list[Aircraft], now: float) -> None:
        """A feed thread, with every answer: count it into that provider's map of how low
        it hears. Not the demo, nor a flight looked up by hand."""
        if self.demo or provider in ("search", "replay"):
            return
        self._answered[provider] = now
        self.reach.add(provider, aircraft, now)

    def _reach_upkeep(self, now: float) -> None:
        """Main thread: ask for the ground under the coverage map now and then, and write
        the map now and then, on worker threads."""
        if self.demo:
            return
        self._ask_ground(now)
        if now - self._reach_saved_at > REACH_SAVE_S:
            self._reach_saved_at = now
            threading.Thread(target=self._save_reach, name="reach", daemon=True).start()

    def _ask_ground(self, now: float) -> None:
        """Ask, on a worker thread, for the height of the ground in the coverage map's cells
        that have a floor but no ground yet: a batch at most every GROUND_ASK_S."""
        if self.find_ground is None or self._ground_busy:
            return
        if now - self._ground_asked_at < GROUND_ASK_S:
            return
        cells = self.reach.missing_ground()
        if not cells:
            return
        self._ground_asked_at, self._ground_busy = now, True
        find, reach = self.find_ground, self.reach

        def work() -> None:
            try:
                reach.set_ground(find(cells))
            except Exception as exc:  # tried again in a minute
                log.info("no ground heights for the coverage map: %s", exc)
            finally:
                self._ground_busy = False

        threading.Thread(target=work, name="ground", daemon=True).start()

    def _save_reach(self) -> None:
        try:
            self.reach.save(self.data_dir / REACH_FILE)
        except OSError as exc:
            log.warning("could not keep the coverage map: %s", exc)

    def _reach_overlay(self) -> Overlay:
        """The tint of how low the feeds hear, for diagnostics: repainted at most every
        REACH_REPAINT_S, so that the map is not rebuilt for every new low report."""
        version, at = self._reach_shown
        now = time.monotonic()
        if self.reach.version != version and now - at >= REACH_REPAINT_S:
            self._reach_shown = version, at = self.reach.version, now
        lat, lon = self.anchor()
        radius = self.overlay_radius()
        key = ("reach", version, (lat, lon), round(radius, 1))
        if self._reach_overlay_cache is None or self._reach_overlay_cache.key != key:
            dlat = radius / 60
            dlon = dlat / max(cos(radians(lat)), 0.05)
            floors = self.reach.floors(lat - dlat, lon - dlon, lat + dlat, lon + dlon)
            self._reach_overlay_cache = Overlay(key, reach_painter(floors))
        return self._reach_overlay_cache

    def _save_tracks(self) -> None:
        """On the way out: the tracks of the aircraft heard lately, so that after a restart
        each takes up where it was (if it is still the same flight)."""
        mono, wall = time.monotonic(), time.time()
        tracks = self.store.export(mono - legs.SAME_FLIGHT_S)
        shift = wall - mono
        data = {
            "saved": wall,
            "tracks": {
                h: [[round(a, 5), round(b, 5), c, round(t + shift, 1)] for a, b, c, t in pts]
                for h, pts in tracks.items()
            },
        }
        try:
            path = self.data_dir / TRACKS_FILE
            path.write_bytes(gzip.compress(json.dumps(data, separators=(",", ":")).encode()))
        except OSError as exc:
            log.warning("could not keep the tracks: %s", exc)

    def _restore_tracks(self) -> None:
        """At start-up: the tracks kept on the way out, if they are recent enough to be of
        flights still under way. The file is read once and removed."""
        path = self.data_dir / TRACKS_FILE
        try:
            data = json.loads(gzip.decompress(path.read_bytes()))
        except (OSError, ValueError, EOFError):
            return
        finally:
            path.unlink(missing_ok=True)
        mono, wall = time.monotonic(), time.time()
        if not isinstance(data, dict) or not wall - data.get("saved", 0) < legs.MAX_HOLE_S:
            return
        shift = mono - wall
        saved = {}
        for hex_code, points in (data.get("tracks") or {}).items():
            try:
                saved[hex_code] = [
                    (float(a), float(b), c, float(t) + shift) for a, b, c, t in points
                ]
            except (TypeError, ValueError):
                continue
        self.store.restore(saved)

    def departure(self, hex_code: str) -> str:
        """Where the aircraft's track starts, in words: "From KPAO" when that is at an
        airport, else "First seen 20 NM NE KSJC" (it was first seen in the air)."""
        track = self.store.track(hex_code)
        if not track:
            return ""
        lat, lon, altitude = track[0]
        key = (hex_code, lat, lon)
        if key not in self._departures:
            # From an airport if it was on the ground there, or first heard low beside it,
            # where the feeds hear a climb out begin; else where it was first heard.
            beside = reach_module.low_beside(self.reach, lat, lon, altitude, self._hearing())
            place = airports_module.whereabouts(lat, lon)
            words = (
                f"From {beside.ident}"
                if beside is not None
                else f"First seen {place}"
                if place
                else ""
            )
            if len(self._departures) > 64:
                self._departures.clear()
            self._departures[key] = words
        return self._departures[key]

    def _details(self, aircraft: Aircraft) -> panel.Details | None:
        """What has been looked up about the selected aircraft, for its pane. Asking is what
        starts the looking up; until an answer comes there is simply less to show."""
        if self.enrich is None:
            return panel.Details(departure=self.departure(aircraft.hex))
        found = self.enrich.route(aircraft.callsign)
        airframe = self.enrich.airframe(aircraft.hex)
        pictures = self.enrich.photos(aircraft.hex, aircraft.registration, aircraft.type_code)

        operator, origin, destination = "", None, None
        aero = self.aeroapi()
        flight = aero.flight(aircraft.callsign or aircraft.registration or "") if aero else None
        if isinstance(flight, keyed.Flight) and flight.origin and flight.destination:
            # The flight it is actually on, from FlightAware: better than an expected route.
            operator, origin, destination = flight.operator, flight.origin, flight.destination
        elif isinstance(found, enrich_module.Route):
            # Where a flight with this callsign is expected to start and end. Not checked
            # against where the aircraft is: it is shown for what it is.
            operator = found.airline
            origin = (found.origin.code, found.origin.city)
            destination = (found.destination.code, found.destination.city)
        type_name = airframe.type_name if isinstance(airframe, enrich_module.Airframe) else ""
        if not operator and isinstance(airframe, enrich_module.Airframe):
            operator = airframe.owner  # a private aircraft has an owner, not an airline
        thumbnails, credit = [], ""
        if isinstance(pictures, tuple):
            for picture in pictures:
                surface = self._photo_surface(aircraft.hex, picture)
                if surface is not None:
                    thumbnails.append(surface)
            credit = photo_credit(pictures)
        departure = "" if origin and destination else self.departure(aircraft.hex)
        return panel.Details(
            operator, origin, destination, type_name, tuple(thumbnails), credit, departure
        )

    def _photo_surface(self, hex_code: str, photo: enrich_module.Photo) -> pygame.Surface | None:
        """The picture decoded and cut to the pane's thumbnail, once. None if it will not
        decode: then there is just no picture."""
        key = (hex_code, id(photo.data))
        if key not in self._photo_surfaces:
            try:
                image = pygame.image.load(io.BytesIO(photo.data)).convert()
                want_w, want_h = panel.PHOTO_SIZE
                scale = max(want_w / image.get_width(), want_h / image.get_height())
                size = (
                    max(round(image.get_width() * scale), want_w),
                    max(round(image.get_height() * scale), want_h),
                )
                image = pygame.transform.smoothscale(image, size)
                crop = pygame.Rect(0, 0, want_w, want_h)
                crop.center = image.get_rect().center
                self._photo_surfaces[key] = image.subsurface(crop).copy()
            except (pygame.error, ValueError, ZeroDivisionError) as exc:
                log.warning("could not show the photo of %s: %s", hex_code, exc)
                self._photo_surfaces[key] = None
            while len(self._photo_surfaces) > 24:
                self._photo_surfaces.popitem(last=False)
        return self._photo_surfaces[key]

    def _tracked(
        self, feed: FeedSnapshot, center: tuple[float, float], now: float, stale: bool
    ) -> list[Drawn]:
        """Every aircraft still fresh, nearest the center of the view first, with what the
        frame needs worked out as seldom as will do.

        How an aircraft looks (its kind, color, label, whether it is a favorite) changes
        only when the feed answers again, so that is kept from one frame to the next. Where
        it is changes all the time, but at regional ranges not by a pixel in half a second,
        so there the positions are reused for that long too.
        """
        look_key = (self.favorite_keys, stale)
        # Compared by identity, and kept hold of, so that a new answer is never mistaken
        # for the old one.
        if feed.aircraft is not self._looks_for or look_key != self._look_key:
            self._looks_for, self._look_key = feed.aircraft, look_key
            self._looks = {a.hex: look(a, self.favorite_keys, stale) for a in feed.aircraft}
            self._moving_key = None
        hold = POSITION_HOLD_S if self.settings.range_nm >= WIDE_RANGE_NM else 0.0
        # The selected aircraft is shown whatever its group: hiding traffic must not make
        # the one being looked at, or followed, vanish.
        hidden, keep = self.hidden_traffic, frozenset(self._kept_wanted())
        moving_key = (center, int(now / hold) if hold else now, hidden, keep)
        if moving_key != self._moving_key:
            self._moving_key = moving_key
            lat0, lon0 = center
            found = []
            for a in feed.aircraft:
                if a.is_stale(now):
                    continue
                seen = self._looks[a.hex]
                if hidden and seen[-1] in hidden and a.hex not in keep:
                    continue
                lat, lon = a.position_at(now)
                found.append(Drawn(a, distance_nm(lat0, lon0, lat, lon), lat, lon, *seen))
            found.sort(key=_by_distance)
            self._moving = found
        return self._moving

    def _kept_wanted(self) -> set[str]:
        """The aircraft to keep hold of when it drops out of the feed: the selected one."""
        return {self.selected_hex} if self.selected_hex is not None else set()

    def _with_lost_aircraft(self, live: list[Drawn], center: tuple[float, float]) -> list[Drawn]:
        """live, plus the selected and pinned aircraft no longer in it, where they were last
        heard of and grayed. Worked out again only when live or the wanted set changes."""
        wanted = self._kept_wanted()
        key = (id(live), frozenset(wanted), self.favorite_keys, self.pinned_keys)
        if key == self._with_lost_key:
            return self._with_lost
        self._with_lost_key = key
        for hex_code in self._kept.keys() - wanted:
            del self._kept[hex_code]
        found = set()
        if wanted:
            for d in live:
                if d.aircraft.hex in wanted:
                    self._kept[d.aircraft.hex] = d.aircraft
                    found.add(d.aircraft.hex)
        # Favorites pinned in settings: their tracks are drawn while they are on the map.
        pinned = self.pinned_keys
        self._pinned_here = frozenset(
            d.aircraft.hex
            for d in live
            if pinned and favorites_module.is_favorite(d.aircraft, pinned)
        )
        lost = []
        for hex_code in sorted(wanted - found):
            a = self._kept.get(hex_code)
            if a is not None:
                where = distance_nm(*center, a.lat, a.lon)
                lost.append(Drawn(a, where, a.lat, a.lon, *look(a, self.favorite_keys, True)))
        self._lost = frozenset(d.aircraft.hex for d in lost)
        self._with_lost = live + lost if lost else live
        return self._with_lost

    def _map_words(self, now: float, chosen: Drawn | None) -> str | None:
        """The short notice at the top-left of the map: something just changed (for a
        moment), or an aircraft is being followed."""
        if self._flash is not None:
            if now < self._flash[1]:
                return self._flash[0]
            self._flash = None
        return f"FOLLOWING {chosen.label}" if self.following and chosen else None

    def _draw_map_note(self, now: float) -> None:
        """Say so, in the corner of the map, while the map itself has holes. Weather layers
        are not counted: a cloud frame that has not been published yet is not a missing map."""
        base = STREET if self.settings.map_style == "street" else IMAGERY
        holes = self.basemap.missing_by_layer.get(base.name, 0) if self.settings.show_map else 0
        if not holes or self.tile_store is None:
            self._map_missing_since = None
            return
        if self._map_missing_since is None:
            self._map_missing_since = now
        waited = now - self._map_missing_since
        if waited < 1.5:
            return  # the usual case: tiles arrive within a moment, and need no comment
        unavailable = waited > MAP_UNAVAILABLE_AFTER_S
        words = "Map unavailable: no connection?" if unavailable else "Loading map..."
        color = theme.STATUS_STALE if unavailable else theme.MAP_TEXT_DIM
        note = theme.shadowed_text(self.fonts.small, words, color)
        view = self.layout.view
        self.screen.blit(note, note.get_rect(bottomright=(view.right - 8, view.bottom - 6)))

    def _prefetch_map(self, now: float, layers: tuple[LayerSpec, ...]) -> None:
        """Once the view has been still for a moment and its own tiles are in, quietly load
        the map around it and for the next range out, so the next drag or step out has its
        tiles in hand. Once per view."""
        if self.tile_store is None or not layers or self._press_pos is not None:
            return
        center, zoom = self.center, self.zoom()
        view = (round(center[0], 3), round(center[1], 3), round(zoom, 2), layers)
        if view == self._prefetched:
            return
        if self._prefetch_due is None or self._prefetch_due[0] != view:
            self._prefetch_due = (view, now)
            return
        if now - self._prefetch_due[1] < PREFETCH_AFTER_S or self.basemap.missing:
            return
        ranges = RANGES_NM
        wider = ranges[min(ranges.index(self.settings.range_nm) + 1, len(ranges) - 1)]
        wider_zoom = None
        if wider != self.settings.range_nm:
            wider_zoom = zoom_for_range(center[0], wider, self.layout.ring_radius)
        self.prefetched_tiles += self.basemap.prefetch(center, zoom, layers, wider_zoom)
        self._prefetched = view

    # ---- the local receiver -----------------------------------------------------------------

    def receiver_status(self) -> dict | None:
        """How the last request to the local receiver went, for the settings pages: None if
        no receiver is set. Times are wall-clock seconds; None where there has been none."""
        if not self.settings.local_receiver_url:
            return None
        status = self.store.feed().sources.get(LOCAL_FEED)
        if status is None or status.checked_at is None:
            return {"ok": None, "checked": None, "last_good": None, "count": 0, "message": ""}
        ago = time.monotonic()
        wall = time.time()

        def clock(at: float | None) -> float | None:
            return None if at is None else wall - (ago - at)

        return {
            "ok": status.error is None,
            "checked": clock(status.checked_at),
            "last_good": clock(status.updated_at),
            "count": status.count,
            "message": plain_error(status.error) if status.error else "",
        }

    # ---- preview --------------------------------------------------------------------------

    def preview(self) -> bytes | None:
        """The screen as it last was, as a JPEG, for the web page. Called from the portal
        thread: it only notes that a picture is wanted and returns the latest the main loop
        has made (None until the first)."""
        self._preview_wanted = time.monotonic()
        return self._preview

    def typing_in_private(self) -> bool:
        """Whether the settings page's keyboard is up. What is typed there (a Wi-Fi
        password, for one) must not be shown to a web page that has no password."""
        page = self.page
        if not isinstance(page, SettingsScreen):
            return False
        child = page.child
        return (
            page.keyboard is not None
            or isinstance(getattr(child, "keyboard", None), Keyboard)
            and not isinstance(child, GoScreen)
        )

    def _update_preview(self, now: float) -> None:
        """After a frame: if a preview has been asked for lately and ours is getting old,
        take a new one. Costs nothing when nobody is looking."""
        if (
            now - self._preview_wanted > PREVIEW_WANTED_FOR_S
            or now - self._preview_at < PREVIEW_EVERY_S
        ):
            return
        self._preview_at = now
        picture = self.screen
        if self.typing_in_private():
            picture = pygame.Surface(self.screen.get_size())
            picture.fill(theme.BACKGROUND)
            words = theme.text(
                self.fonts.title, "Hidden while someone is typing on the display", theme.TEXT_DIM
            )
            picture.blit(words, words.get_rect(center=picture.get_rect().center))
        try:
            buffer = io.BytesIO()
            pygame.image.save(picture, buffer, "preview.jpg")
            self._preview = buffer.getvalue()
        except pygame.error as exc:
            log.warning("could not make the preview: %s", exc)

    # ---- storage ----------------------------------------------------------------------------

    def storage(self, now: float) -> dict | None:
        """Free and total space where the data is kept, and how much of it our caches use,
        in bytes. Worked out on a worker thread now and then; None until the first answer."""
        if now - self._storage_at > STORAGE_REFRESH_S and not self._storage_busy:
            self._storage_busy = True
            self._storage_at = now
            threading.Thread(target=self._measure_storage, name="storage", daemon=True).start()
        return self._storage

    def _measure_storage(self) -> None:
        try:
            tiles = (self.tile_store.disk_usage() or {}) if self.tile_store else {}
            weather = sum(n for name, n in tiles.items() if name.startswith(WEATHER_PREFIXES))
            looked_up = self.enrich.disk_usage() if self.enrich else {}
            estimate = self.data_dir / location_module.IP_CACHE_FILE
            reach_file = self.data_dir / REACH_FILE
            # Every kind of thing kept to save fetching it again, by name.
            items = [
                ("Map tiles", sum(tiles.values()) - weather),
                ("Weather tiles", weather),
                ("Aircraft photos", looked_up.get("photos", 0)),
                ("Aircraft types, operators and routes", looked_up.get("details", 0)),
                ("Airspace boundaries", self.airspace.disk_bytes() if self.airspace else 0),
                ("Location estimate", estimate.stat().st_size if estimate.exists() else 0),
                # Learned, not downloaded: listed, but Clear caches leaves it.
                ("How low the feeds hear", reach_file.stat().st_size if reach_file.exists() else 0),
            ]
            used = sum(size for _, size in items)
            usage = shutil.disk_usage(self.data_dir)
            self._storage = {
                "free": usage.free,
                "total": usage.total,
                "used": used,
                "budget": cache_module.budget(self.data_dir, used),
                "items": [[name, size] for name, size in items],
            }
        except OSError as exc:
            log.warning("could not measure storage: %s", exc)
        finally:
            self._storage_busy = False

    def clear_caches(self) -> None:
        """Delete everything that was downloaded and can be downloaded again: map and
        weather tiles, airspace, and what was looked up about aircraft. Settings, favorites,
        your own pictures and the map of how low the feeds hear (learned over weeks) are not
        caches and are not touched. May be called from the
        portal thread."""
        for service in (self.tile_store, self.airspace, self.enrich):
            if service is not None:
                service.clear()
        self._prefetched = None
        self._storage_at = float("-inf")  # measure again
        log.info("caches cleared")

    def frame_rate(self, now: float) -> int:
        """How often to redraw: fully while the screen is being touched and for a moment
        after, otherwise slowly."""
        if now >= self._active_until:
            return IDLE_FRAME_RATE
        return FRAME_RATE

    def run(self, max_frames: int | None = None) -> None:
        self._started = True
        self._restore_tracks()
        for poller in self.pollers:
            poller.start()
        for service in (self.tile_store, self.weather, self.airspace, self.metar, self.enrich):
            if service is not None:
                service.start()
        if self.wifi.available:
            threading.Thread(target=self._watch_network, name="network", daemon=True).start()
        # The airport and runway tables take about a second to build on the Pi. Built here,
        # the first search, airport tap or close-in view does not freeze the screen for it.
        threading.Thread(target=_warm_tables, name="tables", daemon=True).start()
        frames = 0
        try:
            running = True
            while running and (max_frames is None or frames < max_frames):
                started = time.monotonic()
                events, self._waiting = self._waiting + pygame.event.get(), []
                for event in events:
                    if event.type in _TOUCH_EVENTS:
                        self._active_until = time.monotonic() + ACTIVE_FOR_S
                    if not self.handle_event(event):
                        running = False
                if self._reconfigure.is_set():
                    self._reconfigure.clear()
                    self.configure_feeds()
                self.catch_up()
                self.flush_settings(self._save_delay_s)
                self.draw(time.monotonic())
                pygame.display.flip()
                self._update_preview(time.monotonic())
                frames += 1

                rate = self.frame_rate(time.monotonic())
                if rate != IDLE_FRAME_RATE:
                    self.clock.tick(rate)
                    continue
                # Idle: sleep until the next slow frame is due, but wake the instant the
                # screen is touched, so the first touch is never kept waiting.
                self.clock.tick()
                remaining = 1.0 / rate - (time.monotonic() - started)
                if remaining > 0.002:
                    event = pygame.event.wait(round(remaining * 1000))
                    if event.type != pygame.NOEVENT:
                        self._waiting.append(event)
        finally:
            if self.flush_settings():
                log.info("settings written on the way out")
            self._save_tracks()
            self._save_reach()
            for poller in self.pollers:
                poller.stop()
            self._network_stop.set()
            services = (
                self.portal,
                self.weather,
                self.airspace,
                self.tile_store,
                self.metar,
                self.enrich,
            )
            for service in services:
                if service is not None:
                    service.stop()
            pygame.quit()


PORTAL_SECRET_FILE = "portal_secret"


def portal_secret(data_dir: Path) -> bytes:
    """The key that signs the web page's sign-in cookie, made once and kept (readable by the
    owner only), so that people stay signed in across restarts."""
    path = data_dir / PORTAL_SECRET_FILE
    try:
        secret = path.read_bytes()
        if len(secret) >= 32:
            return secret
    except OSError:
        pass
    secret = os.urandom(32)
    try:
        path.touch(mode=0o600, exist_ok=True)
        path.chmod(0o600)
        path.write_bytes(secret)
    except OSError as exc:
        log.warning("could not keep the web page's key: %s", exc)
    return secret


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="flighttrack", description=__doc__)
    parser.add_argument("--fullscreen", action="store_true", help="fill the display (kiosk use)")
    parser.add_argument(
        "--replay", action="store_true", help="show the built-in demo traffic, not live data"
    )
    parser.add_argument(
        "--replay-file", type=Path, help="replay traffic from this file (implies --replay)"
    )
    parser.add_argument(
        "--portal-port",
        type=int,
        default=int(os.environ.get("FLIGHTTRACK_PORTAL_PORT", DEFAULT_PORT)),
        help="port for the settings portal (default 80, falling back to 8080)",
    )
    parser.add_argument("--no-portal", action="store_true", help="do not start the portal")
    parser.add_argument(
        "--no-map", action="store_true", help="no map, weather or airspace; fetch none of them"
    )
    parser.add_argument("--max-frames", type=int, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    data_dir = settings_module.data_dir()
    tile_store = airspace = None
    if not args.no_map:
        tile_store = TileStore(data_dir / "tiles")
        airspace = AirspaceStore(data_dir / "airspace")
    try:
        app = App(
            fullscreen=args.fullscreen,
            data_dir=data_dir,
            replay=args.replay,
            replay_file=args.replay_file,
            locate=lambda: location_module.ip_location(data_dir),
            portal_port=None if args.no_portal else args.portal_port,
            tile_store=tile_store,
            airspace=airspace,
            wifi=Wifi(),
            metar=MetarStore(),
            enrich=Enricher(data_dir),
            power=Power(),
            find_flight=find_flight,
            find_history=history_module.last_seen,
            find_trace=history_module.flight_trace,
            find_receiver_track=history_module.receiver_track,
            find_ground=reach_module.open_meteo_ground,
            keyed_services=True,
            save_delay_s=SETTINGS_SAVE_DELAY_S,
        )
    except pygame.error as exc:
        # Exit non-zero so the service manager retries once a display is available.
        log.error("could not open the display: %s", exc)
        return 1
    # Being stopped (a restart, an update, the machine shutting down) must end the loop the
    # ordinary way, so that settings still waiting to be written are.
    signal.signal(signal.SIGTERM, lambda *_: pygame.event.post(pygame.event.Event(pygame.QUIT)))
    app.run(args.max_frames)
    return 0
