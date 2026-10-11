"""The user's favorite aircraft: a list of registrations, callsigns or hex codes."""

import re

from flighttrack.feeds.base import Aircraft

MAX_FAVORITES = 200
MAX_LENGTH = 12
_VALID = re.compile(r"[A-Z0-9]+")


def normalize(entry: str) -> str:
    """Canonical form for comparing: upper case, no spaces or dashes ("c-gabc" -> "CGABC")."""
    return entry.upper().replace("-", "").replace(" ", "").strip()


def parse(text: str) -> tuple[list[str], list[str]]:
    """Split what the user typed (one per line, or separated by commas) into entries.

    Returns (entries, rejected). Entries keep the user's own spelling, upper-cased, without
    duplicates; rejected lists anything that can't be a registration, callsign or hex code.
    """
    entries: list[str] = []
    rejected: list[str] = []
    seen: set[str] = set()
    for piece in re.split(r"[,\n\r;]+", text):
        entry = piece.strip().upper()
        if not entry:
            continue
        key = normalize(entry)
        if not _VALID.fullmatch(key) or len(entry) > MAX_LENGTH:
            rejected.append(entry[:20])
        elif key not in seen:
            seen.add(key)
            entries.append(entry)
    return entries, rejected


def keys(entries: list[str]) -> frozenset[str]:
    """The set to pass to is_favorite, built once from the stored entries."""
    return frozenset(normalize(entry) for entry in entries)


def is_favorite(aircraft: Aircraft, favorite_keys: frozenset[str]) -> bool:
    if not favorite_keys:
        return False
    return any(
        value is not None and normalize(value) in favorite_keys
        for value in (aircraft.registration, aircraft.callsign, aircraft.hex)
    )
