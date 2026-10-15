"""Where the data on the display comes from: one list, shown briefly in the footer of the web
page and in full on the About tab of the settings page. Terms are in NOTICE."""

NOT_FOR_NAVIGATION = "For hobby and display use only. Not for navigation."

# Short name, what it supplies, and a line more about it.
SOURCES: tuple[tuple[str, str, str], ...] = (
    (
        "adsb.fi",
        "Aircraft positions",
        "Open data API from volunteer receivers. Personal, non-commercial use.",
    ),
    (
        "adsb.lol",
        "Aircraft positions when adsb.fi cannot be reached, and where a flight was last seen",
        "Community feed under the Open Database License.",
    ),
    (
        "OpenSky",
        "With your account: a flight's track so far, and where it last landed",
        "The OpenSky Network, opensky-network.org. Used only with your own client ID.",
    ),
    (
        "FlightAware",
        "With your key: the flight an aircraft is on, from where to where",
        "FlightAware AeroAPI, Personal tier: personal use. Used only with your own key.",
    ),
    (
        "ADS-B Exchange",
        "With your key: aircraft positions when the other feeds fail",
        "Community API through RapidAPI, non-commercial use. Used only with your own key.",
    ),
    (
        "Esri",
        "Satellite map, street map, place names and boundaries",
        "World Imagery: Esri, Maxar, Earthstar Geographics and the GIS User Community.",
    ),
    (
        "IEM",
        "Rain radar over the contiguous United States",
        "NEXRAD composite, Iowa Environmental Mesonet, Iowa State University.",
    ),
    ("RainViewer", "Rain radar everywhere else", "Worldwide composite of weather radars."),
    (
        "NASA GIBS",
        "Cloud cover",
        "GeoColor imagery from GOES-East, GOES-West and Himawari. None over Europe and Africa.",
    ),
    (
        "FAA",
        "Airspace boundaries and VFR charts",
        "Class B, C and D, and sectional charts, Federal Aviation Administration. US only.",
    ),
    (
        "OurAirports",
        "Airports and runways",
        "Public-domain tables, stored on the display.",
    ),
    (
        "Aviation Weather Center",
        "Airport weather reports (METAR)",
        "US National Weather Service; reports from stations worldwide.",
    ),
    (
        "adsbdb.com",
        "Routes, operators and aircraft types",
        "Looked up for the aircraft you select. A route is the one its callsign usually flies.",
    ),
    (
        "airport-data.com",
        "Aircraft photos",
        "Shown with the photographer's name where it is given.",
    ),
    (
        "Open-Meteo",
        "Height of the ground, for the coverage map in diagnostics",
        "Elevation API, from the Copernicus DEM GLO-90 (ESA). CC BY 4.0.",
    ),
    (
        "ipinfo.io",
        "Rough location",
        "Asked once at start-up, for the Current Location favorite.",
    ),
)

# Used only with the user's own key or account: named on the About tab, not in the brief line.
KEYED = frozenset({"OpenSky", "FlightAware", "ADS-B Exchange"})
BRIEF = "Data: " + ", ".join(name for name, _, _ in SOURCES if name not in KEYED) + "."
