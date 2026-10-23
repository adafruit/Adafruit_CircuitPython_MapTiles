# SPDX-FileCopyrightText: Copyright (c) 2026 Tim Cocks for Adafruit Industries
#
# SPDX-License-Identifier: MIT
"""
`adafruit_maptiles.map_view`
================================================================================

:class:`MapView`, a :class:`displayio.Group` showing a slippy map from the
shared tile cache (:mod:`adafruit_maptiles.cache`), and the pieces for
drawing over it.

The map is one 256-colour bitmap (:attr:`MapView.bitmap`, palette: the
RGB332 colours), over a second one holding just the tiles (the
underlay). Tiles are read from their BMPs a strip of rows at a time into
the underlay. Drawing a frame, the program:

1. copies the underlay back over the map (:meth:`MapView.restore`, of the
   whole map or one rectangle),
2. draws its markers with :meth:`MapView.fill`, :meth:`MapView.line`,
   :meth:`MapView.blit` and :meth:`MapView.cross` (clipped to the map, or
   to the rectangle), at :meth:`MapView.xy` of their positions,
3. draws the scale bar and the tiles' credit (:meth:`MapView.draw_legend`),
4. marks the bitmap changed (:meth:`MapView.dirty`) and refreshes the display.

Colours are RGB332 values (:func:`adafruit_maptiles.rgb332`); on an 8-bit
``picodvi`` framebuffer they are the framebuffer's own.

Missing tiles are grey until they are fetched. Setting the view queues
them (:attr:`MapView.missing`, nearest the centre first);
:meth:`MapView.fetch_step` downloads and converts one (seconds: a
program that must stay responsive calls it when it can wait), then
:meth:`MapView.load` puts it on the map. Programs that do not mind
waiting call :meth:`MapView.update` instead.

Loading: :meth:`MapView.load_step` reads one strip of rows (a few ms),
:meth:`MapView.load` all the strips still to read.

CircuitPython note: assigning to a slice of a memoryview of a big buffer
moves the buffer's whole tail, so pixels are copied with ``bitmaptools``,
never by slices.
"""

import bitmaptools
import displayio
import terminalio

from adafruit_maptiles import (
    TILE,
    mercator,
    metres_per_pixel,
    rgb332_colour,
    unmercator,
    world_x,
    world_y,
)
from adafruit_maptiles.cache import TileCache
from adafruit_maptiles.png import BMP_HEADER

# Colours are RGB332 values.
BLACK = 0x00
WHITE = 0xFF
NO_TILE = 0xB6
"""Where a tile is missing."""
WAITING = 0x49
"""The whole map, while it has no centre."""

_palette = None


def palette():
    """The RGB332 palette (256 colours), shared by every map."""
    global _palette  # noqa: PLW0603
    if _palette is None:
        _palette = displayio.Palette(256)
        for v in range(256):
            _palette[v] = rgb332_colour(v)
    return _palette


_glyphs = {}


def text_bitmap(text, background, width=None, outline=False):
    """text on background in terminalio's font (6x12; 1 pixel margin), as a
    256-colour bitmap to :meth:`MapView.blit` onto the map. With outline:
    a black border, 14 pixels high and 2 pixels either side of the text.
    width: wider than the text (the text goes on the right)."""
    # (a glyph's own blank column is on its right)
    b = displayio.Bitmap(width or 6 * len(text) + (5 if outline else 2), 14 if outline else 13, 256)
    if outline:
        b.fill(BLACK)
        bitmaptools.fill_region(b, 1, 1, b.width - 1, b.height - 1, background)
    else:
        b.fill(background)
    for k, ch in enumerate(text):
        g = _glyphs.get(ch)
        if g is None:
            glyph = terminalio.FONT.get_glyph(ord(ch))
            g = _glyphs[ch] = glyph.tile_index * 6 if glyph else -1
        if g >= 0:
            bitmaptools.blit(
                b,
                terminalio.FONT.bitmap,
                b.width - 6 * len(text) - (2 if outline else 1) + 6 * k,
                1,
                x1=g,
                y1=0,
                x2=g + 6,
                y2=12,
                skip_source_index=0,
            )
    return b


def overlaps(a, b):
    """True if rectangles a and b, ``(x1, y1, x2, y2)``, overlap."""
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


class MapView(displayio.Group):  # noqa: PLR0904
    """A slippy map, width x height pixels, centred on centre at zoom.

    :param int width: the map's width in pixels
    :param int height: the map's height in pixels
    :param tuple centre: ``(lat, lon)``, or None for no map yet (the map is
        dark grey until :attr:`centre` is set)
    :param int zoom: 0 (the world in one tile) .. ``zooms[1]``
    :param TileCache cache: where the tiles come from (default: a
        :class:`~adafruit_maptiles.cache.TileCache` of OpenStreetMap
        tiles in the shared cache)
    :param tuple zooms: ``(closest out, closest in)`` that :attr:`zoom` keeps to
        (default: 0 .. the source's maximum)
    :param int x: the map's position in its parent group
    :param int y: the map's position in its parent group
    :param int strip: rows read per :meth:`load_step`
    :param bool scale_bar: draw a scale bar at the bottom left
    :param bool credit: draw the source's credit at the bottom right (the
        OpenStreetMap tiles' usage policy requires it)
    """

    def __init__(  # noqa: PLR0913, PLR0917
        self,
        width,
        height,
        centre=None,
        zoom=8,
        cache=None,
        zooms=None,
        x=0,
        y=0,
        strip=8,
        scale_bar=True,
        credit=True,
    ):
        super().__init__(x=x, y=y)
        self.width = width
        self.height = height
        self.cache = cache or TileCache()
        self.zooms = zooms or (0, self.cache.source.max_zoom)
        self.strip = strip
        self.bitmap = displayio.Bitmap(width, height, 256)
        """The map as shown: the tiles and what is drawn over them."""
        self.under = displayio.Bitmap(width, height, 256)
        """Just the tiles."""
        self._rows = displayio.Bitmap(TILE, strip, 256)  # a tile's rows, read from its file
        self._rows_mv = memoryview(self._rows)
        self.append(displayio.TileGrid(self.bitmap, pixel_shader=palette()))
        self.credit = text_bitmap(self.cache.source.credit, WHITE) if credit else None
        self._show_scale = scale_bar
        self.scale_bar = None
        """The scale bar's bitmap (None: no scale bar, or no centre)."""
        self.missing = []
        """``(z, x, y)`` tiles to fetch, nearest the centre first."""
        self.net_error = None
        """Why the last download failed (until the view changes), or None."""
        self._todo = []  # strips to (re)load
        self._files = {}  # (x, y) -> open tile BMP, or None, while strips load
        self._centre = centre
        self._zoom = self._clamp(zoom)
        self.set_view()

    # ------------------------------------------------------------ view

    def _clamp(self, zoom):
        return max(self.zooms[0], min(self.zooms[1], zoom))

    @property
    def centre(self):
        """The map's centre, ``(lat, lon)``, or None. Setting it moves the map."""
        return self._centre

    @centre.setter
    def centre(self, centre):
        self.set_view(centre=centre)

    @property
    def zoom(self):
        """The zoom (kept to :attr:`zooms`). Setting it zooms the map."""
        return self._zoom

    @zoom.setter
    def zoom(self, zoom):
        self.set_view(zoom=zoom)

    def set_view(self, centre=None, zoom=None):
        """Move the map to centre and / or zoom (or just reload it): queues
        the tiles not in the cache (:attr:`missing`) and every strip for
        loading. Nothing changes on the screen until :meth:`load` (or
        :meth:`load_step`) and a redraw."""
        if centre is not None:
            self._centre = (centre[0], centre[1])
        if zoom is not None:
            self._zoom = self._clamp(zoom)
        self._close_files()
        self._todo = list(range((self.height + self.strip - 1) // self.strip))
        self.missing = []
        self.net_error = None
        if self._centre is None:
            self.scale_bar = None
            return
        z = self._zoom
        lat, lon = self._centre
        w, h = self.width, self.height
        self.x0 = int(world_x(lon, z)) - w // 2
        self.y0 = int(world_y(lat, z)) - h // 2
        self.clon = lon
        self.cmerc = mercator(lat)
        self.world = TILE << z
        n = 1 << z
        ctx, cty = (self.x0 + w // 2) >> 8, (self.y0 + h // 2) >> 8
        need = []
        seen = set()
        for ty in range(self.y0 >> 8, ((self.y0 + h - 1) >> 8) + 1):
            for tx in range(self.x0 >> 8, ((self.x0 + w - 1) >> 8) + 1):
                # (at low zooms the map wraps round: the same tile twice)
                if 0 <= ty < n and (tx % n, ty) not in seen:
                    seen.add((tx % n, ty))
                    if not self.cache.ready(z, tx % n, ty):
                        need.append(((tx - ctx) ** 2 + (ty - cty) ** 2, tx % n, ty))
        self.missing = [(z, x, y) for _, x, y in sorted(need)]
        self.scale_bar = self._make_scale(lat, z) if self._show_scale else None

    @staticmethod
    def _make_scale(lat, z):
        """The scale bar: the longest 1, 2 or 5 x 10^k that fits in 80 pixels."""
        mpp = metres_per_pixel(lat, z)
        step = 1
        while step * 10 <= mpp * 80:
            step *= 10
        metres = max(m for m in (step, 2 * step, 5 * step) if m <= mpp * 80 or m == step)
        px = max(1, int(metres / mpp))
        text = f"{metres // 1000} km" if metres >= 1000 else f"{metres} m"
        scale = text_bitmap(text, WHITE, px + 8 + 6 * len(text))
        bitmaptools.fill_region(scale, 3, 9, 3 + px, 11, BLACK)
        bitmaptools.fill_region(scale, 3, 4, 4, 11, BLACK)
        bitmaptools.fill_region(scale, 2 + px, 4, 3 + px, 11, BLACK)
        return scale

    def xy(self, lat, lon):
        """(lat, lon) -> ``(x, y)`` on the map, in pixels (outside
        0 .. width, 0 .. height when it is off the map)."""
        return (
            self.width // 2 + round((lon - self.clon) / 360 * self.world),
            self.height // 2 + round((mercator(lat) - self.cmerc) * self.world),
        )

    def latlon(self, x, y):
        """``(x, y)`` on the map -> (lat, lon)."""
        lon = self.clon + (x - self.width // 2) / self.world * 360
        lat = unmercator(self.cmerc + (y - self.height // 2) / self.world)
        return lat, (lon + 540) % 360 - 180

    def pan(self, dx, dy):
        """Move the map by dx widths and dy heights (e.g. ``pan(0.25, 0)``:
        a quarter of the map to the right)."""
        if self._centre is not None:
            self.centre = self.latlon(
                self.width // 2 + dx * self.width, self.height // 2 + dy * self.height
            )

    def zoom_in(self):
        """One zoom closer; True if the zoom changed."""
        z = self._zoom
        self.zoom = z + 1
        return self._zoom != z

    def zoom_out(self):
        """One zoom further out; True if the zoom changed."""
        z = self._zoom
        self.zoom = z - 1
        return self._zoom != z

    def fit(self, points, max_zoom=None, margin=24):
        """Centre the map on the ``(lat, lon)`` points, at the closest zoom
        (up to max_zoom) that shows them all, margin pixels inside the map."""
        lons = [lon for _, lon in points]
        ys = [mercator(lat) for lat, _ in points]
        z = self._clamp(self.zooms[1] if max_zoom is None else max_zoom)
        while z > self.zooms[0] and (
            (max(lons) - min(lons)) / 360 * (TILE << z) > self.width - 2 * margin
            or (max(ys) - min(ys)) * (TILE << z) > self.height - 2 * margin
        ):
            z -= 1
        self.set_view(((unmercator((max(ys) + min(ys)) / 2)), (max(lons) + min(lons)) / 2), z)

    # ---------------------------------------------------------- loading

    @property
    def loading(self):
        """True while strips of the map are still to be read."""
        return bool(self._todo)

    def _tile_file(self, x, y):
        key = (x, y)
        if key not in self._files:
            self._files[key] = self.cache.open(self._zoom, x, y)
        return self._files[key]

    def _close_files(self):
        for f in self._files.values():
            if f:
                f.close()
        self._files = {}

    def load_step(self):
        """Read the next strip of the map into the underlay. Returns the
        rectangle loaded, ``(x1, y1, x2, y2)`` (to redraw), or None when
        the map is loaded."""
        if not self._todo:
            self._close_files()
            return None
        y1 = self._todo.pop(0) * self.strip
        y2 = min(self.height, y1 + self.strip)
        w = self.width
        if self._centre is None:
            bitmaptools.fill_region(self.under, 0, y1, w, y2, WAITING)
            return (0, y1, w, y2)
        n = 1 << self._zoom
        y = y1
        while y < y2:
            # Rows y .. y + h are in the same row of tiles.
            gy = self.y0 + y
            ty = gy >> 8
            h = min(y2 - y, TILE - (gy & 255))
            sx = 0
            while sx < w:
                gx = self.x0 + sx
                off = gx & 255
                m = min(TILE - off, w - sx)
                f = self._tile_file((gx >> 8) % n, ty) if 0 <= ty < n else None
                if f is None:
                    bitmaptools.fill_region(self.under, sx, y, sx + m, y + h, NO_TILE)
                else:
                    f.seek(BMP_HEADER + (gy & 255) * TILE)
                    f.readinto(self._rows_mv[: h * TILE])
                    bitmaptools.blit(self.under, self._rows, sx, y, x1=off, y1=0, x2=off + m, y2=h)
                sx += m
            y += h
        return (0, y1, w, y2)

    def load(self):
        """Read every strip still to load. True if there were any."""
        if not self._todo:
            return False
        while self.load_step():
            pass
        return True

    # ---------------------------------------------------------- fetching

    def fetch_step(self):
        """Download (if needed) and convert the next missing tile: seconds.
        Returns False if no tile was waiting. A download that fails sets
        :attr:`net_error` and drops the tiles that need downloading (the
        rest still convert); setting the view again retries them. A tile
        fetched for the current view is queued to load."""
        if not self.missing:
            return False
        z, x, y = self.missing.pop(0)
        fetch = not self.cache.has_png(z, x, y)
        try:
            self.cache.get(z, x, y)
        except Exception as error:
            print("TILE_ERROR", z, x, y, type(error).__name__, error)
            if fetch:
                self.net_error = f"{type(error).__name__} {error}"
                self.missing = [t for t in self.missing if self.cache.has_png(*t)]
            return True
        if z == self._zoom and self._centre is not None:
            f = self._files.pop((x, y), None)
            if f:
                f.close()
            # Reload the strips the tile is in.
            top = (y << 8) - self.y0
            for k in range(
                max(0, top // self.strip), min(self.height, top + TILE) // self.strip + 1
            ):
                if k * self.strip < self.height and k not in self._todo:
                    self._todo.append(k)
        return True

    def update(self, progress=None):
        """Fetch every missing tile, then load the map: for programs that can
        wait (seconds a tile). progress(done, total) is called before each
        tile."""
        total = len(self.missing)
        while self.missing:
            if progress:
                progress(total - len(self.missing), total)
            self.fetch_step()
        self.load()

    # ---------------------------------------------------------- drawing

    def _clip(self, clip):
        if clip is None:
            return (0, 0, self.width, self.height)
        return (
            max(0, clip[0]),
            max(0, clip[1]),
            min(self.width, clip[2]),
            min(self.height, clip[3]),
        )

    def restore(self, clip=None):
        """Copy the tiles back over the map, in clip ``(x1, y1, x2, y2)``
        (default: all of it), wiping what was drawn there."""
        x1, y1, x2, y2 = self._clip(clip)
        if x1 < x2 and y1 < y2:
            bitmaptools.blit(self.bitmap, self.under, x1, y1, x1=x1, y1=y1, x2=x2, y2=y2)

    def fill(self, x1, y1, x2, y2, colour, clip=None):  # noqa: PLR0913, PLR0917
        """A filled rectangle, x2 and y2 exclusive."""
        c = self._clip(clip)
        x1, y1, x2, y2 = max(x1, c[0]), max(y1, c[1]), min(x2, c[2]), min(y2, c[3])
        if x1 < x2 and y1 < y2:
            bitmaptools.fill_region(self.bitmap, x1, y1, x2, y2, colour)

    def line(self, x1, y1, x2, y2, colour, clip=None):  # noqa: PLR0913, PLR0917
        """A line, clipped (Liang-Barsky)."""
        c = self._clip(clip)
        dx, dy = x2 - x1, y2 - y1
        t0, t1 = 0, 1
        for p, q in ((-dx, x1 - c[0]), (dx, c[2] - 1 - x1), (-dy, y1 - c[1]), (dy, c[3] - 1 - y1)):
            if p == 0:
                if q < 0:
                    return
            elif p < 0:
                t0 = max(t0, q / p)
            else:
                t1 = min(t1, q / p)
        if t0 <= t1:
            bitmaptools.draw_line(
                self.bitmap,
                round(x1 + t0 * dx),
                round(y1 + t0 * dy),
                round(x1 + t1 * dx),
                round(y1 + t1 * dy),
                colour,
            )

    def blit(self, src, x, y, clip=None):
        """The bitmap src (256 colours, RGB332) with its top left at x, y."""
        c = self._clip(clip)
        x1, y1 = max(x, c[0]), max(y, c[1])
        x2, y2 = min(x + src.width, c[2]), min(y + src.height, c[3])
        if x1 < x2 and y1 < y2:
            bitmaptools.blit(self.bitmap, src, x1, y1, x1=x1 - x, y1=y1 - y, x2=x2 - x, y2=y2 - y)

    def cross(self, x, y, clip=None):
        """A white cross with a black middle, 13 pixels across, centred on
        x, y (e.g. home)."""
        self.fill(x - 6, y - 2, x + 7, y + 3, WHITE, clip)
        self.fill(x - 2, y - 6, x + 3, y + 7, WHITE, clip)
        self.fill(x - 5, y - 1, x + 6, y + 2, BLACK, clip)
        self.fill(x - 1, y - 5, x + 2, y + 6, BLACK, clip)

    def draw_legend(self, clip=None):
        """The scale bar (bottom left) and the credit (bottom right)."""
        if self.scale_bar is not None:
            self.blit(self.scale_bar, 0, self.height - self.scale_bar.height, clip)
        if self.credit is not None and self._centre is not None:
            self.blit(
                self.credit,
                self.width - self.credit.width,
                self.height - self.credit.height,
                clip,
            )

    def dirty(self, clip=None):
        """Mark the map (or clip of it) changed, for the next display refresh."""
        if clip is None:
            self.bitmap.dirty()
        else:
            x1, y1, x2, y2 = self._clip(clip)
            if x1 < x2 and y1 < y2:
                self.bitmap.dirty(x1, y1, x2, y2)

    def redraw(self):
        """The tiles and the legend, nothing else (a plain map): loads what
        is still to load, then draws."""
        self.load()
        self.restore()
        self.draw_legend()
        self.dirty()

    def close(self):
        """Close any tile files left open by a partly loaded map."""
        self._close_files()
