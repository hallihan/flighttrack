"""The picture behind the aircraft: map tiles composed into one cached surface.

Composing is the expensive part, so it happens only when the view changes or new tiles have
arrived, never per frame.
"""

import io
import logging
from collections import OrderedDict, deque
from dataclasses import dataclass
from math import ceil, floor

import pygame

from flighttrack.geo.mercator import TILE_PX, offset_px, tiles_covering
from flighttrack.tiles import TileKey, TileLayer
from flighttrack.tiles.store import TileStore
from flighttrack.ui import theme
from flighttrack.ui.overlays import Overlay

log = logging.getLogger(__name__)

MAX_DECODED_TILES = 200
MAX_KEPT_TILES = 160  # last-good weather tiles held for layers with a family
DECODES_PER_FRAME = 3
# While tiles are streaming in or the map is being dragged, recompose at most this often.
RECOMPOSE_INTERVAL_S = 0.12
# The picture is composed this much bigger than the view on every side. A drag, or an aircraft
# being followed, then just slides it, and it is only rebuilt once the view nears its edge.
MARGIN_PX = 112
# How much of the margin may be used up before rebuilding.
MARGIN_USE = 0.7
# While any tile is missing, look for it again this often: a map that came up blank because
# the network was not ready fills itself in without anyone touching the screen.
RETRY_MISSING_S = 12.0


@dataclass(frozen=True, slots=True)
class LayerSpec:
    layer: TileLayer
    shade: tuple[int, int, int] | None = None  # multiply colors by this/255 per channel
    alpha: int | None = None  # overall opacity, 0-255
    # For cloud imagery: subtract this from every channel, then add what is left to the map.
    # Dark ground in the image then contributes nothing and only bright cloud shows.
    add_above: int | None = None
    # For layers that are replaced by a newer one every few minutes (weather): a name shared
    # by all its versions. Where the newest version has no tile yet, or cannot get one, the
    # last tile shown for that spot is kept, so the layer never blinks out between updates.
    family: str | None = None
    # A layer laid over another that covers only part of the world (the VFR chart). Where it
    # has no tile there is nothing wrong and nothing to keep asking for.
    optional: bool = False


class Basemap:
    def __init__(
        self, store: TileStore | None, size: tuple[int, int], margin: int = MARGIN_PX
    ) -> None:
        """size is the view's. With no store there are no tile layers, only overlays."""
        self._store = store
        self._margin = margin
        self._size = size = (size[0] + 2 * margin, size[1] + 2 * margin)  # of the picture
        self.missing = 0  # tiles the picture wanted and does not have yet
        self.standing_in = 0  # of those, how many another level's tiles are standing in for
        self.missing_by_layer: dict[str, int] = {}  # the same, by layer name
        self._raw: deque[tuple[TileKey, bytes]] = deque()
        self._tiles: OrderedDict[TileKey, pygame.Surface] = OrderedDict()
        self._last_good: OrderedDict[TileKey, pygame.Surface] = OrderedDict()
        self._composed = pygame.Surface(size)
        self._composed.fill(theme.MAP_FILL)
        self._signature: tuple | None = None
        self._overlays: tuple[Overlay, ...] = ()
        self._center: tuple[float, float] | None = None  # where the composed image is centered
        self._dirty = False
        self._composed_at = float("-inf")

    def pump(self) -> None:
        """Take in tiles that have arrived, decoding a few per frame to keep frames short."""
        if self._store is None:
            return
        self._raw.extend(self._store.drain())
        for _ in range(min(DECODES_PER_FRAME, len(self._raw))):
            key, data = self._raw.popleft()
            try:
                image = pygame.image.load(io.BytesIO(data))
            except pygame.error as exc:
                log.warning("could not decode tile %s: %s", key, exc)
                continue
            # Match the display format once here, so every later blit is fast.
            opaque = data.startswith(b"\xff\xd8")
            self._tiles[key] = image.convert() if opaque else image.convert_alpha()
            self._tiles.move_to_end(key)
            while len(self._tiles) > MAX_DECODED_TILES:
                self._tiles.popitem(last=False)
            self._dirty = True

    def refresh(self) -> None:
        """Compose the picture afresh at the next render (the theme's map color changed)."""
        self._signature = None

    def render(
        self,
        center: tuple[float, float],
        zoom: float,
        layers: tuple[LayerSpec, ...],
        now: float,
        overlays: tuple[Overlay, ...] = (),
    ) -> tuple[pygame.Surface, tuple[int, int]]:
        """The background for a view centered on center at this zoom, and where to blit it
        relative to the view's top-left corner. Cheap unless something changed.

        The picture is bigger than the view by a margin all round. While the map is dragged,
        or follows an aircraft, the same picture is reused, shifted, and only rebuilt when
        the view nears its edge, when tiles arrive, or when an overlay changes, and then no
        more often than the interval above. Sliding a picture is far cheaper than building one.
        """
        signature = (round(zoom, 4), layers)
        if signature != self._signature:
            # A new range or a layer switched: nothing of the old picture is right.
            self._signature = signature
            self._compose(center, zoom, layers, overlays, now)
        else:
            dx, dy = offset_px(center, *self._center, zoom)
            room = self._margin * MARGIN_USE
            wanted = (
                abs(dx) > room  # the view has slid most of the way to the picture's edge
                or abs(dy) > room
                or self._dirty  # new tiles have arrived
                or overlays != self._overlays
            )
            since = now - self._composed_at
            if (wanted and since >= RECOMPOSE_INTERVAL_S) or (
                self.missing and since >= RETRY_MISSING_S
            ):
                self._compose(center, zoom, layers, overlays, now)
        dx, dy = offset_px(center, *self._center, zoom)
        return self._composed, (round(dx) - self._margin, round(dy) - self._margin)

    def _compose(
        self,
        home: tuple[float, float],
        zoom: float,
        layers: tuple[LayerSpec, ...],
        overlays: tuple[Overlay, ...],
        now: float,
    ) -> None:
        self._dirty = False
        self._composed_at = now
        self._center = home
        self._overlays = overlays
        self.missing = 0
        self.missing_by_layer = {}
        self.standing_in = 0
        self._composed.fill(theme.MAP_FILL)
        if self._store is not None:
            for spec in layers:
                self._draw_layer(home, zoom, spec)
        for overlay in overlays:
            overlay.paint(self._composed, home, zoom)

    def _stand_in(self, name: str, z: int, x: int, row: int) -> pygame.Surface | None:
        """The ground of one tile, made from decoded tiles of a neighboring level: a
        quarter (or sixteenth) of a coarser one enlarged, or four finer ones reduced."""
        for up in (1, 2):
            parent = self._tiles.get((name, z - up, x >> up, row >> up))
            if parent is not None:
                part = TILE_PX >> up
                corner = ((x % (1 << up)) * part, (row % (1 << up)) * part)
                return pygame.transform.scale(
                    parent.subsurface(pygame.Rect(corner, (part, part))), (TILE_PX, TILE_PX)
                )
        half = TILE_PX // 2
        made = None
        for dx in (0, 1):
            for dy in (0, 1):
                child = self._tiles.get((name, z + 1, 2 * x + dx, 2 * row + dy))
                if child is not None:
                    if made is None:
                        made = pygame.Surface((TILE_PX, TILE_PX), pygame.SRCALPHA)
                    made.blit(pygame.transform.scale(child, (half, half)), (dx * half, dy * half))
        return made

    def prefetch(
        self,
        center: tuple[float, float],
        zoom: float,
        layers: tuple[LayerSpec, ...],
        wider_zoom: float | None,
        limit: int = 180,
    ) -> int:
        """Ask for the map tiles likely to be wanted next, so that a drag or a step out
        finds them in memory: twice the picture's width and height around the view at this
        level, and the picture at the next range out. Weather is left out: it is replaced
        too often to be worth it. Returns how many tiles were asked for."""
        if self._store is None:
            return 0
        width, height = self._size
        asked = 0
        for spec in layers:
            layer = spec.layer
            if layer.short_lived:
                continue
            wanted = [(layer.zoom_for(zoom), zoom, 2 * width, 2 * height)]
            if wider_zoom is not None:
                wanted.append((layer.zoom_for(wider_zoom), wider_zoom, width, height))
            for z, view_zoom, w, h in wanted:
                _, _, columns, rows = tiles_covering(center, view_zoom, z, w, h)
                if len(columns) * len(rows) > limit:
                    continue  # too far out for this layer's levels: not worth hundreds of tiles
                for column in columns:
                    for row in rows:
                        x = column % 2**z
                        if (layer.name, z, x, row) not in self._tiles and asked < limit:
                            asked += self._store.prefetch(layer, z, x, row)
        return asked

    def _draw_layer(self, home: tuple[float, float], zoom: float, spec: LayerSpec) -> None:
        layer = spec.layer
        width, height = self._size
        z = layer.zoom_for(zoom)
        scale, (cx, cy), columns, rows = tiles_covering(home, zoom, z, width, height)
        if not rows:
            return
        mosaic = pygame.Surface((len(columns) * TILE_PX, len(rows) * TILE_PX), pygame.SRCALPHA)
        found = False
        for i, column in enumerate(columns):
            for j, row in enumerate(rows):
                x = column % 2**z
                tile = self._tiles.get((layer.name, z, x, row))
                kept_as = (spec.family, z, x, row) if spec.family else None
                if tile is None:
                    self._store.request(layer, z, x, row)
                    tile = self._last_good.get(kept_as) if kept_as else None
                    if tile is None:
                        # Not here yet. If the same ground is in hand at another level
                        # (the one just zoomed away from), show that, enlarged or reduced,
                        # until this arrives: no hole, and no other map showing through.
                        stand_in = self._stand_in(layer.name, z, x, row)
                        if stand_in is not None:
                            mosaic.blit(stand_in, (i * TILE_PX, j * TILE_PX))
                            found = True
                            self.standing_in += 1
                        if not spec.optional:
                            self.missing += 1
                            self.missing_by_layer[layer.name] = (
                                self.missing_by_layer.get(layer.name, 0) + 1
                            )
                        continue
                else:
                    self._tiles.move_to_end((layer.name, z, x, row))
                    if kept_as:
                        self._last_good[kept_as] = tile
                        self._last_good.move_to_end(kept_as)
                        while len(self._last_good) > MAX_KEPT_TILES:
                            self._last_good.popitem(last=False)
                mosaic.blit(tile, (i * TILE_PX, j * TILE_PX))
                found = True
        if not found:
            return

        # The part of the mosaic the view covers, scaled to the view. Cropping first matters:
        # weather tiles are magnified many times at close range.
        left = cx - width / 2 / scale - columns[0] * TILE_PX
        top = cy - height / 2 / scale - rows[0] * TILE_PX
        source = pygame.Rect(
            floor(left), floor(top), ceil(width / scale) + 2, ceil(height / scale) + 2
        ).clip(mosaic.get_rect())
        if source.width == 0 or source.height == 0:
            return
        target = (max(round(source.width * scale), 1), max(round(source.height * scale), 1))
        scaled = pygame.transform.smoothscale(mosaic.subsurface(source), target)
        position = (round((source.x - left) * scale), round((source.y - top) * scale))
        if spec.add_above is not None:
            floor_level = (spec.add_above,) * 3
            scaled.fill(floor_level, special_flags=pygame.BLEND_RGB_SUB)
        if spec.shade is not None:
            scaled.fill(spec.shade, special_flags=pygame.BLEND_RGB_MULT)
        if spec.add_above is not None:
            self._composed.blit(scaled, position, special_flags=pygame.BLEND_RGB_ADD)
            return
        if spec.alpha is not None:
            scaled.set_alpha(spec.alpha)
        self._composed.blit(scaled, position)
