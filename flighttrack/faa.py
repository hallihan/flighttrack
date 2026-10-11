"""When the FAA's aeronautical data goes out of date.

The FAA publishes on a fixed calendar. Airspace and other data change every 28 days (the
AIRAC cycle); the VFR charts every 56, on every other one of those dates. A new edition takes
effect at 0901 UTC on its day. So what we hold is not "good for N days from when we fetched
it": it is good until the next effective date, however recently it was fetched.

Dates: 20 February 2025 was an effective date for both, and the rest follow from it (the
chart dates for 2025 are 20 Feb, 17 Apr, 12 Jun, 7 Aug, 2 Oct and 27 Nov).
"""

import calendar

CYCLE_EPOCH = float(calendar.timegm((2025, 2, 20, 9, 1, 0)))
AIRAC_S = 28 * 86400.0  # airspace
CHART_S = 56 * 86400.0  # VFR charts
# The services we read do not all switch over on the minute. Anything fetched in the first two
# days of a cycle is fetched once more after them, in case it was still the old edition.
SETTLE_S = 2 * 86400.0


def cycle_start(now: float, period_s: float) -> float:
    """When the edition in force at `now` took effect."""
    return CYCLE_EPOCH + ((now - CYCLE_EPOCH) // period_s) * period_s


def next_cycle(now: float, period_s: float) -> float:
    return cycle_start(now, period_s) + period_s


def out_of_date(fetched_at: float, now: float, period_s: float) -> bool:
    """Whether something fetched at fetched_at should be fetched again: a new edition has
    taken effect since, or it was fetched so soon after one that it may have been the old."""
    start = cycle_start(now, period_s)
    if fetched_at < start:
        return True
    return fetched_at < start + SETTLE_S <= now
