import json
from pathlib import Path

import pytest
import requests

from flighttrack.feeds.http import (
    PUBLIC_PROVIDERS,
    USER_AGENT,
    FallbackFeed,
    ReadsbHttpFeed,
    local_feed,
    public_feed,
)
from flighttrack.feeds.merge import merge
from flighttrack.feeds.readsb import parse_aircraft, parse_response
from flighttrack.state import Store

FIXTURES = Path(__file__).parent / "fixtures"
PUBLIC = json.loads((FIXTURES / "adsbfi_point.json").read_text())
LOCAL = json.loads((FIXTURES / "local_receiver.json").read_text())


class FakeResponse:
    def __init__(self, payload=None, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} error")

    def json(self):
        return self._payload


class FakeSession:
    """Stands in for requests.Session so no test touches the network."""

    def __init__(self, response):
        self.response = response
        self.headers = {}
        self.calls = []

    def get(self, url, timeout):
        self.calls.append((url, timeout))
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def test_recorded_public_response_parses_with_registration_and_type():
    aircraft = parse_response(PUBLIC, now=100.0)
    assert len(aircraft) == len(PUBLIC["ac"]) == 12
    assert all(a.registration and a.type_code for a in aircraft)
    assert all(-90 <= a.lat <= 90 and a.seen_at <= 100.0 for a in aircraft)


def test_recorded_local_response_parses_and_skips_aircraft_without_position():
    aircraft = parse_response(LOCAL, now=100.0)
    with_position = [r for r in LOCAL["aircraft"] if "lat" in r]
    assert 0 < len(aircraft) == len(with_position) < len(LOCAL["aircraft"])
    assert all(a.registration is None and a.type_code is None for a in aircraft)


def test_public_url_is_built_from_the_query_and_identifies_us():
    session = FakeSession(FakeResponse(PUBLIC))
    name, template = PUBLIC_PROVIDERS[0]
    feed = ReadsbHttpFeed(name, template, session=session, clock=lambda: 50.0)
    aircraft = feed.fetch(51.47001, -0.45432, 16.2)
    assert session.calls[0][0] == "https://opendata.adsb.fi/api/v3/lat/51.4700/lon/-0.4543/dist/17"
    assert session.headers["User-Agent"] == USER_AGENT
    assert len(aircraft) == 12


def test_local_url_is_used_as_given():
    feed = local_feed("http://receiver.example/tar1090/data/aircraft.json")
    assert feed.url(1, 2, 3) == "http://receiver.example/tar1090/data/aircraft.json"
    assert feed.name == "local" and feed.attribution is None


def test_http_error_status_raises():
    feed = ReadsbHttpFeed("x", "http://x.example/", session=FakeSession(FakeResponse(status=429)))
    with pytest.raises(requests.HTTPError):
        feed.fetch(0, 0, 10)


def _feed(name, response):
    return ReadsbHttpFeed(
        name, "http://x.example/", attribution=name, session=FakeSession(response)
    )


def test_fallback_uses_the_second_provider_when_the_first_fails():
    feed = FallbackFeed(
        "public",
        [_feed("first", requests.ConnectionError("down")), _feed("second", FakeResponse(PUBLIC))],
    )
    assert feed.attribution == "first"
    assert len(feed.fetch(0, 0, 10)) == 12
    assert feed.attribution == "second"


def test_fallback_raises_when_every_provider_fails():
    feed = FallbackFeed(
        "public",
        [_feed("a", requests.ConnectionError("down")), _feed("b", FakeResponse(status=503))],
    )
    with pytest.raises(requests.HTTPError):
        feed.fetch(0, 0, 10)


def test_default_public_feed_starts_with_adsb_fi():
    assert public_feed().attribution == "adsb.fi"


BASE = {"hex": "abc123", "lat": 10.0, "lon": 20.0}


def test_merge_takes_the_freshest_position_and_fills_gaps_from_the_other_source():
    public = parse_aircraft(
        {**BASE, "flight": "TST1", "r": "N123AB", "t": "B738", "alt_baro": 9000, "seen_pos": 4.0},
        now=100.0,
    )
    local = parse_aircraft({**BASE, "lat": 10.1, "alt_baro": 9400, "seen_pos": 0.2}, now=100.0)
    for order in ([public], [local]), ([local], [public]):
        (merged,) = merge(order)
        assert (merged.lat, merged.altitude_ft) == (10.1, 9400), "fresh local position wins"
        assert (merged.callsign, merged.registration, merged.type_code) == (
            "TST1",
            "N123AB",
            "B738",
        )


def test_merge_keeps_aircraft_seen_by_only_one_source():
    a = parse_aircraft(BASE, now=0.0)
    b = parse_aircraft({**BASE, "hex": "def456"}, now=0.0)
    assert {m.hex for m in merge([[a], [b]])} == {"abc123", "def456"}
    assert merge([]) == []


def test_merge_does_not_invent_an_altitude_for_an_aircraft_on_the_ground():
    airborne = parse_aircraft({**BASE, "alt_baro": 800, "seen_pos": 9.0}, now=100.0)
    landed = parse_aircraft({**BASE, "alt_baro": "ground", "seen_pos": 0.0}, now=100.0)
    (merged,) = merge([[airborne], [landed]])
    assert merged.on_ground and merged.altitude_ft == 0


def test_merge_carries_an_emergency_only_when_the_fresher_record_cannot_say():
    squawking = parse_aircraft({**BASE, "squawk": "7700", "seen_pos": 5.0}, now=100.0)
    silent = parse_aircraft({**BASE, "seen_pos": 0.0}, now=100.0)
    cleared = parse_aircraft({**BASE, "squawk": "1200", "seen_pos": 0.0}, now=100.0)
    assert merge([[squawking], [silent]])[0].emergency
    assert not merge([[squawking], [cleared]])[0].emergency


def test_store_merges_the_recorded_public_and_local_feeds():
    store = Store()
    public = parse_response(PUBLIC, now=100.0)
    local = parse_response(LOCAL, now=100.0)
    store.set_aircraft("public", public, now=100.0)
    store.set_aircraft("local", local, now=100.0)
    merged = {a.hex: a for a in store.feed().aircraft}
    shared = {a.hex for a in public} & {a.hex for a in local}
    assert len(shared) == 4
    assert len(merged) == len(public) + len(local) - len(shared)
    assert all(merged[h].registration and merged[h].type_code for h in shared)
