import json

import pytest

from flighttrack.feeds.base import MAX_EXTRAPOLATION_S, STALE_AFTER_S, Aircraft
from flighttrack.feeds.coverage import MAX_QUERY_NM, QueryPlan, plan_query
from flighttrack.feeds.poller import FeedPoller
from flighttrack.feeds.readsb import parse_aircraft, parse_response
from flighttrack.feeds.replay import ReplayFeed
from flighttrack.geo import distance_nm
from flighttrack.state import Store

FULL = {
    "hex": "C0FFEE",
    "flight": "TST123  ",
    "r": "C-TEST",
    "t": "B738",
    "lat": 43.5,
    "lon": -79.5,
    "alt_baro": 12025,
    "gs": 301.4,
    "track": 87.2,
    "baro_rate": -1216,
    "squawk": "4521",
    "emergency": "none",
    "seen_pos": 2.0,
}


def test_parses_a_full_record():
    a = parse_aircraft(FULL, now=100.0)
    assert a == Aircraft(
        hex="c0ffee",
        lat=43.5,
        lon=-79.5,
        seen_at=98.0,
        callsign="TST123",
        registration="C-TEST",
        type_code="B738",
        altitude_ft=12025,
        on_ground=False,
        ground_speed_kt=301.4,
        track_deg=87.2,
        vertical_rate_fpm=-1216,
        squawk="4521",
        emergency=False,
    )


def test_minimal_record_leaves_optional_fields_empty():
    a = parse_aircraft({"hex": "abc123", "lat": 1.0, "lon": 2.0}, now=10.0)
    assert a is not None
    assert a.callsign is None and a.altitude_ft is None and a.track_deg is None
    assert a.label == "ABC123"
    assert a.seen_at == 10.0


@pytest.mark.parametrize("missing", ["hex", "lat", "lon"])
def test_record_without_identity_or_position_is_skipped(missing):
    raw = {k: v for k, v in FULL.items() if k != missing}
    assert parse_aircraft(raw, now=0.0) is None


def test_ground_altitude():
    a = parse_aircraft({**FULL, "alt_baro": "ground"}, now=0.0)
    assert a.on_ground and a.altitude_ft == 0


def test_falls_back_to_geometric_altitude_and_rate():
    raw = {k: v for k, v in FULL.items() if k not in ("alt_baro", "baro_rate")}
    a = parse_aircraft({**raw, "alt_geom": 5000, "geom_rate": 640}, now=0.0)
    assert a.altitude_ft == 5000 and a.vertical_rate_fpm == 640


@pytest.mark.parametrize(
    ("fields", "expected"),
    [
        ({"squawk": "7700"}, True),
        ({"squawk": "7600"}, True),
        ({"squawk": "7500"}, True),
        ({"emergency": "general"}, True),
        ({"squawk": "7000", "emergency": "none"}, False),
    ],
)
def test_emergency_detection(fields, expected):
    assert parse_aircraft({**FULL, **fields}, now=0.0).emergency is expected


def test_response_accepts_both_list_keys_and_skips_junk():
    junk = [FULL, {"hex": "nopos1"}, "not a record"]
    assert len(parse_response({"ac": junk}, now=0.0)) == 1
    assert len(parse_response({"aircraft": junk}, now=0.0)) == 1
    assert parse_response({}, now=0.0) == []
    assert parse_response({"ac": None}, now=0.0) == []


def test_dead_reckoning_moves_along_track_and_is_capped():
    a = parse_aircraft({**FULL, "gs": 360.0, "track": 90.0, "seen_pos": 0.0}, now=0.0)
    lat, lon = a.position_at(10.0)  # 360 kt for 10 s is one nautical mile
    assert distance_nm(a.lat, a.lon, lat, lon) == pytest.approx(1.0, abs=1e-6)
    assert lon > a.lon

    capped = a.position_at(10_000.0)
    expected = 360.0 * MAX_EXTRAPOLATION_S / 3600.0
    assert distance_nm(a.lat, a.lon, *capped) == pytest.approx(expected, abs=1e-6)


def test_without_speed_or_track_position_is_unchanged():
    a = parse_aircraft({"hex": "abc123", "lat": 1.0, "lon": 2.0}, now=0.0)
    assert a.position_at(20.0) == (1.0, 2.0)


def test_staleness():
    a = parse_aircraft(FULL, now=0.0)
    assert not a.is_stale(STALE_AFTER_S - 5)
    assert a.is_stale(STALE_AFTER_S + 5)


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _replay_file(tmp_path, frames, interval=5):
    path = tmp_path / "replay.json"
    path.write_text(
        json.dumps({"center": {"lat": 1.0, "lon": 2.0}, "interval": interval, "frames": frames})
    )
    return path


def test_replay_steps_through_frames_and_loops(tmp_path):
    frames = [{"ac": [{"hex": f"00000{i}", "lat": 1.0, "lon": 2.0}]} for i in range(3)]
    clock = FakeClock()
    feed = ReplayFeed(_replay_file(tmp_path, frames), clock=clock)
    assert feed.center == (1.0, 2.0)

    seen = []
    for _ in range(4):
        seen.append(feed.fetch(0, 0, 50)[0].hex)
        clock.now += 5
    assert seen == ["000000", "000001", "000002", "000000"]


def test_replay_rejects_an_empty_file(tmp_path):
    with pytest.raises(ValueError):
        ReplayFeed(_replay_file(tmp_path, []))


def test_bundled_sample_covers_every_range():
    feed = ReplayFeed()
    distances = [distance_nm(*feed.center, a.lat, a.lon) for a in feed.fetch(0, 0, 50)]
    assert min(distances) < 1
    assert any(40 < d <= 50 for d in distances)


PLAN = QueryPlan(1.0, 2.0, 10.0, 5.0)


@pytest.mark.parametrize(
    ("range_nm", "radius", "interval"),
    [(1, 1.8, 5.0), (50, 90.0, 5.0), (100, 180.0, 10.0), (250, MAX_QUERY_NM, 15.0)],
)
def test_query_plan_widens_with_range_slows_down_and_respects_the_api_limit(
    range_nm, radius, interval
):
    plan = plan_query((51.5, -0.1), range_nm, corner_factor=1.8)
    assert (plan.lat, plan.lon) == (51.5, -0.1)
    assert plan.radius_nm == pytest.approx(radius)
    assert plan.interval_s == interval


def test_poller_follows_a_changing_plan():
    class Recorder:
        name = "rec"

        def __init__(self):
            self.asked = []

        def fetch(self, lat, lon, radius_nm):
            self.asked.append(radius_nm)
            return []

    feed, plans = Recorder(), [QueryPlan(1, 2, 18, 5.0), QueryPlan(1, 2, 250, 15.0)]
    poller = FeedPoller(feed, Store(), lambda: plans[0])
    poller.poll_once()
    assert poller.next_wait_s() == 5.0
    plans.pop(0)
    poller.poll_once()
    assert feed.asked == [18, 250] and poller.next_wait_s() == 15.0


class BrokenFeed:
    name = "broken"

    def fetch(self, lat, lon, radius_nm):
        raise RuntimeError("network down")


def test_poller_publishes_aircraft_and_reports_failures(tmp_path):
    frames = [{"ac": [{"hex": "000001", "lat": 1.0, "lon": 2.0}]}]
    store = Store()
    good = FeedPoller(ReplayFeed(_replay_file(tmp_path, frames)), store, lambda: PLAN)
    good.poll_once()
    status = store.feed().sources["replay"]
    assert status.count == 1 and status.error is None and status.updated_at is not None
    assert good.next_wait_s() == 5.0

    bad = FeedPoller(BrokenFeed(), store, lambda: PLAN)
    bad.poll_once()
    snapshot = store.feed()
    assert snapshot.sources["broken"].error == "network down"
    assert len(snapshot.aircraft) == 1, "one source failing must not hide the other's aircraft"
    assert not snapshot.all_failing


def test_poller_backs_off_while_failing_and_resets_on_success():
    class Flaky:
        name = "flaky"
        fail = True

        def fetch(self, lat, lon, radius_nm):
            if self.fail:
                raise RuntimeError("503")
            return []

    feed = Flaky()
    poller = FeedPoller(feed, Store(), lambda: PLAN)
    waits = []
    for _ in range(6):
        poller.poll_once()
        waits.append(poller.next_wait_s())
    assert waits == [10.0, 20.0, 40.0, 60.0, 60.0, 60.0]
    feed.fail = False
    poller.poll_once()
    assert poller.next_wait_s() == 5.0


def test_store_keeps_last_good_data_when_a_source_fails_and_tracks_age():
    store = Store()
    assert store.feed().updated_at is None and not store.feed().all_failing
    store.set_aircraft("public", [parse_aircraft(FULL, now=10.0)], now=10.0)
    store.set_error("public", "timeout")
    snapshot = store.feed()
    assert len(snapshot.aircraft) == 1 and snapshot.updated_at == 10.0
    assert snapshot.all_failing
