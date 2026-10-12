"""Working out where "home" is.

In order of preference: the home airport chosen in settings, exact coordinates if the settings
file has them, and otherwise an estimate from the device's public IP address.
"""

import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path

import requests

from flighttrack import airports, net
from flighttrack.feeds.http import USER_AGENT
from flighttrack.settings import Settings

log = logging.getLogger(__name__)

# Free, keyless, returns {"loc": "lat,lon", "city": ...}. City-level accuracy at best.
IP_LOOKUP_URL = "https://ipinfo.io/json"
IP_CACHE_FILE = "ip_location.json"


@dataclass(frozen=True, slots=True)
class Location:
    lat: float
    lon: float
    source: str  # "airport", "coordinates" or "ip"
    label: str  # what to show the user, e.g. "EGLL" or "London (estimated)"
    place: str = ""  # for an estimate: the town and state or country, e.g. "Denver, CO"

    @property
    def point(self) -> tuple[float, float]:
        return self.lat, self.lon


def configured_location(settings: Settings) -> Location | None:
    """The location the user has chosen, if any. Never touches the network."""
    if settings.home_airport:
        airport = airports.find(settings.home_airport)
        if airport is not None:
            return Location(airport.lat, airport.lon, "airport", airport.ident)
        log.warning("home airport %r is not in the airport table", settings.home_airport)
    if settings.home is not None:
        lat, lon = settings.home
        return Location(lat, lon, "coordinates", f"{lat:.3f}, {lon:.3f}")
    return None


def _parse_ip_response(payload: dict) -> Location:
    lat_text, lon_text = str(payload["loc"]).split(",")
    lat, lon = float(lat_text), float(lon_text)
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError(f"coordinates out of range: {lat}, {lon}")
    city = payload.get("city")
    label = f"{city} (estimated)" if isinstance(city, str) and city else "estimated from IP"
    return Location(lat, lon, "ip", label, _place(payload))


US_STATES = {
    "Alabama": "AL", "Alaska": "AK", "Arizona": "AZ", "Arkansas": "AR", "California": "CA",
    "Colorado": "CO", "Connecticut": "CT", "Delaware": "DE", "District of Columbia": "DC",
    "Florida": "FL", "Georgia": "GA", "Hawaii": "HI", "Idaho": "ID", "Illinois": "IL",
    "Indiana": "IN", "Iowa": "IA", "Kansas": "KS", "Kentucky": "KY", "Louisiana": "LA",
    "Maine": "ME", "Maryland": "MD", "Massachusetts": "MA", "Michigan": "MI",
    "Minnesota": "MN", "Mississippi": "MS", "Missouri": "MO", "Montana": "MT",
    "Nebraska": "NE", "Nevada": "NV", "New Hampshire": "NH", "New Jersey": "NJ",
    "New Mexico": "NM", "New York": "NY", "North Carolina": "NC", "North Dakota": "ND",
    "Ohio": "OH", "Oklahoma": "OK", "Oregon": "OR", "Pennsylvania": "PA",
    "Rhode Island": "RI", "South Carolina": "SC", "South Dakota": "SD", "Tennessee": "TN",
    "Texas": "TX", "Utah": "UT", "Vermont": "VT", "Virginia": "VA", "Washington": "WA",
    "West Virginia": "WV", "Wisconsin": "WI", "Wyoming": "WY", "Puerto Rico": "PR",
}  # fmt: skip


def _place(payload: dict) -> str:
    """ "City, ST" for a US address (the state's two letters), "City, CC" elsewhere (the
    country's), or as much of that as the answer has."""
    city, region, country = (
        value.strip()[:40] if isinstance(value, str) else ""
        for value in (payload.get("city"), payload.get("region"), payload.get("country"))
    )
    area = US_STATES.get(region, "") if country == "US" else country
    return ", ".join(part for part in (city, area) if part)


def ip_location(data_dir: Path, session: requests.Session | None = None) -> Location | None:
    """Estimate the location from the public IP address. Makes a network request, so call it
    from a worker thread. Falls back to the last successful answer; None if there is none."""
    cache = data_dir / IP_CACHE_FILE
    try:
        response = net.get(
            session or requests,
            IP_LOOKUP_URL,
            headers={"User-Agent": USER_AGENT},
            timeout=(4.0, 8.0),
        )
        response.raise_for_status()
        location = _parse_ip_response(response.json())
    except (requests.RequestException, KeyError, ValueError, TypeError) as exc:
        log.warning("IP location lookup failed: %s", exc)
    else:
        try:
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(asdict(location)))
        except OSError as exc:
            log.warning("could not cache the IP location: %s", exc)
        return location
    try:
        return Location(**json.loads(cache.read_text()))
    except (OSError, ValueError, TypeError):
        return None
