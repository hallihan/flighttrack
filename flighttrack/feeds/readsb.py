"""Parser for readsb-style aircraft JSON, the format the public ADS-B APIs and a local
receiver's aircraft.json have in common."""

from typing import Any

from flighttrack.feeds.base import Aircraft

EMERGENCY_SQUAWKS = frozenset({"7500", "7600", "7700"})


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    return value.strip() or None


def parse_aircraft(raw: dict[str, Any], now: float) -> Aircraft | None:
    """One aircraft record, or None if it has no usable identity or position."""
    hex_code = _text(raw.get("hex"))
    lat, lon = _number(raw.get("lat")), _number(raw.get("lon"))
    if hex_code is None or lat is None or lon is None:
        return None

    alt = raw.get("alt_baro")
    on_ground = alt == "ground"
    altitude = _number(alt)
    if altitude is None and not on_ground:
        altitude = _number(raw.get("alt_geom"))

    rate = _number(raw.get("baro_rate"))
    if rate is None:
        rate = _number(raw.get("geom_rate"))

    squawk = _text(raw.get("squawk"))
    emergency_flag = _text(raw.get("emergency"))
    emergency = squawk in EMERGENCY_SQUAWKS or emergency_flag not in (None, "none")

    age = _number(raw.get("seen_pos")) or 0.0

    return Aircraft(
        hex=hex_code.lower(),
        lat=lat,
        lon=lon,
        seen_at=now - max(age, 0.0),
        callsign=_text(raw.get("flight")),
        registration=_text(raw.get("r")),
        type_code=_text(raw.get("t")),
        altitude_ft=0 if on_ground else (None if altitude is None else round(altitude)),
        on_ground=on_ground,
        ground_speed_kt=_number(raw.get("gs")),
        track_deg=_number(raw.get("track")),
        vertical_rate_fpm=None if rate is None else round(rate),
        squawk=squawk,
        emergency=emergency,
        category=_text(raw.get("category")),
    )


def parse_response(payload: dict[str, Any], now: float) -> list[Aircraft]:
    """All usable aircraft in a response. APIs use the key "ac"; a local receiver, "aircraft"."""
    records = payload.get("ac")
    if records is None:
        records = payload.get("aircraft")
    if not isinstance(records, list):
        return []
    parsed = (parse_aircraft(r, now) for r in records if isinstance(r, dict))
    return [a for a in parsed if a is not None]
