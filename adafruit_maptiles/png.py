# SPDX-FileCopyrightText: Copyright (c) 2026 Tim Cocks for Adafruit Industries
#
# SPDX-License-Identifier: MIT
"""
`adafruit_maptiles.png`
================================================================================

PNG -> 8-bit BMP conversion, in the RGB332 colours that a CircuitPython
8-bit framebuffer (e.g. ``picodvi`` with ``color_depth=8``) shows
directly. The BMP's rows are top-down and, for a 256 pixel wide tile, need
no padding, so a row of the tile is at ``BMP_HEADER + row * 256`` in the
file and can be read straight into a :class:`displayio.Bitmap`.

Converting a 256x256 tile takes ~1.5 s on an RP2350 (zlib, then a palette
lookup per pixel in Python), so it is done once per tile and the BMP kept.

Plain Python: it runs on a PC too (the tests do).
"""

import struct
import zlib

from adafruit_maptiles import rgb332, rgb332_colour

BMP_HEADER = 14 + 40 + 1024
"""Bytes before the pixels: file header, BITMAPINFOHEADER, 256-colour palette."""


def bmp_header(width, height):
    """8-bit top-down BMP header with the RGB332 palette."""
    h = bytearray(BMP_HEADER)
    stride = (width + 3) & ~3
    struct.pack_into("<2sIHHI", h, 0, b"BM", BMP_HEADER + stride * height, 0, 0, BMP_HEADER)
    struct.pack_into(
        "<IiiHHIIiiII", h, 14, 40, width, -height, 1, 8, 0, stride * height, 2835, 2835, 256, 0
    )
    for v in range(256):
        c = rgb332_colour(v)
        struct.pack_into("<BBBB", h, 54 + 4 * v, c & 255, c >> 8 & 255, c >> 16, 0)
    return h


def _unfilter(f, cur, prev, bpp):
    """Undo one PNG row filter in place (prev: the row above, unfiltered)."""
    n = len(cur)
    if f == 1:
        for i in range(bpp, n):
            cur[i] = (cur[i] + cur[i - bpp]) & 255
    elif f == 2:
        for i in range(n):
            cur[i] = (cur[i] + prev[i]) & 255
    elif f == 3:
        for i in range(n):
            a = cur[i - bpp] if i >= bpp else 0
            cur[i] = (cur[i] + ((a + prev[i]) >> 1)) & 255
    elif f == 4:
        for i in range(n):
            a = cur[i - bpp] if i >= bpp else 0
            c = prev[i - bpp] if i >= bpp else 0
            b = prev[i]
            p = a + b - c
            pa = abs(p - a)
            pb = abs(p - b)
            pc = abs(p - c)
            cur[i] = (cur[i] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
    elif f:
        raise ValueError(f"PNG filter {f}")


def png_to_bmp(data, out):  # noqa: PLR0912, PLR0914, PLR0915
    """A PNG (bytes) -> an RGB332 BMP written to the open file out.
    Indexed (1-8 bit), grey (1-8 bit) and 8-bit RGB, RGBA and grey+alpha;
    transparent pixels become white. Returns ``(width, height)``.

    :raises ValueError: not a PNG this can read (interlaced, 16-bit)
    """
    if bytes(data[:8]) != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG")
    mv = memoryview(data)
    pos = 8
    idat = bytearray()
    plte = trns = None
    width = 0
    while pos + 8 <= len(data):
        ln = int.from_bytes(bytes(mv[pos : pos + 4]), "big")
        kind = bytes(mv[pos + 4 : pos + 8])
        body = mv[pos + 8 : pos + 8 + ln]
        if kind == b"IHDR":
            width, height, depth, ctype, _, _, interlace = struct.unpack(">IIBBBBB", bytes(body))
        elif kind == b"PLTE":
            plte = bytes(body)
        elif kind == b"tRNS":
            trns = bytes(body)
        elif kind == b"IDAT":
            idat.extend(body)
        elif kind == b"IEND":
            break
        pos += 12 + ln
    if not width:
        raise ValueError("no IHDR")
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(ctype)
    if channels is None or interlace or depth > 8 or (depth < 8 and ctype not in {0, 3}):
        raise ValueError(f"PNG type {ctype} depth {depth} interlace {interlace}")
    raw = zlib.decompress(idat)
    del idat
    stride = (width * channels * depth + 7) // 8
    if len(raw) < (stride + 1) * height:
        raise ValueError("PNG data short")
    bpp = max(1, channels * depth // 8)
    # Indexed and grey: one lookup per pixel value.
    lut = None
    if ctype == 3:
        lut = bytearray(b"\xff" * 256)
        for i in range(min(256, len(plte or b"") // 3)):
            if trns is None or i >= len(trns) or trns[i] >= 128:
                lut[i] = rgb332(plte[3 * i], plte[3 * i + 1], plte[3 * i + 2])
    elif ctype == 0:
        levels = (1 << depth) - 1
        lut = bytearray(256)
        for i in range(levels + 1):
            v = i * 255 // levels
            lut[i] = rgb332(v, v, v)
    pad = (width + 3) & ~3
    row = bytearray(pad)
    prev = bytearray(stride)
    out.write(bmp_header(width, height))
    per_byte = 8 // depth
    mask = (1 << depth) - 1
    for y in range(height):
        at = y * (stride + 1)
        f = raw[at]
        cur = bytearray(raw[at + 1 : at + 1 + stride])
        if f:
            _unfilter(f, cur, prev, bpp)
        if lut is not None and depth == 8:
            for x in range(width):
                row[x] = lut[cur[x]]
        elif lut is not None:
            for x in range(width):
                shift = 8 - depth * (x % per_byte + 1)
                row[x] = lut[cur[x // per_byte] >> shift & mask]
        elif ctype == 2:
            for x in range(width):
                k = 3 * x
                row[x] = rgb332(cur[k], cur[k + 1], cur[k + 2])
        elif ctype == 6:
            for x in range(width):
                k = 4 * x
                row[x] = rgb332(cur[k], cur[k + 1], cur[k + 2]) if cur[k + 3] >= 128 else 0xFF
        else:  # grey + alpha
            for x in range(width):
                v = cur[2 * x]
                row[x] = rgb332(v, v, v) if cur[2 * x + 1] >= 128 else 0xFF
        out.write(row)
        prev = cur
    return width, height
