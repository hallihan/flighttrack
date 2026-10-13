"""Where one flight ends and the one before it begins, in a track with holes in it.

Every source of an aircraft's past (adsb.lol's traces, a local receiver's history, OpenSky,
FlightAware, this display's own recording) has holes where nobody heard it. A hole may be
a stretch out of range, or a landing somewhere nobody covers and a later takeoff. The rule:

- a point on the ground is where the flight began (its takeoff, or the taxi before it);
- so is a point low beside an airport, down where the feeds stop hearing aircraft there
  (reach.py), when the aircraft climbs from it: coverage often does not reach the ground,
  so a takeoff is first heard in the climb. Merely near an airport, or near one but well
  above where hearing ends, says nothing about a landing or a takeoff;
- a hole of up to SAME_FLIGHT_S is always the same flight (even a touch-and-go is);
- a hole longer than MAX_HOLE_S never is;
- in between, it is the same flight only if, across the hole, the aircraft covered ground
  at no less than MIN_PACE of the speed it was flying at on either side of it. One that
  landed, waited and took off again covers far less ground in the time.

Plain Python, no pygame: tested directly.
"""

from collections.abc import Callable

from flighttrack.geo import distance_nm

# One point of a track with its time: lat, lon, altitude in feet (None on the ground),
# monotonic seconds.
TimedPoint = tuple[float, float, int | None, float]

SAME_FLIGHT_S = 10 * 60
MAX_HOLE_S = 2 * 3600
MIN_PACE = 0.5
# Across a hole, faster than this many times its speed (and than MAX_PACE_KT) is not the
# aircraft getting there, but a bad position.
MAX_PACE = 3.0
MAX_PACE_KT = 700.0
# Whether a point is low beside an airport, as reach.low_beside says.
Low = Callable[[TimedPoint], bool]

# Its speed either side of a hole is measured over this long, between points this close.
SPEED_SPAN_S = 10 * 60
SPEED_STEP_S = 120


def merged(*sources: list[TimedPoint]) -> list[TimedPoint]:
    """Points from several sources as one track, by time, with repeats left out: two points
    within a second of each other are the same report, from two places."""
    points = sorted((p for source in sources for p in source), key=lambda p: p[3])
    out: list[TimedPoint] = []
    for p in points:
        if out and p[3] - out[-1][3] < 1.0:
            continue
        out.append(p)
    return out


def speed_kt(points: list[TimedPoint], index: int, step: int) -> float | None:
    """How fast the aircraft was going around points[index], looking along step (+1 after
    it, -1 before), over SPEED_SPAN_S of closely spaced airborne points. None if too few."""
    nm = seconds = 0.0
    i = index
    while 0 <= i + step < len(points):
        a, b = points[i], points[i + step]
        gap = abs(b[3] - a[3])
        if gap > SPEED_STEP_S or a[2] is None or b[2] is None:
            break
        nm += distance_nm(a[0], a[1], b[0], b[1])
        seconds += gap
        if seconds >= SPEED_SPAN_S:
            break
        i += step
    return nm / (seconds / 3600) if seconds >= 60 else None


def same_flight(points: list[TimedPoint], index: int) -> bool:
    """Whether the hole between points[index - 1] and points[index] is within one flight."""
    a, b = points[index - 1], points[index]
    hole = b[3] - a[3]
    if hole <= SAME_FLIGHT_S:
        return True
    if hole > MAX_HOLE_S:
        return False
    speeds = [s for s in (speed_kt(points, index - 1, -1), speed_kt(points, index, 1)) if s]
    if not speeds:
        return False  # nothing to judge it by: an older track is not worth a wrong one
    speed = max(speeds)
    pace = distance_nm(a[0], a[1], b[0], b[1]) / (hole / 3600)
    return MIN_PACE * speed <= pace <= max(MAX_PACE * speed, MAX_PACE_KT)


def flight_start(points: list[TimedPoint], end: int | None = None) -> int:
    """Index in points (oldest first) where the flight of points[end] (the newest, unless
    given) began: its last point on the ground before it took off, or the first point after
    a hole between flights, or 0. On the ground at end and just before it (landed, taxiing
    in), it is still the flight just made."""
    i = len(points) - 1 if end is None else end
    while i > 0 and points[i][2] is None:
        if not same_flight(points, i):
            return i
        i -= 1
    for k in range(i, 0, -1):
        if not same_flight(points, k):
            return k
        if points[k - 1][2] is None:
            return k - 1
    return 0


def current_flight(points: list[TimedPoint]) -> list[TimedPoint]:
    """The newest point's flight, from where it began."""
    return points[flight_start(points) :]


def took_off(points: list[TimedPoint], low: Low | None = None) -> bool:
    """Whether a flight (as current_flight gives it) starts where it took off: on the
    ground, or low beside an airport, and then flies (higher)."""
    if not points:
        return False
    first = points[0]
    if first[2] is None:
        return any(p[2] is not None for p in points)
    return (
        low is not None and low(first) and any(p[2] is not None and p[2] > first[2] for p in points)
    )
