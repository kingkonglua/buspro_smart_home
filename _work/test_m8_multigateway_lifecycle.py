"""M-8 regression tests: module-level service registration + multi gateway.

Covers the acceptance criteria that the pre-existing ``test_fix_bugs.py`` M-8
block does not:

  1. ``async_setup`` -> ``async_setup_entry`` -> ``async_unload_entry`` ->
     ``async_setup_entry`` twice, without raising or double-registering.
  2. Two config entries set up at the same time.

Run:  python3 _work/test_m8_multigateway_lifecycle.py

This test is intentionally separate from ``_work/test_fix_bugs.py`` so the
existing regression suite is left untouched.
"""
import asyncio
import logging
import os
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(HERE, "ha_stub"))
sys.path.insert(0, os.path.join(HERE, "pylibs"))
sys.path.insert(0, ROOT)

import custom_components.buspro as bp  # noqa: E402
from custom_components.buspro import (  # noqa: E402
    DOMAIN,
    SERVICE_BUSPRO_ACTIVATE_SCENE,
    SERVICE_BUSPRO_SEND_MESSAGE,
    SERVICE_BUSPRO_UNIVERSAL_SWITCH,
)

logging.basicConfig(level=logging.CRITICAL)

ALL_SERVICES = (
    SERVICE_BUSPRO_ACTIVATE_SCENE,
    SERVICE_BUSPRO_SEND_MESSAGE,
    SERVICE_BUSPRO_UNIVERSAL_SWITCH,
)


class StrictServices:
    """Service registry that raises on duplicate registration."""

    def __init__(self):
        self._services = {}
        self.register_calls = []

    def has_service(self, domain, service):
        return (domain, service) in self._services

    def async_register(self, domain, service, handler, schema=None):
        if (domain, service) in self._services:
            raise ValueError(
                f"service {domain}.{service} already registered"
            )
        self._services[(domain, service)] = handler
        self.register_calls.append((domain, service))

    def async_remove(self, domain, service):
        self._services.pop((domain, service), None)


class FakeEntry:
    def __init__(self, entry_id, host="10.0.0.1", port=6000):
        self.entry_id = entry_id
        self.data = {"host": host, "port": port}
        self.options = {}
        self._unload_callbacks = []

    def async_on_unload(self, cb):
        self._unload_callbacks.append(cb)

    def add_update_listener(self, cb):
        return cb


class FakeConfigEntries:
    async def async_forward_entry_setups(self, entry, platforms):
        return True

    async def async_unload_platforms(self, entry, platforms):
        return True

    async def async_reload(self, entry_id):
        return True


class FakeHass:
    def __init__(self):
        self.data = {}
        self.services = StrictServices()
        self.config_entries = FakeConfigEntries()


class FakeModule:
    """Stand-in for BusproModule; never touches the network."""

    instances = []

    def __init__(self, hass, host, port):
        self.hass = hass
        self.host = host
        self.port = port
        self.connected = False
        self.hdl = None
        self.start_calls = 0
        self.stop_calls = 0
        FakeModule.instances.append(self)

    async def start(self):
        self.start_calls += 1
        self.connected = True

    def register_services(self):
        # Delegate to the real module-level registration path.
        bp._register_services(self.hass)

    async def stop(self, event=None):
        self.stop_calls += 1
        self.connected = False


class M8LifecycleTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        FakeModule.instances = []

    async def test_setup_unload_setup_twice_no_duplicate(self):
        """Two full rounds must not raise or double-register services."""
        hass = FakeHass()
        with mock.patch.object(bp, "BusproModule", FakeModule):
            await bp.async_setup(hass, {})

            # ---- round 1 -------------------------------------------------
            entry = FakeEntry("entry-1")
            await bp.async_setup_entry(hass, entry)
            self.assertEqual(
                len(hass.services.register_calls), 3,
                "first entry must register exactly the three services",
            )
            for service in ALL_SERVICES:
                self.assertTrue(hass.services.has_service(DOMAIN, service))

            unload_ok = await bp.async_unload_entry(hass, entry)
            self.assertTrue(unload_ok)
            self.assertEqual(
                FakeModule.instances[-1].stop_calls, 1,
                "unload must stop the entry's module",
            )
            for service in ALL_SERVICES:
                self.assertFalse(
                    hass.services.has_service(DOMAIN, service),
                    "services must be dropped once the last entry is gone",
                )

            # ---- round 2 (this is where the old per-entry code blew up) ---
            entry2 = FakeEntry("entry-1", host="10.0.0.9")
            await bp.async_setup_entry(hass, entry2)
            self.assertEqual(
                len(hass.services.register_calls), 6,
                "second round must register exactly once again, not twice",
            )
            # Each service is registered once per round (the strict registry
            # already rejects any concurrent double registration).
            for service in ALL_SERVICES:
                self.assertEqual(
                    hass.services.register_calls.count((DOMAIN, service)), 2,
                    "each service registered once per round",
                )
            for service in ALL_SERVICES:
                self.assertTrue(hass.services.has_service(DOMAIN, service))

            # A second entry in the same round must not re-register.
            entry3 = FakeEntry("entry-3", host="10.0.0.10")
            await bp.async_setup_entry(hass, entry3)
            self.assertEqual(len(hass.services.register_calls), 6)

    async def test_two_entries_setup_at_once(self):
        """Two gateways loaded together: both stored, services registered once."""
        hass = FakeHass()
        e1 = FakeEntry("entry-1", host="10.0.0.1")
        e2 = FakeEntry("entry-2", host="10.0.0.2")

        with mock.patch.object(bp, "BusproModule", FakeModule):
            await bp.async_setup(hass, {})
            await asyncio.gather(
                bp.async_setup_entry(hass, e1),
                bp.async_setup_entry(hass, e2),
            )

        bucket = hass.data[DOMAIN]
        self.assertEqual(set(bucket), {"entry-1", "entry-2"},
                         "both gateways must be kept, keyed by entry id")
        self.assertEqual(len(hass.services.register_calls), 3,
                         "services are global and must be registered once")

        # Unloading one gateway keeps the other gateway and its services.
        with mock.patch.object(bp, "BusproModule", FakeModule):
            await bp.async_unload_entry(hass, e1)
        self.assertEqual(set(hass.data[DOMAIN]), {"entry-2"})
        for service in ALL_SERVICES:
            self.assertTrue(
                hass.services.has_service(DOMAIN, service),
                "services must survive while another entry is loaded",
            )

    async def test_async_setup_is_idempotent(self):
        """Calling async_setup more than once must not duplicate services."""
        hass = FakeHass()
        await bp.async_setup(hass, {})
        await bp.async_setup(hass, {})
        self.assertEqual(len(hass.services.register_calls), 3)
        self.assertIsInstance(hass.data[DOMAIN], bp.BusproData)


if __name__ == "__main__":
    unittest.main(verbosity=2)
