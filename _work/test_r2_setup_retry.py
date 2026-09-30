"""R2 regression: async_setup_entry transient-error retry + module teardown.

Runs the REAL ``custom_components.buspro.async_setup_entry`` against the offline
HA stub. Only ``BusproModule`` and the platform forwarder are replaced with
light doubles so we can inject the failure the real machine hits on first boot:

  * ``start()`` raising ``OSError`` (gateway not up yet / port in use)
  * platform ``async_setup_entry`` raising ``OSError`` after the socket opened

Required behaviour under test:
  1. transient start() error -> ConfigEntryNotReady + module.stop() + no
     residue in ``hass.data[DOMAIN]``
  2. transient platform-setup error -> same rollback (socket reclaimed)
  3. on_unload always registers ``module.stop`` once start succeeded
  4. a non-transient error (ValueError) is NOT masked as ConfigEntryNotReady

Run:  python3 _work/test_r2_setup_retry.py
"""
import asyncio
import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(HERE, "ha_stub"))
sys.path.insert(0, os.path.join(HERE, "pylibs"))
sys.path.insert(0, ROOT)

import custom_components.buspro as bp  # noqa: E402
from custom_components.buspro import DOMAIN  # noqa: E402
from homeassistant.exceptions import ConfigEntryNotReady  # noqa: E402


# --------------------------------------------------------------------------- #
# Light HA doubles
# --------------------------------------------------------------------------- #
class FakeBus:
    def async_listen_once(self, event, cb):
        return lambda: None

    def async_fire(self, *args, **kwargs):
        pass


class FakeServices:
    def __init__(self):
        self._services = {}

    def has_service(self, domain, service):
        return (domain, service) in self._services

    def async_register(self, domain, service, handler, schema=None):
        self._services[(domain, service)] = handler

    def async_remove(self, domain, service):
        self._services.pop((domain, service), None)


class FakeConfigEntries:
    def __init__(self):
        self.forward_error = None
        self.forward_count = 0

    async def async_forward_entry_setups(self, entry, platforms):
        if self.forward_error is not None:
            raise self.forward_error
        self.forward_count += 1
        return True


class FakeHass:
    def __init__(self):
        self.data = {}
        self.bus = FakeBus()
        self.services = FakeServices()
        self.config_entries = FakeConfigEntries()


class FakeEntry:
    def __init__(self, entry_id="entry-r2"):
        self.entry_id = entry_id
        self.data = {"host": "10.0.0.5", "port": 6000}
        self.options = {}
        self._unload_callbacks = []

    def async_on_unload(self, cb):
        self._unload_callbacks.append(cb)

    def add_update_listener(self, cb):
        return cb


class FakeModule:
    """Stand-in for BusproModule: records start/stop, injectable failures."""

    instances = []
    start_error = None

    def __init__(self, hass, host, port):
        self.hass = hass
        self.host = host
        self.port = port
        self.entry_id = None
        self.start_calls = 0
        self.stop_calls = 0
        type(self).instances.append(self)

    async def async_configure_hooks(self):
        pass

    async def start(self):
        self.start_calls += 1
        if type(self).start_error is not None:
            raise type(self).start_error

    async def stop(self, event=None):
        self.stop_calls += 1


# async_setup_entry looks BusproModule up in the module globals, so replacing it
# here is enough to keep the real setup code path but skip the UDP transport.
bp.BusproModule = FakeModule


# --------------------------------------------------------------------------- #
# Harness
# --------------------------------------------------------------------------- #
_FAILURES = []


def record(name, ok, detail=""):
    if ok:
        print(f"PASS: {name}")
    else:
        print(f"FAIL: {name}")
        if detail:
            print(f"       {detail}")
        _FAILURES.append(name)


def _reset():
    FakeModule.instances.clear()
    FakeModule.start_error = None


async def _run_setup(entry=None, forward_error=None, start_error=None):
    _reset()
    FakeModule.start_error = start_error
    hass = FakeHass()
    entry = entry or FakeEntry()
    hass.config_entries.forward_error = forward_error
    raised = None
    try:
        await bp.async_setup_entry(hass, entry)
    except BaseException as exc:  # noqa: BLE001
        raised = exc
    module = FakeModule.instances[-1] if FakeModule.instances else None
    return hass, entry, module, raised


async def case_transient_start_error():
    name = "setup_transient_error_raises_ConfigEntryNotReady"
    hass, entry, module, raised = await _run_setup(
        start_error=OSError("Address already in use")
    )
    if not isinstance(raised, ConfigEntryNotReady):
        record(name, False, f"expected ConfigEntryNotReady, got {raised!r}")
        return
    if module is None:
        record(name, False, "BusproModule was never constructed")
        return
    if module.stop_calls < 1:
        record(name, False, "module.stop() was not called after start() failed")
        return
    residue = hass.data.get(DOMAIN)
    if residue:
        record(name, False, f"hass.data[{DOMAIN!r}] not empty: {residue!r}")
        return
    record(name, True)


async def case_platform_forward_failure():
    name = "module_stopped_on_platform_setup_failure"
    hass, entry, module, raised = await _run_setup(
        forward_error=OSError("network unreachable during platform setup")
    )
    if not isinstance(raised, ConfigEntryNotReady):
        record(name, False, f"expected ConfigEntryNotReady, got {raised!r}")
        return
    if module is None or module.stop_calls < 1:
        record(name, False, "module.stop() was not called after platform failure")
        return
    residue = hass.data.get(DOMAIN)
    if residue and entry.entry_id in residue:
        record(name, False, f"entry still in hass.data[{DOMAIN!r}]: {residue!r}")
        return
    record(name, True)


async def case_on_unload_registered():
    name = "module_registered_in_on_unload"
    hass, entry, module, raised = await _run_setup()
    if raised is not None:
        record(name, False, f"setup unexpectedly raised {raised!r}")
        return
    if module is None:
        record(name, False, "BusproModule was never constructed")
        return
    if not any(cb == module.stop for cb in entry._unload_callbacks):
        registered = [getattr(cb, "__name__", repr(cb)) for cb in entry._unload_callbacks]
        record(name, False, f"module.stop not registered; callbacks={registered}")
        return
    record(name, True)


async def case_non_transient_propagates():
    name = "non_transient_error_propagates"
    hass, entry, module, raised = await _run_setup(
        forward_error=ValueError("programming error, not a transient fault")
    )
    if isinstance(raised, ConfigEntryNotReady):
        record(name, False, "ValueError was wrongly converted to ConfigEntryNotReady")
        return
    if not isinstance(raised, ValueError):
        record(name, False, f"expected ValueError to propagate, got {raised!r}")
        return
    record(name, True)


async def main():
    for case in (
        case_transient_start_error,
        case_platform_forward_failure,
        case_on_unload_registered,
        case_non_transient_propagates,
    ):
        try:
            await case()
        except Exception:  # noqa: BLE001
            name = case.__name__.replace("case_", "")
            print(f"FAIL: {name}")
            traceback.print_exc()
            _FAILURES.append(name)

    print()
    if _FAILURES:
        print("RESULT: FAILED")
        return 1
    print("RESULT: ALL_PASS")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
