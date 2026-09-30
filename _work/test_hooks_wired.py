"""R3 regression: prove the three pybuspro hooks are actually wired.

``pybuspro`` reserves ``advertised_ip`` / ``allowed_source_ips`` /
``on_connection_lost`` and its comments say "populated by the integration's
setup path". Before the R3 fix nothing wrote them, so a real install advertises
192.168.1.15, filters no source IP and never learns the transport died.

This test drives the REAL ``async_setup`` -> ``async_setup_entry`` with the
``_work/ha_stub`` Home Assistant stand-in and only replaces the UDP transport,
then asserts each hook holds a usable value.

Run:  python3 _work/test_hooks_wired.py
"""
import asyncio
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(HERE, "ha_stub"))
sys.path.insert(0, os.path.join(HERE, "pylibs"))
sys.path.insert(0, ROOT)

import custom_components.buspro as bp  # noqa: E402
from custom_components.buspro import DOMAIN  # noqa: E402
from custom_components.buspro.pybuspro.transport.udp_client import (  # noqa: E402
    UDPClient,
)


async def _noop_connect(self):
    self.transport = None


async def _noop_stop(self):
    self.closing = True
    self.transport = None


UDPClient._connect = _noop_connect
UDPClient.stop = _noop_stop


class FakeBus:
    def __init__(self):
        self.listeners = []
        self.fired = []

    def async_listen_once(self, event, cb):
        entry = (event, cb)
        self.listeners.append(entry)

        def _unsub():
            if entry in self.listeners:
                self.listeners.remove(entry)

        return _unsub

    def async_fire(self, event, data=None):
        self.fired.append((event, data))


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
    def __init__(self, hass):
        self.hass = hass

    async def async_forward_entry_setups(self, entry, platforms):
        return True

    async def async_unload_platforms(self, entry, platforms):
        return True


class FakeHass:
    def __init__(self):
        self.data = {}
        self.loop = asyncio.get_event_loop()
        self.bus = FakeBus()
        self.services = FakeServices()
        self.config_entries = FakeConfigEntries(self)
        self.created_tasks = []

    def async_create_task(self, target, name=None, eager_start=False):
        task = asyncio.get_running_loop().create_task(target)
        self.created_tasks.append(task)
        return task


class FakeEntry:
    def __init__(self, entry_id, host="127.0.0.1", port=6000):
        self.entry_id = entry_id
        self.data = {"host": host, "port": port}
        self.options = {"devices": {}}
        self.title = "HDL Buspro"

    def async_on_unload(self, cb):
        return None

    def add_update_listener(self, cb):
        return cb


class HooksWiredTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.hass = FakeHass()
        self.entry = FakeEntry("hooks-entry")
        await bp.async_setup(self.hass, {})
        await bp.async_setup_entry(self.hass, self.entry)
        self.module = self.hass.data[DOMAIN][self.entry.entry_id]

    async def asyncTearDown(self):
        self.module._stop_requested = True
        await bp.async_unload_entry(self.hass, self.entry)
        for task in self.hass.created_tasks:
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

    async def test_allowed_source_ips_contains_gateway(self):
        allowed = self.module.hdl.allowed_source_ips
        self.assertIsInstance(allowed, set)
        self.assertTrue(allowed, "allowed_source_ips must not stay empty")
        self.assertIn("127.0.0.1", allowed)

    async def test_advertised_ip_is_local_route_address(self):
        ip = self.module.hdl.advertised_ip
        self.assertIsNotNone(ip, "advertised_ip must not fall back to legacy")
        self.assertNotEqual(ip, "0.0.0.0")
        self.assertEqual(ip, "127.0.0.1")

    async def test_on_connection_lost_is_wired(self):
        cb = self.module.hdl.on_connection_lost
        self.assertIsNotNone(cb, "on_connection_lost must be assigned")
        self.assertTrue(callable(cb))
        self.assertEqual(cb, self.module._handle_connection_lost)

    async def test_connection_lost_marks_disconnected_and_fires_event(self):
        self.assertTrue(self.module.connected)
        self.module.hdl.on_connection_lost()
        self.assertFalse(self.module.connected,
                         "connected must flip False on transport loss")
        events = [e for e, _ in self.hass.bus.fired]
        self.assertIn(bp.EVENT_BUSPRO_CONNECTION_LOST, events)
        self.assertTrue(self.module._reconnect_task is not None,
                        "a reconnect task must be scheduled")


if __name__ == "__main__":
    unittest.main(verbosity=2)
