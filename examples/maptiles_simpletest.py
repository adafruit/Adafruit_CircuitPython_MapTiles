# SPDX-FileCopyrightText: Copyright (c) 2026 Tim Cocks for Adafruit Industries
#
# SPDX-License-Identifier: MIT
"""An OpenStreetMap map on a Fruit Jam's HDMI display. Tiles are downloaded
(Wi-Fi in settings.toml) into the shared cache on the SD card,
/sd/tiles/osm, the first time they are needed. Keys on the serial console:
w a s d pan, + / - zoom."""

import sys

import supervisor
from adafruit_fruitjam.peripherals import request_display_config

from adafruit_maptiles.cache import TileCache
from adafruit_maptiles.map_view import MapView

CENTRE = (40.7128, -74.0060)  # (lat, lon)
ZOOM = 12

request_display_config(320, 240, color_depth=8)
display = supervisor.runtime.display
display.auto_refresh = False

# Name your program in the User-Agent: the OpenStreetMap tile servers ask for it.
cache = TileCache(user_agent="maptiles_simpletest/1.0 CircuitPython")
view = MapView(display.width, display.height, CENTRE, ZOOM, cache=cache)
display.root_group = view


def show():
    view.update(progress=lambda done, total: print(f"tile {done + 1} of {total}"))
    if view.net_error:
        print("Download failed:", view.net_error)
    view.redraw()  # the tiles and the scale bar and credit; draw markers before dirty()
    lat, lon = view.centre
    x, y = view.xy(lat, lon)
    view.cross(x, y)
    view.dirty()
    display.refresh()


show()
while True:
    if supervisor.runtime.serial_bytes_available:
        key = sys.stdin.read(1)
        if key in "wasd":
            view.pan(*{"w": (0, -0.25), "a": (-0.25, 0), "s": (0, 0.25), "d": (0.25, 0)}[key])
        elif key in "+=":
            view.zoom_in()
        elif key == "-":
            view.zoom_out()
        else:
            continue
        show()
