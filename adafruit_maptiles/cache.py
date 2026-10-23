# SPDX-FileCopyrightText: Copyright (c) 2026 Tim Cocks for Adafruit Industries
#
# SPDX-License-Identifier: MIT
"""
`adafruit_maptiles.cache`
================================================================================

The tile cache on the SD card, shared by every program that uses this
library: a tile downloaded once (by any of them) is there for all of
them. Each tile source has its own folder, in the source's own
``{z}/{x}/{y}`` layout::

    /sd/tiles/osm/9/122/196.png     as downloaded
    /sd/tiles/osm/9/122/196.bmp     the same, 8-bit RGB332, rows top-down

Only the BMP is read to draw the map (:mod:`adafruit_maptiles.png`).
Downloads and conversions each take seconds, so they happen once per
tile; files are written under a temporary name and renamed when complete,
so a reset part way leaves no broken tile. Tiles copied to the card from
a PC (PNG or BMP, same layout) are used as they are.

The default source is OpenStreetMap's standard tile server. Its usage
policy (https://operations.osmfoundation.org/policies/tiles/) asks for a
User-Agent that identifies the program, that downloaded tiles are kept
(which the cache does) and that the map shows "(C) OpenStreetMap
contributors" (:class:`~adafruit_maptiles.map_view.MapView` does). Pass
your program's name as ``user_agent``.

Plain Python apart from mounting the card and downloading.
"""

import os

from adafruit_maptiles import TILE
from adafruit_maptiles.png import png_to_bmp

CACHE_ROOT = "/sd/tiles"
"""Where every program's tiles go, a folder per source."""

USER_AGENT = "adafruit_maptiles/1.0 CircuitPython"


class TileSource:
    """Where tiles come from.

    :param str name: the cache folder's name (under ``CACHE_ROOT``); one per
        tile style, shared by everything that uses that style
    :param str url: the tile URL, with ``{z}``, ``{x}`` and ``{y}``
    :param str credit: the attribution that must be shown on the map
    :param int max_zoom: the closest zoom the server has
    """

    def __init__(self, name, url, credit, max_zoom=19):
        self.name = name
        self.url = url
        self.credit = credit
        self.max_zoom = max_zoom


OSM = TileSource(
    "osm", "https://tile.openstreetmap.org/{z}/{x}/{y}.png", "(C) OpenStreetMap contributors"
)
"""OpenStreetMap's standard tiles (the default)."""


def _exists(path):
    try:
        os.stat(path)
        return True
    except OSError:
        return False


def _makedirs(path):
    at = ""
    for part in path.strip("/").split("/"):
        at += "/" + part
        try:
            os.mkdir(at)
        except OSError:
            pass


def mount_sd(path="/sd"):
    """True if a card is mounted at path, mounting the board's SD card
    (``board.SD_SCK``, ``SD_MOSI``, ``SD_MISO``, ``SD_CS``, or ``board.SPI()``
    and ``SD_CS`` on boards without SD bus pins) there if the supervisor or
    the program has not already."""
    try:
        if os.statvfs(path) != os.statvfs("/"):
            return True
    except OSError:
        pass
    spi = None  # a bus made here, released if the card does not mount
    try:
        import board  # noqa: PLC0415
        import busio  # noqa: PLC0415
        import sdcardio  # noqa: PLC0415
        import storage  # noqa: PLC0415

        if hasattr(board, "SD_SCK"):
            spi = busio.SPI(board.SD_SCK, board.SD_MOSI, board.SD_MISO)
            bus = spi
        else:
            # The card shares the board's SPI bus (e.g. PyPortal, Metro
            # ESP32-S3) with other devices, so the bus is left as it is.
            bus = board.SPI()
        storage.mount(storage.VfsFat(sdcardio.SDCard(bus, board.SD_CS)), path)
        return os.statvfs(path) != os.statvfs("/")
    except Exception as error:
        if spi is not None:
            try:
                spi.deinit()
            except Exception:
                pass
        print("SD_MOUNT_ERROR", type(error).__name__, error)
        return False


_radio = None
_session = None


def _find_radio():
    """The board's native Wi-Fi (``wifi.radio``), otherwise its ESP32
    co-processor: shared through the core when the core is managing it (web
    workflow), otherwise driven over the ``board.ESP_*`` pins from here."""
    try:
        import wifi  # noqa: PLC0415

        return wifi.radio
    except ImportError:
        pass
    from adafruit_esp32spi import adafruit_esp32spi  # noqa: PLC0415

    try:
        import _airlift  # noqa: PLC0415, PLC2701

        shared = _airlift.active()
    except ImportError:
        shared = False
    if shared:
        return adafruit_esp32spi.ESP_SPIcontrol()
    import board  # noqa: PLC0415
    from digitalio import DigitalInOut  # noqa: PLC0415

    gpio0 = getattr(board, "ESP_GPIO0", None)
    return adafruit_esp32spi.ESP_SPIcontrol(
        board.SPI(),
        DigitalInOut(board.ESP_CS),
        DigitalInOut(board.ESP_BUSY),
        DigitalInOut(board.ESP_RESET),
        DigitalInOut(gpio0) if gpio0 else None,
    )


def default_session():
    """An ``adafruit_requests`` session, made once and shared by every cache:
    over the board's native Wi-Fi (``wifi.radio``), otherwise through an
    ESP32 co-processor (``adafruit_esp32spi``; on the Fruit Jam the core
    shares its ESP32-C6 when web workflow is running). If the radio is not
    connected yet, it joins ``CIRCUITPY_WIFI_SSID`` with
    ``CIRCUITPY_WIFI_PASSWORD`` from settings.toml."""
    global _radio, _session  # noqa: PLW0603
    if _session is None:
        import adafruit_connection_manager  # noqa: PLC0415
        import adafruit_requests  # noqa: PLC0415

        if _radio is None:
            _radio = _find_radio()
        if not _radio.connected:
            ssid = os.getenv("CIRCUITPY_WIFI_SSID")
            if not ssid:
                raise RuntimeError("Wi-Fi is not connected, and CIRCUITPY_WIFI_SSID is not set")
            _radio.connect(ssid, os.getenv("CIRCUITPY_WIFI_PASSWORD", ""))
        pool = adafruit_connection_manager.get_radio_socketpool(_radio)
        ssl = adafruit_connection_manager.get_radio_ssl_context(_radio)
        _session = adafruit_requests.Session(pool, ssl)
    return _session


class TileCache:
    """The cached tiles of one source, fetched when missing.

    :param TileSource source: where tiles come from (default: :data:`OSM`)
    :param str root: the cache's top folder (default: :data:`CACHE_ROOT`);
        a root on ``/sd`` mounts the SD card when the cache is first used
    :param str user_agent: identifies the program to the tile server
    :param session: an ``adafruit_requests.Session`` to download with
        (default: :func:`default_session`, made at the first download)
    """

    def __init__(self, source=OSM, root=CACHE_ROOT, user_agent=USER_AGENT, session=None):
        self.source = source
        root = root.rstrip("/")
        self.folder = f"{root}/{source.name}"
        self.user_agent = user_agent
        self._session = session
        self._mounted = None
        self.fetched = 0
        """Tiles downloaded since the cache was made."""
        self.converted = 0
        """Tiles converted to BMP since the cache was made."""

    @property
    def available(self):
        """True if the card the cache is on is there (mounting it the first
        time, if needed)."""
        if self._mounted is None:
            self._mounted = mount_sd() if self.folder.startswith("/sd/") else True
        return self._mounted

    def path(self, z, x, y, ext="bmp"):
        """The file of tile (z, x, y): ``ext`` ``"bmp"`` or ``"png"``."""
        return f"{self.folder}/{z}/{x}/{y}.{ext}"

    def ready(self, z, x, y):
        """True if the tile's BMP is in the cache."""
        return self.available and _exists(self.path(z, x, y))

    def has_png(self, z, x, y):
        """True if the tile's PNG is in the cache (no download needed)."""
        return self.available and _exists(self.path(z, x, y, "png"))

    def open(self, z, x, y):
        """The tile's BMP, open for reading (pixels from ``png.BMP_HEADER``),
        or None if it is not in the cache."""
        try:
            return open(self.path(z, x, y), "rb")
        except OSError:
            return None

    def session(self):
        """The ``adafruit_requests`` session downloads use."""
        if self._session is None:
            self._session = default_session()
        return self._session

    def fetch(self, z, x, y):
        """Download the tile's PNG to the cache; returns its bytes."""
        url = self.source.url.format(z=z, x=x, y=y)
        r = self.session().get(url, headers={"User-Agent": self.user_agent})
        try:
            if r.status_code != 200:
                raise OSError(f"HTTP {r.status_code} for {url}")
            data = r.content
        finally:
            r.close()
        path = self.path(z, x, y, "png")
        _makedirs(path[: path.rfind("/")])
        with open(path + ".tmp", "wb") as f:
            f.write(data)
        os.rename(path + ".tmp", path)
        self.fetched += 1
        return data

    def convert(self, z, x, y, data=None):
        """The tile's PNG (data, or the cached file) -> its BMP."""
        if data is None:
            with open(self.path(z, x, y, "png"), "rb") as f:
                data = f.read()
        path = self.path(z, x, y)
        _makedirs(path[: path.rfind("/")])
        with open(path + ".tmp", "wb") as f:
            size = png_to_bmp(data, f)
        if size != (TILE, TILE):
            os.remove(path + ".tmp")
            raise ValueError(f"tile is {size[0]}x{size[1]}")
        os.rename(path + ".tmp", path)
        self.converted += 1

    def get(self, z, x, y):
        """Make sure the tile's BMP is in the cache: downloads the PNG if it
        is not there either, then converts it. Seconds. Returns True if it
        downloaded.

        :raises OSError: no card, no network, or the server refused
        :raises ValueError: the PNG cannot be read
        """
        if self.ready(z, x, y):
            return False
        if not self.available:
            raise OSError("no SD card for the tile cache")
        fetch = not self.has_png(z, x, y)
        self.convert(z, x, y, self.fetch(z, x, y) if fetch else None)
        return fetch
