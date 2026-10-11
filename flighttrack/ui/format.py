"""Turning aircraft data into display text. Plain Python: no pygame here."""

import time

from flighttrack.feeds.base import Aircraft

MISSING = "---"


def range_label(range_nm: float) -> str:
    return f"{range_nm:g} NM"


def altitude(aircraft: Aircraft) -> str:
    if aircraft.on_ground:
        return "GROUND"
    if aircraft.altitude_ft is None:
        return MISSING
    return f"{aircraft.altitude_ft:,} ft"


def altitude_tag(aircraft: Aircraft) -> str:
    """Altitude in hundreds of feet, the way a controller's data block shows it."""
    if aircraft.on_ground:
        return "GND"
    if aircraft.altitude_ft is None:
        return MISSING
    return f"{round(aircraft.altitude_ft / 100):03d}"


def speed(knots: float | None) -> str:
    return MISSING if knots is None else f"{round(knots)} kt"


def heading(degrees: float | None) -> str:
    return MISSING if degrees is None else f"{round(degrees) % 360:03d}°"


def distance(nm: float) -> str:
    return f"{nm:.1f} NM"


def vertical_rate(fpm: int | None, short: bool = False) -> str:
    """short leaves the unit off, for where room is tight and the label says what it is."""
    if fpm is None:
        return MISSING
    rounded = round(fpm / 100) * 100
    if rounded == 0:
        return "level"
    return f"{rounded:+,}" if short else f"{rounded:+,} fpm"


def age(seconds: float) -> str:
    """How long ago, shortly: seconds, then minutes, hours and days."""
    seconds = max(seconds, 0)
    if seconds < 100:
        return f"{seconds:.0f}s"
    if seconds < 100 * 60:
        return f"{seconds / 60:.0f}m"
    if seconds < 48 * 3600:
        return f"{seconds / 3600:.0f}h"
    return f"{seconds / 86400:.0f}d"


def receiver_lines(status: dict | None, now: float) -> list[tuple[str, bool, bool]]:
    """How the local receiver last answered, as lines for a settings page: each with whether
    it is good news and whether it is bad. Nothing if no receiver is set."""
    if status is None:
        return []
    if status["checked"] is None:
        return [("Not asked yet.", False, False)]

    def at(when: float) -> str:
        ago = max(now - when, 0)
        how_long = f"{ago:.0f} s ago" if ago < 100 else f"{ago / 60:.0f} min ago"
        return f"{time.strftime('%H:%M:%S', time.localtime(when))} ({how_long})"

    if status["ok"]:
        count = status["count"]
        return [(f"Last response {at(status['checked'])}: OK, {count} aircraft", True, False)]
    lines = [
        (f"Last response {at(status['checked'])}: failed", False, True),
        (f"    {status['message']}", False, True),
    ]
    if status["last_good"] is None:
        lines.append(("    It has not answered properly yet.", False, False))
    else:
        lines.append((f"    Last good response {at(status['last_good'])}", False, False))
    return lines
