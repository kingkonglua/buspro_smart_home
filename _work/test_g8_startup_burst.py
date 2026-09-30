#!/usr/bin/env python3
"""G8 regression: startup read burst / dropped first packet / missing ACK.

Reproduces the three G8 findings with a *fake* bus (no sockets, no HDL
traffic):

  A. Dropped first status request.  At startup a channel issues exactly one
     ``_ReadStatusOfChannels`` and forgets it.  If that single UDP packet is
     lost the entity is stuck at its default (off) forever.  Expected: the
     device retries (bounded), and stops the moment a real reading arrives.

  B. Startup thundering herd.  Every Light/Switch on the integration sleeps a
     fixed 3 s and then fires its read on the *same* tick, so N channels = N
     UDP packets in the same instant.  Expected: the first attempt is
     staggered with jitter so the requests do not all land in one timeslice.

  C. Control frame without ACK resend.  A channel command is sent once and
     forgotten; if the ``*Response`` never comes back HA and the load drift
     apart.  Expected: exactly one bounded resend when no ACK arrives, and no
     resend at all when the ACK does arrive (idempotent, not unconditional).

Run:  /tmp/hdl_venv/bin/python _work/test_g8_startup_burst.py ; echo "rc=$?"
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(HERE, "ha_stub"))
sys.path.insert(0, os.path.join(HERE, "pylibs"))
sys.path.insert(0, ROOT)

import custom_components.buspro as bp  # noqa: E402,F401  (ensures package import)
from custom_components.buspro.pybuspro.devices import Light, Switch  # noqa: E402
from custom_components.buspro.pybuspro.devices import device as device_mod  # noqa: E402
from custom_components.buspro.pybuspro.helpers.enums import OperateCode  # noqa: E402

logging.basicConfig(level=logging.CRITICAL)
for _name in ("buspro", "buspro.log", "g8.fake"):
    logging.getLogger(_name).setLevel(logging.CRITICAL)

# Neutralize nothing: the whole point is to exercise the base read path.  But we
# shrink the timing knobs so the test is fast.  On the pre-fix code these names
# do not exist / are ignored -- which is exactly why the pre-fix run fails.
_RETRY_CONSTANTS = {
    "_CHANNEL_STATUS_INITIAL_DELAY_SECONDS": 0.05,
    "_CHANNEL_STATUS_INITIAL_JITTER_SECONDS": 0.0,
    "_CHANNEL_STATUS_RETRY_BASE_SECONDS": 0.05,
    "_CHANNEL_STATUS_RETRY_MAX_SECONDS": 0.1,
    "_CHANNEL_STATUS_RETRY_JITTER_SECONDS": 0.0,
    "_CHANNEL_STATUS_MAX_SENDS": 4,
    "_ACK_TIMEOUT_SECONDS": 0.1,
    "_ACK_MAX_RESENDS": 1,
}


def configure_timing(**overrides):
    for key, value in _RETRY_CONSTANTS.items():
        setattr(device_mod, key, overrides.get(key, value))


class _Telegram:
    """Minimal stand-in for an inbound telegram."""

    def __init__(self, operate_code, payload, source):
        self.operate_code = operate_code
        self.payload = payload
        self.source_address = source
        self.target_address = None


class FakeNetworkInterface:
    """Records telegrams + their send instants; never touches a socket."""

    def __init__(self):
        self.sent = []
        self.sent_times = []
        self.on_send = None  # optional callback(telegram, ordinal) for injection

    async def send_telegram(self, telegram):
        self.sent.append(telegram)
        self.sent_times.append(time.monotonic())
        if self.on_send is not None:
            self.on_send(telegram, len(self.sent))


class FakeHdl:
    """Minimal stand-in for the pybuspro Buspro client."""

    def __init__(self):
        self.network_interface = FakeNetworkInterface()
        self.logger = logging.getLogger("g8.fake")
        self._telegram_received_cbs = []
        self.started = False

    def register_telegram_received_device_cb(self, cb, addr, postfix=None):
        self._telegram_received_cbs.append(
            {"callback": cb, "device_address": addr, "postfix": postfix}
        )

    def unregister_telegram_received_device_cb(self, cb, addr, postfix=None):
        try:
            self._telegram_received_cbs.remove(
                {"callback": cb, "device_address": addr, "postfix": postfix}
            )
        except ValueError:
            pass

    def deliver(self, telegram, source):
        """Push a telegram into the device callbacks, like the real dispatcher."""
        for entry in list(self._telegram_received_cbs):
            if tuple(entry["device_address"]) == tuple(source):
                entry["callback"](telegram)


# --------------------------------------------------------------------------- #
# Scenario A - dropped first request must be retried
# --------------------------------------------------------------------------- #
async def scenario_a_dropped_first_packet():
    configure_timing(_CHANNEL_STATUS_INITIAL_JITTER_SECONDS=0.0)
    hdl = FakeHdl()
    ni = hdl.network_interface
    light = Light(hdl, (1, 10), 1, "L-A")

    # Drop the first TWO ReadStatusOfChannels requests, answer only the 3rd.
    def _on_send(telegram, ordinal):
        if telegram.operate_code != OperateCode.ReadStatusOfChannels:
            return
        if ordinal == 3:
            hdl.deliver(
                _Telegram(OperateCode.ReadStatusOfChannelsResponse, [1, 100], (1, 10)),
                (1, 10),
            )

    ni.on_send = _on_send

    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        if getattr(light, "_got_initial_status", False):
            break
        await asyncio.sleep(0.01)
    await asyncio.sleep(0.05)

    sends = len(ni.sent)
    got = getattr(light, "_got_initial_status", False)
    print(f"[A] dropped first 2 reads -> sends={sends} got_status={got} "
          f"is_on={light.is_on} brightness={light.current_brightness}")
    assert sends >= 3, (
        "G8-A: dropped startup read was not retried "
        f"(only {sends} send(s); expected >= 3 after 2 lost packets)"
    )
    assert sends <= 8, f"G8-A: retry not bounded ({sends} sends)"
    assert got, "G8-A: device never confirmed a real status reading"
    assert light.is_on, "G8-A: state not applied after retry"
    print("PASS G8-A: lost startup reads retried (bounded) -> state recovered")


# --------------------------------------------------------------------------- #
# Scenario B - 20 devices must not fire in the same timeslice
# --------------------------------------------------------------------------- #
async def scenario_b_startup_thundering_herd():
    configure_timing(
        _CHANNEL_STATUS_INITIAL_DELAY_SECONDS=0.05,
        _CHANNEL_STATUS_INITIAL_JITTER_SECONDS=2.0,
        _CHANNEL_STATUS_RETRY_BASE_SECONDS=0.05,
        _CHANNEL_STATUS_RETRY_MAX_SECONDS=0.1,
        _CHANNEL_STATUS_MAX_SENDS=3,
    )
    random.seed(20261001)
    hdl = FakeHdl()
    ni = hdl.network_interface

    devices = [Light(hdl, (2, 50 + i), 1, f"L-{i}") for i in range(20)]
    addresses = {tuple(d._device_address) for d in devices}

    first_send = {}
    original = ni.send_telegram

    async def _recording_send(telegram):
        await original(telegram)
        target = tuple(telegram.target_address)
        if telegram.operate_code == OperateCode.ReadStatusOfChannels:
            if target in addresses and target not in first_send:
                first_send[target] = time.monotonic()

    ni.send_telegram = _recording_send

    deadline = time.monotonic() + 4.0
    while time.monotonic() < deadline and len(first_send) < len(addresses):
        await asyncio.sleep(0.01)

    missing = addresses - set(first_send)
    assert not missing, f"G8-B: devices never sent their startup read: {missing}"

    times = sorted(first_send.values())
    spread = times[-1] - times[0]
    print(f"[B] 20 startup reads -> spread={spread:.3f}s "
          f"(first={times[0] % 10:.3f} last={times[-1] % 10:.3f})")
    assert spread > 0.2, (
        "G8-B: startup reads fired as one thundering herd "
        f"(spread={spread:.3f}s, expected > 0.2 s of jitter)"
    )
    print("PASS G8-B: startup reads are jitter-staggered, not same-timeslice")

    for d in devices:
        d._got_initial_status = True
    await asyncio.sleep(0.2)


# --------------------------------------------------------------------------- #
# Scenario C - control frame ACK resend is bounded and ack-aware
# --------------------------------------------------------------------------- #
async def _count_control_sends(ni):
    return sum(
        1 for t in ni.sent if t.operate_code == OperateCode.SingleChannelControl
    )


async def scenario_c_control_ack_resend():
    configure_timing(_ACK_TIMEOUT_SECONDS=0.15)

    # C1: ACK lost -> exactly one resend (bounded, not unconditional loop).
    hdl = FakeHdl()
    ni = hdl.network_interface
    light = Light(hdl, (1, 30), 1, "L-C")
    await light.set_on()
    before = await _count_control_sends(ni)
    await asyncio.sleep(0.5)  # well past _ACK_TIMEOUT_SECONDS
    after = await _count_control_sends(ni)
    print(f"[C1] command sent, ACK dropped -> control frames {before} -> {after}")
    assert before == 1, f"G8-C1: expected 1 initial control frame, got {before}"
    assert after == 2, (
        "G8-C1: missing ACK did not trigger exactly one resend "
        f"(got {after - before} resend(s); expected 1)"
    )

    # C2: ACK arrives -> no resend.
    hdl2 = FakeHdl()
    ni2 = hdl2.network_interface
    light2 = Light(hdl2, (1, 31), 1, "L-C2")
    await light2.set_on()
    hdl2.deliver(
        _Telegram(OperateCode.SingleChannelControlResponse, [1, 1, 100], (1, 31)),
        (1, 31),
    )
    await asyncio.sleep(0.5)
    total = await _count_control_sends(ni2)
    print(f"[C2] command sent, ACK received -> total control frames={total}")
    assert total == 1, (
        "G8-C2: a command that was acknowledged was resent "
        f"({total} frames; expected 1 -- no duplicate load action)"
    )
    print("PASS G8-C: control ACK resend is bounded and only on lost ACK")


async def main():
    failures = []
    scenarios = (
        ("G8-A dropped first packet", scenario_a_dropped_first_packet()),
        ("G8-B startup thundering herd", scenario_b_startup_thundering_herd()),
        ("G8-C control ACK resend", scenario_c_control_ack_resend()),
    )
    for name, coro in scenarios:
        try:
            await coro
        except AssertionError as err:
            failures.append((name, err))
            print(f"FAIL {name}: {err}")
        except Exception as err:  # noqa: BLE001
            failures.append((name, err))
            print(f"ERROR {name}: {type(err).__name__}: {err}")
    if failures:
        print(f"\n{len(failures)} G8 check(s) failed")
        return 1
    print("\nAll G8 startup-burst checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
