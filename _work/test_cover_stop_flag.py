"""Reproduce BUG-C1: cover keeps is_opening=True after a manual stop.

Run:  python3 _work/test_cover_stop_flag.py

Uses a mock device (no real HDL gateway) and a fake monotonic clock. The cover
platform is imported through _work/ha_stub/ so no real Home Assistant runtime is
required. NOTHING under custom_components/ is modified by this script.
"""
import os
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(HERE, "ha_stub"))
sys.path.insert(0, ROOT)

from homeassistant.core import HomeAssistant

from custom_components.buspro import DATA_BUSPRO
from custom_components.buspro import cover as cover_mod


class FakeClock:
    """Stand-in for the ``time`` module; only monotonic() is used by cover.py."""

    def __init__(self, t=1000.0):
        self.t = t

    def monotonic(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


class FakeModule:
    def __init__(self, connected=True):
        self.connected = connected


class FakeDevice:
    """Minimal mock cover device: no bus, no gateway."""

    def __init__(self):
        self.name = "mock-cover"
        self.status = 0
        self.level = 0
        self.is_moving = False
        self.is_closed = True
        self._cbs = []

    def register_device_updated_cb(self, cb):
        self._cbs.append(cb)

    async def open(self):
        pass

    async def close(self):
        pass

    async def stop(self):
        pass


def make_hass(connected=True):
    hass = HomeAssistant()
    hass.data[DATA_BUSPRO] = FakeModule(connected)
    return hass


class CoverStopFlagTests(unittest.IsolatedAsyncioTestCase):
    async def test_stop_clears_opening_flag(self):
        clock = FakeClock()
        cover = cover_mod.BusproCover(make_hass(), FakeDevice(), "bus_motor", 10)

        with mock.patch.object(cover_mod, "time", clock):
            await cover.async_open_cover()
            self.assertEqual(cover.state, "opening",
                             "precondition: a plain open must show 'opening'")

            await cover.async_stop_cover()
            self.assertNotEqual(
                cover.state, "opening",
                "BUG-C1: after async_stop_cover() the cover still reports "
                f"'opening' (state={cover.state!r}, is_opening={cover.is_opening!r})"
            )
            self.assertIs(
                cover.is_opening, False,
                f"BUG-C1: is_opening must be False after stop, got {cover.is_opening!r}"
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
