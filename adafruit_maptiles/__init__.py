# SPDX-FileCopyrightText: Copyright (c) 2026 Tim Cocks for Adafruit Industries
#
# SPDX-License-Identifier: MIT
"""
`adafruit_maptiles`
================================================================================

Slippy-map (OpenStreetMap style) tiles for CircuitPython displays: a map
widget, and a tile cache on the SD card that every program using this
library shares.

* Author(s): Tim Cocks

The package:

* :mod:`adafruit_maptiles` (this module): the tile maths (Web Mercator,
  256x256 tiles, ``{z}/{x}/{y}``) and the RGB332 colours the cached tiles
  are stored in. Plain Python.
* :mod:`adafruit_maptiles.png`: PNG -> 8-bit RGB332 BMP conversion. Plain
  Python (``zlib``).
* :mod:`adafruit_maptiles.cache`: :class:`~adafruit_maptiles.cache.TileCache`,
  the shared cache (``/sd/tiles/<source>/{z}/{x}/{y}.bmp``), downloads, and
  mounting the SD card.
* :mod:`adafruit_maptiles.map_view`: :class:`~adafruit_maptiles.map_view.MapView`,
  a :class:`displayio.Group` showing the map, with helpers for drawing on
  it.

Implementation Notes
--------------------

**Hardware:**

* A board with an SD card slot (``board.SD_*`` pins, or a card mounted at
  ``/sd`` by the program) and a network connection, e.g. the
  `Adafruit Fruit Jam <https://www.adafruit.com/product/6200>`_.

**Software and Dependencies:**

* Adafruit CircuitPython firmware for the supported boards:
  https://circuitpython.org/downloads
* Adafruit's Requests library: https://github.com/adafruit/Adafruit_CircuitPython_Requests
* Adafruit's Connection Manager library:
  https://github.com/adafruit/Adafruit_CircuitPython_ConnectionManager
* Adafruit's ESP32SPI library (boards with an ESP32 co-processor, such as
  the Fruit Jam): https://github.com/adafruit/Adafruit_CircuitPython_ESP32SPI
"""

import math

__version__ = "0.0.0+auto.0"
__repo__ = "https://github.com/adafruit/Adafruit_CircuitPython_MapTiles.git"

TILE = 256
"""Tile width and height, pixels."""


# ----------------------------------------------------------------- maths


def world_x(lon, zoom):
    """Longitude -> x in pixels on the whole map at this zoom."""
    return (lon + 180.0) / 360.0 * (TILE << zoom)


def mercator(lat):
    """Latitude -> Mercator y, -0.5 (85.05N) .. 0.5 (85.05S)."""
    lat = max(-85.05, min(85.05, lat))
    s = math.sin(math.radians(lat))
    return -math.log((1 + s) / (1 - s)) / (4 * math.pi)


def unmercator(y):
    """Mercator y -> latitude (:func:`mercator`'s inverse)."""
    k = math.exp(-4 * math.pi * max(-0.5, min(0.5, y)))
    return math.degrees(math.asin((k - 1) / (k + 1)))


def world_y(lat, zoom):
    """Latitude -> y in pixels on the whole map at this zoom."""
    return (0.5 + mercator(lat)) * (TILE << zoom)


def tile_xy(lat, lon, zoom):
    """The ``(x, y)`` numbers of the tile that (lat, lon) is on."""
    n = 1 << zoom
    return int(world_x(lon, zoom)) // TILE % n, min(n - 1, int(world_y(lat, zoom)) // TILE)


def metres_per_pixel(lat, zoom):
    """Ground distance across one map pixel at this latitude and zoom."""
    return 156543.034 * math.cos(math.radians(lat)) / (1 << zoom)


# ------------------------------------------------------------ colours


def rgb332(r, g, b):
    """8-bit r, g, b -> an RGB332 value (the cached tiles' pixels)."""
    return (r & 0xE0) | (g & 0xE0) >> 3 | b >> 6


def rgb332_colour(v):
    """RGB332 value -> 0xRRGGBB."""
    return ((v >> 5) * 255 // 7) << 16 | ((v >> 2 & 7) * 255 // 7) << 8 | (v & 3) * 85
