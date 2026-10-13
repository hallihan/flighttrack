"""Where a flight not heard now was last seen: adsb.lol's traces and a receiver's history.

The data is made up in the shape the services return; no live service is called."""

import gzip
import json

import pytest
import requests

from flighttrack.feeds import history
from flighttrack.ui import format as fmt

WALL = 1_800_000_000.0  # a fixed "now", as time.time() would give it
MONO = 5_000.0  # and as time.monotonic() would


class Answer:
    def __init__(self, status: int, body: object = None, raw: bytes | None = None) -> None:
        self.status_code = status
        self._body = body
        self.content = raw if raw is not None else json.dumps(body).encode()

    def json(self):
        return self._body

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))


class Fake:
    """A session that answers from a table of URLs, and 404 for anything else."""

    def __init__(self, table: dict[str, Answer]) -> None:
        self.table, self.asked, self.headers = table, [], {}

    def get(self, url: str, timeout=None) -> Answer:
        self.asked.append(url)
        return self.table.get(url, Answer(404))


def trace(hex_code: str, base: float, points: list[list]) -> dict:
    return {"icao": hex_code, "r": "N456QX", "t": "C172", "timestamp": base, "trace": points}


TRACE = trace(
    "abc213",
    WALL - 7200,
    [
        [0.0, 34.90, -122.50, 2000, 95.0, 300.0, 0, 0, {"flight": "N456QX   "}],
        [600.0, 37.02, -122.61, 725, 89.0, 225.0, 0, -1152, None],
        [660.0, 37.03, -122.62, "ground", 12.0, 225.0, 0, None, None],
    ],
)


def test_the_last_point_of_a_trace_is_where_it_was_last_seen():
    found = history.parse_trace(TRACE, WALL, MONO)
    assert (found.hex, found.lat, found.lon) == ("abc213", 37.03, -122.62)
    assert found.on_ground and found.altitude_ft is None
    assert found.callsign == "N456QX" and found.registration == "N456QX"
    assert MONO - found.seen_at == pytest.approx(7200 - 660)
    assert history.parse_trace({"icao": "nothex"}, WALL, MONO) is None


def test_a_registration_is_looked_up_for_its_hex_and_adsb_lol_asked_newest_first():
    airframe = history.AIRFRAME_URL.format("N456QX")
    session = Fake(
        {airframe: Answer(200, {"response": {"aircraft": {"mode_s": "ABC213"}}})}
        | {
            "https://adsb.lol/globe_history/2027/01/14/traces/13/trace_full_abc213.json": Answer(
                200, TRACE
            )
        }
    )
    assert history.hex_for("n456qx", session) == "abc213"
    assert history.hex_for("ABC213", session) == "abc213", "a hex is its own"
    assert history.hex_for("BAW117", session) is None, "not a registration adsbdb knows"
    paused = []
    found = history.adsb_lol_last_seen(
        "abc213", session, wall=lambda: WALL, clock=lambda: MONO, pause=paused.append
    )
    assert found is not None and found.lat == 37.03
    traces = [u for u in session.asked if "adsb.lol" in u]
    assert traces[0] == "https://adsb.lol/data/traces/13/trace_full_abc213.json", "today first"
    assert len(traces) == 2 and paused == [history.SPACING_S], "a second apart, and no further"


def _chunk(now: float, rows: list[list]) -> bytes:
    return gzip.compress(json.dumps({"files": [{"now": now, "aircraft": rows}]}).encode())


def test_a_receivers_history_is_searched_newest_first_by_hex_or_callsign():
    url = "http://rx.local/tar1090/data/aircraft.json"
    base = "http://rx.local/tar1090/chunks/"
    old = _chunk(
        WALL - 3000, [["abc213", 900, 80.0, 220.0, 37.0, -122.6, 1.0, "adsb_icao", "N456QX"]]
    )
    new = _chunk(
        WALL - 600, [["abc123", 3000, 120.0, 90.0, 37.2, -122.9, 2.0, "adsb_icao", "FFT1 "]]
    )
    session = Fake(
        {
            base + "chunks.json": Answer(200, {"chunks_all": ["chunk_1.gz", "chunk_2.gz"]}),
            base + "chunk_1.gz": Answer(200, raw=old),
            base + "chunk_2.gz": Answer(200, raw=new),
        }
    )
    clocks = {"wall": lambda: WALL, "clock": lambda: MONO}
    found = history.receiver_last_seen(url, "abc213", None, session, **clocks)
    assert found.hex == "abc213" and found.altitude_ft == 900
    assert MONO - found.seen_at == pytest.approx(3001)
    assert session.asked[1] == base + "chunk_2.gz", "the newest chunk first"
    by_callsign = history.receiver_last_seen(url, None, "fft1", session, **clocks)
    assert by_callsign.hex == "abc123"
    assert history.receiver_last_seen("http://rx.local/other.json", "abc213", None, session) is None


def test_every_sighting_is_offered_and_trouble_raised_only_when_nothing_was_found():
    class Down(Fake):
        def get(self, url, timeout=None):
            raise requests.ConnectionError("down")

    with pytest.raises(requests.ConnectionError):
        history.last_seen("N456QX", None, None, Down({}), pause=lambda s: None)
    assert history.last_seen("BAW117", None, None, Fake({}), pause=lambda s: None) == []


def test_ages_read_in_minutes_hours_and_days():
    assert [fmt.age(s) for s in (45, 600, 3 * 3600, 3 * 86400)] == ["45s", "10m", "3h", "3d"]


def test_a_trace_is_the_full_one_and_the_recent_one_together():
    def point(t, alt):
        return [t, 37.0 + t / 10000, -122.6, alt, 100.0, 90.0, 0, 0, None]

    full = [point(0, "ground"), point(60, 500), point(120, 1500)]
    recent = [point(120, 1500), point(180, 2500)]  # the full one is written less often
    session = Fake(
        {
            "https://adsb.lol/data/traces/13/trace_full_abc213.json": Answer(
                200, trace("abc213", WALL - 9000, full)
            ),
            "https://adsb.lol/data/traces/13/trace_recent_abc213.json": Answer(
                200, trace("abc213", WALL - 9000, recent)
            ),
        }
    )
    found = history.flight_trace("abc213", session, wall=lambda: WALL, clock=lambda: MONO)
    assert [p[2] for p in found] == [None, 500, 1500, 2500], "merged, the repeat once"
    assert found[0][3] == pytest.approx(MONO - 9000)
    nothing = history.flight_trace("abcdef", Fake({}), wall=lambda: WALL, clock=lambda: MONO)
    assert nothing == [], "no trace"


def test_a_receivers_history_gives_the_whole_track_it_heard():
    def snapshot(now, rows):
        return {"now": now, "aircraft": rows}

    chunk = {
        "files": [
            snapshot(WALL - 600, [["abc213", "ground", 0, 0, 37.0, -122.6, 1.0]]),
            snapshot(WALL - 300, [["abc213", 1500, 90, 0, 37.1, -122.6, 0.0], ["zzz999", 900]]),
        ]
    }
    base = "http://receiver.local/tar1090/"
    session = Fake(
        {
            base + "chunks/chunks.json": Answer(200, {"chunks_all": ["chunk_1.gz"]}),
            base + "chunks/chunk_1.gz": Answer(200, raw=gzip.compress(json.dumps(chunk).encode())),
        }
    )
    found = history.receiver_track(
        base + "data/aircraft.json", "abc213", session, wall=lambda: WALL, clock=lambda: MONO
    )
    assert found == [(37.0, -122.6, None, MONO - 601), (37.1, -122.6, 1500, MONO - 300)]


def test_the_track_from_before_goes_in_front_of_what_the_display_saw():
    import time as time_module

    from flighttrack.feeds.base import Aircraft
    from flighttrack.state import MAX_PAST_POINTS, Store

    store = Store()
    now = time_module.monotonic()
    store.set_aircraft("area", [Aircraft("abc213", 37.10, -122.6, now)], now)
    earlier = [(37.0 + i / 1000, -122.6, 1000, now - 3000 + i) for i in range(2000)]
    later = [(36.0, -122.0, 1000, now + 5)]  # after the display started: already its own
    store.set_past("abc213", earlier + later)
    track = store.track("abc213")
    assert len(track) == MAX_PAST_POINTS + 1, "thinned, then the display's own point"
    assert track[0] == (37.0, -122.6, 1000), "the takeoff kept"
    assert track[-1][:2] == (37.10, -122.6)
    store.set_past("nobody", earlier)
    assert store.track("nobody") == ()


def test_a_track_that_stops_long_before_the_display_saw_the_aircraft_is_left_out():
    # adsb.lol's trace can be hours behind: its last flight is then an earlier one, which
    # ended somewhere else. Put in front, it drew a line from there and named the wrong
    # airport as the departure.
    import time as time_module

    from flighttrack.feeds.base import Aircraft
    from flighttrack.state import Store

    store = Store()
    now = time_module.monotonic()
    store.set_aircraft("area", [Aircraft("a44eeb", 34.70, -80.40, now)], now)
    morning = [(34.65 - i / 1000, -80.69, 2800, now - 7000 + i * 5) for i in range(30)]
    store.set_past("a44eeb", morning)
    assert store.track("a44eeb") == ((34.70, -80.40, None),), "only what the display saw"
    recent = [(34.65 + i / 1000, -80.45, 2800, now - 300 + i * 5) for i in range(10)]
    store.set_past("a44eeb", recent)
    assert len(store.track("a44eeb")) == 11, "a trace that runs up to it is still used"


def test_a_long_recording_keeps_its_start_and_a_restart_keeps_the_track():
    import time as time_module

    from flighttrack.feeds.base import Aircraft
    from flighttrack.state import MAX_PAST_POINTS, MAX_TRACK_POINTS, Store

    store = Store()
    now = time_module.monotonic()
    first = Aircraft("abc213", 37.0, -122.6, now - 2000, altitude_ft=None, on_ground=True)
    store.set_aircraft("area", [first], now - 2000)
    for i in range(1, 2000):
        plane = Aircraft("abc213", 37.0 + i / 1000, -122.6, now - 2000 + i, altitude_ft=3000)
        store.set_aircraft("area", [plane], now - 2000 + i)
    track = store.track("abc213")
    assert track[0] == (37.0, -122.6, None), "the takeoff, though the recording is full"
    assert len(track) <= MAX_PAST_POINTS * 3 // 2 + MAX_TRACK_POINTS
    history = store.history("abc213")
    assert [p[3] for p in history] == sorted(p[3] for p in history)

    # Over a restart: what was kept joins the same aircraft's new track when it is heard.
    kept = store.export(now - 600)
    assert list(kept) == ["abc213"]
    again = Store()
    again.restore(kept)
    plane = Aircraft("abc213", 39.0, -122.6, now + 30, altitude_ft=3000)
    again.set_aircraft("area", [plane], now + 30)
    assert again.track("abc213")[0] == (37.0, -122.6, None)
    assert again.track("abc213")[-1] == (39.0, -122.6, 3000)


def test_the_app_keeps_tracks_over_a_restart(tmp_path):
    import time as time_module

    import pygame

    from flighttrack import settings as settings_module
    from flighttrack.app import TRACKS_FILE, App
    from flighttrack.feeds.base import Aircraft

    settings_module.save(
        settings_module.Settings(home_lat=37.0, home_lon=-122.6), tmp_path / "settings.json"
    )
    try:
        app = App(fullscreen=False, data_dir=tmp_path, replay=False)
        now = time_module.monotonic()
        for i in range(5):
            plane = Aircraft("abc213", 37.0 + i / 100, -122.6, now - 50 + i * 10, altitude_ft=2000)
            app.store.set_aircraft("public", [plane], now - 50 + i * 10)
        app._save_tracks()
        assert (tmp_path / TRACKS_FILE).exists()
        later = App(fullscreen=False, data_dir=tmp_path, replay=False)
        later._restore_tracks()
        assert not (tmp_path / TRACKS_FILE).exists(), "read once"
        plane = Aircraft("abc213", 37.05, -122.6, time_module.monotonic(), altitude_ft=2000)
        later.store.set_aircraft("public", [plane], time_module.monotonic())
        assert len(later.store.track("abc213")) == 6
    finally:
        pygame.quit()
