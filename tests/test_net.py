"""The rate limiter every request to a public service goes through."""

import threading

import pytest

from flighttrack import net


class Clock:
    def __init__(self):
        self.now, self.slept = 100.0, []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(round(seconds, 3))
        self.now += seconds


def test_requests_to_one_host_are_spaced_and_other_hosts_are_not_held_up():
    clock = Clock()
    limiter = net.RateLimiter(clock, clock.sleep)
    for _ in range(3):
        limiter.wait("https://opendata.adsb.fi/api/v3/lat/1/lon/2/dist/3")
    assert clock.slept == [1.1, 1.1], "a second and a bit apart, as adsb.fi asks"
    limiter.wait("https://api.adsb.lol/v2/hex/abc123")
    assert clock.slept == [1.1, 1.1], "another host has its own turns"
    limiter.wait("https://server.arcgisonline.com/tile/1/2/3", net.TILE_SPACING_S)
    limiter.wait("https://server.arcgisonline.com/tile/1/2/4", net.TILE_SPACING_S)
    assert clock.slept[-1] == net.TILE_SPACING_S, "tiles closer together"


@pytest.mark.parametrize(
    "url",
    [
        "http://feeder.local/tar1090/data/aircraft.json",
        "http://192.168.1.50/data/aircraft.json",
        "http://localhost:8080/x",
        "http://receiver/data/aircraft.json",
    ],
)
def test_the_local_network_is_not_limited(url):
    clock = Clock()
    limiter = net.RateLimiter(clock, clock.sleep)
    for _ in range(3):
        limiter.wait(url)
    assert clock.slept == []


def test_threads_take_their_turns_one_after_another():
    clock = Clock()
    lock = threading.Lock()

    def sleep(seconds):
        with lock:
            clock.slept.append(round(seconds, 3))

    limiter = net.RateLimiter(clock, sleep)
    threads = [
        threading.Thread(target=limiter.wait, args=("https://api.adsbdb.com/v0/aircraft/x",))
        for _ in range(4)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(clock.slept) == [1.0, 2.0, 3.0], "booked in turn, each a second after the last"
