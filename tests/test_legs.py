"""Where one flight ends and the one before it begins, in a track with holes in it."""

from flighttrack import legs
from flighttrack.geo import destination


def flying(start, track_deg, kt, t0, seconds, alt=3000, every=10):
    """Points of an aircraft flying straight: from start, every few seconds."""
    points = []
    for s in range(0, seconds + 1, every):
        lat, lon = destination(*start, track_deg, kt * s / 3600)
        points.append((lat, lon, alt, t0 + s))
    return points


def test_a_flight_starts_where_it_left_the_ground_and_ends_on_it():
    ground = [(35.0, -80.6, None, 0.0), (35.0, -80.6, None, 30.0)]
    air = flying((35.0, -80.6), 90, 100, 60, 600)
    landed = [(air[-1][0], air[-1][1], None, 700.0)]
    flight = legs.current_flight(ground + air + landed)
    assert flight[0] == ground[-1], "from its last point on the ground: the takeoff"
    assert flight[-1] == landed[0] and legs.took_off(flight)


def test_a_short_hole_is_always_the_same_flight():
    before = flying((35.0, -80.6), 90, 100, 0, 600)
    after = flying(before[-1][:2], 270, 100, 600 + 9 * 60, 300)  # back where it was: circling
    assert legs.current_flight(before + after)[0] == before[0]


def test_a_hole_the_aircraft_flew_through_is_bridged_and_one_it_landed_in_is_not():
    before = flying((34.2, -79.7), 315, 95, 0, 600)
    # Out of range for 40 minutes, still going: it covers the ground it would have.
    gone = destination(*before[-1][:2], 315, 95 * 40 / 60)
    after = flying(gone, 315, 95, 600 + 40 * 60, 600)
    assert legs.current_flight(before + after)[0] == before[0]
    # 40 minutes, but it is only 5 NM further on: it landed somewhere and took off again.
    near = destination(*before[-1][:2], 315, 5)
    after = flying(near, 315, 95, 600 + 40 * 60, 600)
    assert legs.current_flight(before + after)[0] == after[0]


def test_a_hole_of_hours_or_an_impossible_jump_is_between_two_flights():
    before = flying((34.2, -79.7), 315, 95, 0, 600)
    far = destination(*before[-1][:2], 315, 95 * 3)
    after = flying(far, 315, 95, 600 + 3 * 3600, 600)
    assert legs.current_flight(before + after)[0] == after[0], "three hours: never"
    jump = destination(*before[-1][:2], 90, 400)  # 400 NM in 15 minutes
    after = flying(jump, 315, 95, 600 + 15 * 60, 600)
    assert legs.current_flight(before + after)[0] == after[0]


def test_an_earlier_flight_two_hours_behind_is_not_taken_for_this_one():
    # N377MM: adsb.lol last heard it near T73 at 09:50, after a flight from KEQY; it then
    # flew to KFLO, and was heard again coming back at 11:45, 15 NM from where it was lost.
    morning = flying((34.66, -80.70), 120, 95, 0, 120, alt=2900, every=5)
    now = flying((34.70, -80.40), 319, 93, 115 * 60, 300, alt=2975, every=10)
    assert legs.current_flight(morning + now)[0] == now[0]


def test_with_nothing_to_judge_the_speed_by_a_long_hole_ends_the_flight():
    lone = [(34.6, -80.7, 2900, 0.0), (34.9, -80.4, 2900, 30 * 60.0)]
    assert legs.current_flight(lone) == lone[1:]


def test_sources_merge_into_one_track_without_repeats():
    a = [(1.0, 1.0, 100, 10.0), (2.0, 2.0, 100, 30.0)]
    b = [(1.0, 1.0, 100, 10.4), (1.5, 1.5, 100, 20.0)]
    assert legs.merged(a, b) == [a[0], b[1], a[1]]
