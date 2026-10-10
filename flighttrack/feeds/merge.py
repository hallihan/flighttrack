"""Combining aircraft seen by more than one source into one record per airframe."""

from collections.abc import Iterable
from dataclasses import replace

from flighttrack.feeds.base import Aircraft

# Fields a fresher record may lack that an older one from another source can supply.
_FILLABLE = (
    "callsign",
    "registration",
    "type_code",
    "altitude_ft",
    "ground_speed_kt",
    "track_deg",
    "vertical_rate_fpm",
    "squawk",
    "category",
)


def _combine(newer: Aircraft, older: Aircraft) -> Aircraft:
    fills = {
        name: getattr(older, name)
        for name in _FILLABLE
        if getattr(newer, name) is None and getattr(older, name) is not None
    }
    # On the ground "altitude 0" is real data, not a gap; don't let it be overwritten or copied.
    if newer.on_ground:
        fills.pop("altitude_ft", None)
    if newer.squawk is None and older.emergency and not newer.emergency:
        fills["emergency"] = True
    return replace(newer, **fills) if fills else newer


def merge(sources: Iterable[Iterable[Aircraft]]) -> list[Aircraft]:
    """One record per hex: the freshest position wins, gaps are filled from the others.

    This is what lets a local receiver supply fast position updates while a public feed
    supplies the registration and type the receiver doesn't know.
    """
    best: dict[str, Aircraft] = {}
    for source in sources:
        for aircraft in source:
            current = best.get(aircraft.hex)
            if current is None:
                best[aircraft.hex] = aircraft
            elif aircraft.seen_at > current.seen_at:
                best[aircraft.hex] = _combine(aircraft, current)
            else:
                best[aircraft.hex] = _combine(current, aircraft)
    return list(best.values())
