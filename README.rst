Introduction
============


.. image:: https://readthedocs.org/projects/adafruit-circuitpython-maptiles/badge/?version=latest
    :target: https://docs.circuitpython.org/projects/maptiles/en/latest/
    :alt: Documentation Status


.. image:: https://raw.githubusercontent.com/adafruit/Adafruit_CircuitPython_Bundle/main/badges/adafruit_discord.svg
    :target: https://adafru.it/discord
    :alt: Discord


.. image:: https://github.com/adafruit/Adafruit_CircuitPython_MapTiles/workflows/Build%20CI/badge.svg
    :target: https://github.com/adafruit/Adafruit_CircuitPython_MapTiles/actions
    :alt: Build Status


.. image:: https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json
    :target: https://github.com/astral-sh/ruff
    :alt: Code Style: Ruff

Slippy-map (OpenStreetMap style) tiles for CircuitPython displays: a map
widget, and a tile cache on the SD card that every program using this
library shares.


Dependencies
=============
This driver depends on:

* `Adafruit CircuitPython <https://github.com/adafruit/circuitpython>`_

Please ensure all dependencies are available on the CircuitPython filesystem.
This is easily achieved by downloading
`the Adafruit library and driver bundle <https://circuitpython.org/libraries>`_
or individual libraries can be installed using
`circup <https://github.com/adafruit/circup>`_.


`Adafruit Fruit Jam <https://www.adafruit.com/product/6200>`_

Installing from PyPI
=====================

On supported GNU/Linux systems like the Raspberry Pi, you can install the driver locally `from
PyPI <https://pypi.org/project/adafruit-circuitpython-maptiles/>`_.
To install for current user:

.. code-block:: shell

    pip3 install adafruit-circuitpython-maptiles

To install system-wide (this may be required in some cases):

.. code-block:: shell

    sudo pip3 install adafruit-circuitpython-maptiles

To install in a virtual environment in your current project:

.. code-block:: shell

    mkdir project-name && cd project-name
    python3 -m venv .venv
    source .env/bin/activate
    pip3 install adafruit-circuitpython-maptiles

Installing to a Connected CircuitPython Device with Circup
==========================================================

Make sure that you have ``circup`` installed in your Python environment.
Install it with the following command if necessary:

.. code-block:: shell

    pip3 install circup

With ``circup`` installed and your CircuitPython device connected use the
following command to install:

.. code-block:: shell

    circup install adafruit_maptiles

Or the following command to update an existing version:

.. code-block:: shell

    circup update

Usage Example
=============

.. code-block:: python

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


Documentation
=============
API documentation for this library can be found on `Read the Docs <https://docs.circuitpython.org/projects/maptiles/en/latest/>`_.

For information on building library documentation, please check out
`this guide <https://learn.adafruit.com/creating-and-sharing-a-circuitpython-library/sharing-our-docs-on-readthedocs#sphinx-5-1>`_.

Contributing
============

Contributions are welcome! Please read our `Code of Conduct
<https://github.com/adafruit/Adafruit_CircuitPython_MapTiles/blob/HEAD/CODE_OF_CONDUCT.md>`_
before contributing to help this project stay welcoming.
