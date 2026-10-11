"""Map tiles: which services to use, and fetching and caching them."""

from dataclasses import dataclass

from flighttrack.faa import CHART_S


@dataclass(frozen=True, slots=True)
class TileLayer:
    name: str  # also the cache directory name
    url_template: str  # with {z}, {x}, {y}
    max_zoom: int
    extension: str
    attribution: str
    min_zoom: int = 0
    # Replaced by a newer layer within minutes (weather). Its tiles are kept in memory and
    # only written to disk if memory runs short: on an SD card they would be worn-in rubbish.
    short_lived: bool = False
    # For what the FAA publishes on a calendar (the charts): the length of its cycle. Its
    # tiles go out of date when a new edition takes effect, not after a number of days.
    cycle_s: float | None = None

    def url(self, z: int, x: int, y: int) -> str:
        return (
            self.url_template.replace("{z}", str(z)).replace("{x}", str(x)).replace("{y}", str(y))
        )

    def zoom_for(self, view_zoom: float) -> int:
        """The tile level to use for a view at this (fractional) zoom: the nearest available."""
        return min(max(round(view_zoom), self.min_zoom), self.max_zoom)


_ESRI = "https://server.arcgisonline.com/ArcGIS/rest/services"

# Satellite imagery. Attribution required: Esri, Maxar, Earthstar Geographics and the GIS user
# community.
IMAGERY = TileLayer(
    "imagery", f"{_ESRI}/World_Imagery/MapServer/tile/{{z}}/{{y}}/{{x}}", 18, "jpg", "Esri"
)
# Transparent overlay of place names and boundaries, made to sit on the imagery.
PLACES = TileLayer(
    "places",
    f"{_ESRI}/Reference/World_Boundaries_and_Places/MapServer/tile/{{z}}/{{y}}/{{x}}",
    15,
    "png",
    "Esri",
)

# A street map, with its own names: an alternative to the imagery.
STREET = TileLayer(
    "street", f"{_ESRI}/World_Street_Map/MapServer/tile/{{z}}/{{y}}/{{x}}", 18, "jpg", "Esri"
)
# FAA VFR sectional charts. United States only (elsewhere the service answers 404), and only
# at these five levels: closer in the tiles are enlarged, further out there would be too many.
CHART = TileLayer(
    "vfr",
    "https://tiles.arcgis.com/tiles/ssFJjBXIUyZDrSYZ/arcgis/rest/services/VFR_Sectional"
    "/MapServer/tile/{z}/{y}/{x}",
    12,
    "jpg",
    "FAA",
    min_zoom=8,
    cycle_s=CHART_S,
)

TileKey = tuple[str, int, int, int]  # layer name, z, x, y
