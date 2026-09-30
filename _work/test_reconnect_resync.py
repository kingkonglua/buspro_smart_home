#!/usr/bin/env python3
"""Regression test for BUG-1 (post-reconnect device resync) and BUG-2
(Light/Switch ``read_status`` missing).

Run:  python3 _work/test_reconnect_resync.py

BUG-1: after ``BusproModule._reconnect_loop()`` succeeds, every device that is
registered on the gateway's telegram-callback list must receive at least one
status re-read request, and a failing read must not abort the reconnect.

BUG-2: ``Light.read_status()`` / ``Switch.read_status()`` must no longer raise
``NotImplementedError``; they must send a real 0xE0 ``ReadStatusOfChannels``
telegram (the same control the base ``Device`` read path uses).

The test builds a fake transport (no sockets) and drives the real integration
objects, so it runs in milliseconds.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(HERE, "ha_stub"))
sys.path.insert(0, os.path.join(HERE, "pylibs"))
sys.path.insert(0, ROOT)

import custom_components.buspro as bp  # noqa: E402
from custom_components.buspro.pybuspro.devices import Light, Switch  # noqa: E402
from custom_components.buspro.pybuspro.devices.device import Device  # noqa: E402
from custom_components.buspro.pybuspro.helpers.enums import OperateCode  # noqa: E402

logging.basicConfig(level=logging.CRITICAL)
logging.getLogger("buspro").setLevel(logging.CRITICAL)

# Device construction schedules a delayed (run_from_init=True) read task. That
# background read is irrelevant here (and would sleep 3 s); neutralize it so
# the test stays deterministic and leak-free. The read_status() path under test
# is a separate, immediate send.
Device._call_read_current_status_of_channels = (
    lambda self, run_from_init=False: None
)


class FakeNetworkInterface:
    """Counts sent telegrams instead of touching a socket."""

    def __init__(self):
        self.sent = []
        self.started = False

    async def start(self):
        self.started = True

    async def stop(self):
        self.started = False

    async def send_telegram(self, telegram):
        self.sent.append(telegram)


class FakeHdl:
    """Minimal stand-in for the pybuspro ``Buspro`` client."""

    def __init__(self):
        self.network_interface = FakeNetworkInterface()
        self._telegram_received_cbs = []
        self.on_connection_lost = None
        self.started = False

    def register_telegram_received_device_cb(
        self, telegram_received_cb, device_address, postfix=None
    ):
        self._telegram_received_cbs.append(
            {
                "callback": telegram_received_cb,
                "device_address": device_address,
                "postfix": postfix,
            }
        )

    def unregister_telegram_received_device_cb(
        self, telegram_received_cb, device_address, postfix=None
    ):
        try:
            self._telegram_received_cbs.remove(
                {
                    "callback": telegram_received_cb,
                    "device_address": device_address,
                    "postfix": postfix,
                }
            )
        except ValueError:
            pass

    async def start(self, state_updater=False):
        self.network_interface.started = True
        self.started = True

    async def stop(self):
        self.network_interface.started = False
        self.started = False


class FakeHass:
    def __init__(self, loop):
        self.loop = loop


def _make_module(loop):
    module = bp.BusproModule(FakeHass(loop), "127.0.0.1", 6000)
    hdl = FakeHdl()
    module.hdl = hdl

    async def _noop():
        return None

    # The reconnect path refreshes the source-IP filter / advertised IP using
    # real sockets + DNS; irrelevant to the resync behaviour under test.
    module._async_refresh_source_filter = _noop
    module._async_refresh_advertised_ip = _noop
    return module, hdl


async def test_bug1_reconnect_resync():
    """Every registered device is re-read once after a successful reconnect."""
    loop = asyncio.get_running_loop()
    module, hdl = _make_module(loop)

    light_a = Light(hdl, (1, 10), 1, "L-A")
    bad = Light(hdl, (1, 11), 1, "L-BAD")
    light_b = Light(hdl, (1, 12), 1, "L-B")

    calls = {}

    def _record(device):
        async def _read():
            calls[device.device_identifier] = calls.get(
                device.device_identifier, 0
            ) + 1
        return _read

    async def _boom():
        raise RuntimeError("simulated device read failure")

    light_a.read_status = _record(light_a)
    light_b.read_status = _record(light_b)
    bad.read_status = _boom

    bp.RECONNECT_DELAYS = [0]
    await module._reconnect_loop()

    assert module.connected is True, "reconnect must still count as success"

    missing = [d for d in (light_a, light_b)
               if calls.get(d.device_identifier, 0) < 1]
    assert not missing, (
        "BUG-1: devices were NOT re-read after reconnect: "
        f"{[d.device_identifier for d in missing]} (calls={calls})"
    )
    # The failing device must not have stopped the devices after it.
    assert calls.get(light_b.device_identifier, 0) >= 1, (
        "a failing device read aborted the resync loop before later devices"
    )
    print("PASS BUG-1: resync re-read every registered device; "
          f"failure tolerated (calls={calls}, connected={module.connected})")


async def test_bug2_read_status():
    """Light/Switch read_status sends a real 0xE0 status request."""
    for cls, address in ((Light, (1, 20)), (Switch, (1, 21))):
        hdl = FakeHdl()
        device = cls(hdl, address, 1, "x")
        try:
            await device.read_status()
        except NotImplementedError as err:
            raise AssertionError(
                f"BUG-2: {cls.__name__}.read_status() raised "
                f"NotImplementedError: {err!r}"
            )
        sent = hdl.network_interface.sent
        assert sent, f"{cls.__name__}.read_status() sent no telegram"
        op = sent[-1].operate_code
        assert op == OperateCode.ReadStatusOfChannels, (
            f"{cls.__name__}.read_status() sent op={op!r}, "
            f"expected {OperateCode.ReadStatusOfChannels!r}"
        )
        print(f"PASS BUG-2: {cls.__name__}.read_status() -> "
              f"ReadStatusOfChannels telegram for {address}")


async def main():
    failures = []
    for name, coro in (
        ("BUG-1 reconnect resync", test_bug1_reconnect_resync()),
        ("BUG-2 read_status", test_bug2_read_status()),
    ):
        try:
            await coro
        except AssertionError as err:
            failures.append((name, err))
            print(f"FAIL {name}: {err}")
        except Exception as err:  # noqa: BLE001
            failures.append((name, err))
            print(f"ERROR {name}: {type(err).__name__}: {err}")
    if failures:
        print(f"\n{len(failures)} check(s) failed")
        return 1
    print("\nAll reconnect/resync checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
