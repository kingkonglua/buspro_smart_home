"""BUG-C2 regression test: per-gateway module wiring.

M-8 changed ``hass.data[DOMAIN]`` into a ``BusproData`` dict keyed by
``config_entry.entry_id``.  Several entities still looked up the module through
``hass.data[DOMAIN]`` (which proxies ``__getattr__`` to the *first* module), so
with two gateways every entity followed the first gateway's connection state.
``config_flow._live_hdl`` and ``diagnostics`` had the same problem.

Run:  python3 _work/test_multi_gateway.py

The test drives the real platform ``async_setup_entry`` code against two fake
gateways whose ``.connected`` differs, then checks each entity follows *its
own* gateway.  It fails on the pre-fix tree and passes after the fix.
"""

import asyncio
import logging
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(HERE, "ha_stub"))
sys.path.insert(0, os.path.join(HERE, "pylibs"))
sys.path.insert(0, ROOT)

import custom_components.buspro as bp  # noqa: E402
from custom_components.buspro import BusproData, DOMAIN  # noqa: E402
from custom_components.buspro import binary_sensor as binary_sensor_mod  # noqa: E402
from custom_components.buspro import button as button_mod  # noqa: E402
from custom_components.buspro import climate as climate_mod  # noqa: E402
from custom_components.buspro import cover as cover_mod  # noqa: E402
from custom_components.buspro import diagnostics as diagnostics_mod  # noqa: E402
from custom_components.buspro import light as light_mod  # noqa: E402
from custom_components.buspro import scene as scene_mod  # noqa: E402
from custom_components.buspro import sensor as sensor_mod  # noqa: E402
from custom_components.buspro import switch as switch_mod  # noqa: E402
from custom_components.buspro.climate import (  # noqa: E402
    BusproACClimate,
    BusproPanelACClimate,
)
from custom_components.buspro.config_flow import BusproOptionsFlow  # noqa: E402
from custom_components.buspro.const import (  # noqa: E402
    CONF_AC_NUMBER,
    CONF_AREA_NUMBER,
    CONF_CHANNEL,
    CONF_DEVICE_ID,
    CONF_DEVICE_TYPE,
    CONF_SCENE_NUMBER,
    CONF_SUBNET_ID,
    CONF_SUBTYPE,
    CONF_TRAVEL_TIME,
    DEVICE_TYPE_BINARY_SENSOR,
    DEVICE_TYPE_BUTTON,
    DEVICE_TYPE_CLIMATE,
    DEVICE_TYPE_COVER,
    DEVICE_TYPE_LIGHT,
    DEVICE_TYPE_SCENE,
    DEVICE_TYPE_SENSOR,
    DEVICE_TYPE_SWITCH,
)
from custom_components.buspro.pybuspro.devices.ac import AC  # noqa: E402
from custom_components.buspro.pybuspro.devices.climate import Climate  # noqa: E402
from custom_components.buspro.pybuspro.devices.curtain import Curtain  # noqa: E402
from custom_components.buspro.pybuspro.devices.device import Device  # noqa: E402
from custom_components.buspro.pybuspro.devices.panel_ac import (  # noqa: E402
    PanelAirConditioner,
)
from custom_components.buspro.pybuspro.devices.sensor import Sensor  # noqa: E402
from custom_components.buspro.sensor import BusproSensor  # noqa: E402

logging.basicConfig(level=logging.CRITICAL)

# Keep the background "read status" tasks out of the test.
Device._call_read_current_status_of_channels = (
    lambda self, run_from_init=False: None
)
Sensor._call_read_current_status_of_sensor = (
    lambda self, run_from_init=False: None
)
AC._call_read_current_status = lambda self, run_from_init=False: None
Climate._call_read_current_heating_status = (
    lambda self, run_from_init=False: None
)
Curtain._call_read_current_status = lambda self, run_from_init=False: None
PanelAirConditioner._start_background_reads = lambda self: None


# --------------------------------------------------------------------------- #
# stand-ins
# --------------------------------------------------------------------------- #
class FakeNetworkInterface:
    def __init__(self):
        self.sent = []

    async def send_telegram(self, telegram):
        self.sent.append(telegram)


class FakeBus:
    """Just enough pybuspro bus for the device constructors."""

    def __init__(self, advertised_ip=None):
        self.logger = logging.getLogger("test.bus")
        self.network_interface = FakeNetworkInterface()
        self.loop = None
        self.advertised_ip = advertised_ip
        self.allowed_source_ips = set()
        self.dropped_source_ips = set()
        self.started = True
        self._telegram_received_cbs = []

    def register_telegram_received_device_cb(self, cb, device_address, postfix=None):
        self._telegram_received_cbs.append(cb)

    def unregister_telegram_received_device_cb(self, cb, device_address, postfix=None):
        try:
            self._telegram_received_cbs.remove(cb)
        except ValueError:
            pass


class FakeModule:
    """Stand-in for BusproModule (per gateway)."""

    def __init__(self, connected, host="10.0.0.1"):
        self.connected = connected
        self.hdl = FakeBus(advertised_ip=host)
        self.gateway_address_send_receive = ((host, 6000), ("", 6000))
        self.stop_calls = 0

    async def start(self):
        self.connected = True

    async def stop(self, event=None):
        self.connected = False
        self.stop_calls += 1

    def register_services(self):
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
    async def async_forward_entry_setups(self, entry, platforms):
        return True

    async def async_unload_platforms(self, entry, platforms):
        return True


class FakeHass:
    def __init__(self):
        self.data = {}
        self.services = FakeServices()
        self.config_entries = FakeConfigEntries()
        self.loop = asyncio.get_event_loop()


class FakeEntry:
    def __init__(self, entry_id, devices, host="10.0.0.1"):
        self.entry_id = entry_id
        self.data = {"host": host, "port": 6000}
        self.options = {"devices": devices}
        self.title = entry_id
        self.version = 1
        self.unique_id = host
        self._unload_callbacks = []

    def async_on_unload(self, cb):
        self._unload_callbacks.append(cb)

    def add_update_listener(self, cb):
        return cb


# --------------------------------------------------------------------------- #
# one device per entity flavour, so the test exercises every platform
# --------------------------------------------------------------------------- #
def _devices():
    return {
        "light_1_10_1": {
            CONF_DEVICE_TYPE: DEVICE_TYPE_LIGHT,
            CONF_SUBNET_ID: 1, CONF_DEVICE_ID: 10, CONF_CHANNEL: 1,
            "name": "L1",
        },
        "switch_1_11_1": {
            CONF_DEVICE_TYPE: DEVICE_TYPE_SWITCH,
            CONF_SUBNET_ID: 1, CONF_DEVICE_ID: 11, CONF_CHANNEL: 1,
            "name": "S1",
        },
        "binary_sensor_1_12_1_motion": {
            CONF_DEVICE_TYPE: DEVICE_TYPE_BINARY_SENSOR,
            CONF_SUBNET_ID: 1, CONF_DEVICE_ID: 12, CONF_CHANNEL: 1,
            CONF_SUBTYPE: "motion",
            "name": "BS1",
        },
        "sensor_1_13_temperature": {
            CONF_DEVICE_TYPE: DEVICE_TYPE_SENSOR,
            CONF_SUBNET_ID: 1, CONF_DEVICE_ID: 13,
            CONF_SUBTYPE: "temperature",
            "name": "T1",
        },
        "cover_1_14_1_bus_motor": {
            CONF_DEVICE_TYPE: DEVICE_TYPE_COVER,
            CONF_SUBNET_ID: 1, CONF_DEVICE_ID: 14, CONF_CHANNEL: 1,
            CONF_SUBTYPE: "bus_motor", CONF_TRAVEL_TIME: 10,
            "name": "C1",
        },
        "button_1_15_3": {
            CONF_DEVICE_TYPE: DEVICE_TYPE_BUTTON,
            CONF_SUBNET_ID: 1, CONF_DEVICE_ID: 15, CONF_CHANNEL: 3,
            "name": "B1",
        },
        "scene_1_16_1_1": {
            CONF_DEVICE_TYPE: DEVICE_TYPE_SCENE,
            CONF_SUBNET_ID: 1, CONF_DEVICE_ID: 16,
            CONF_AREA_NUMBER: 1, CONF_SCENE_NUMBER: 1,
            "name": "SC1",
        },
        "climate_1_17_floor_heating_1": {
            CONF_DEVICE_TYPE: DEVICE_TYPE_CLIMATE,
            CONF_SUBNET_ID: 1, CONF_DEVICE_ID: 17,
            CONF_SUBTYPE: "floor_heating", CONF_AC_NUMBER: 1,
            "name": "FH1",
        },
        "climate_1_18_ac_1": {
            CONF_DEVICE_TYPE: DEVICE_TYPE_CLIMATE,
            CONF_SUBNET_ID: 1, CONF_DEVICE_ID: 18,
            CONF_SUBTYPE: "ac", CONF_AC_NUMBER: 1,
            "name": "AC1",
        },
        "climate_1_19_ac_panel_1": {
            CONF_DEVICE_TYPE: DEVICE_TYPE_CLIMATE,
            CONF_SUBNET_ID: 1, CONF_DEVICE_ID: 19,
            CONF_SUBTYPE: "ac_panel", CONF_AC_NUMBER: 1, CONF_CHANNEL: 1,
            "name": "PAC1",
        },
    }


PLATFORM_SETUPS = (
    light_mod.async_setup_entry,
    switch_mod.async_setup_entry,
    binary_sensor_mod.async_setup_entry,
    sensor_mod.async_setup_entry,
    cover_mod.async_setup_entry,
    button_mod.async_setup_entry,
    scene_mod.async_setup_entry,
    climate_mod.async_setup_entry,
)


async def setup_platforms(hass, entry):
    """Run every platform's async_setup_entry and return the entities."""
    entities = []

    def add(ents):
        entities.extend(ents)

    for setup in PLATFORM_SETUPS:
        await setup(hass, entry, add)
    return entities


def prime_devices(entities):
    """Make device-level ``available`` True so only the gateway state varies."""
    for entity in entities:
        if isinstance(entity, BusproSensor):
            entity._temperature = 20.0
        elif isinstance(entity, BusproACClimate):
            entity._device._available = True
        elif isinstance(entity, BusproPanelACClimate):
            entity._device._power = 1


class MultiGatewayTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.hass = FakeHass()
        self.module1 = FakeModule(connected=True, host="10.0.0.1")
        self.module2 = FakeModule(connected=False, host="10.0.0.2")
        self.hass.data[DOMAIN] = BusproData()
        self.hass.data[DOMAIN]["entry-1"] = self.module1
        self.hass.data[DOMAIN]["entry-2"] = self.module2

        self.entry1 = FakeEntry("entry-1", _devices(), host="10.0.0.1")
        self.entry2 = FakeEntry("entry-2", _devices(), host="10.0.0.2")

        self.entities1 = await setup_platforms(self.hass, self.entry1)
        self.entities2 = await setup_platforms(self.hass, self.entry2)
        prime_devices(self.entities1)
        prime_devices(self.entities2)

    def _assert_follows(self, entities, module, label):
        self.assertTrue(entities, f"{label} produced no entities")
        for entity in entities:
            self.assertEqual(
                entity.available,
                module.connected,
                f"{type(entity).__name__} on {label} must follow "
                f"{label}.connected={module.connected}, "
                f"got available={entity.available}",
            )

    async def test_each_gateway_follows_its_own_module(self):
        self._assert_follows(self.entities1, self.module1, "gateway1")
        self._assert_follows(self.entities2, self.module2, "gateway2")

    async def test_no_crosstalk_when_gateways_change_state(self):
        # gateway2 is down, gateway1 is up: gateway1 must stay available.
        self._assert_follows(self.entities1, self.module1, "gateway1")

        # Swap: gateway1 down, gateway2 up. Each side must track its own module
        # (pre-fix both sides proxied to the first module).
        self.module1.connected = False
        self.module2.connected = True
        self._assert_follows(self.entities1, self.module1, "gateway1")
        self._assert_follows(self.entities2, self.module2, "gateway2")

    async def test_unload_gateway2_leaves_gateway1_working(self):
        await bp.async_unload_entry(self.hass, self.entry2)
        self.assertEqual(self.module2.stop_calls, 1)
        self.assertNotIn("entry-2", self.hass.data[DOMAIN])
        self.assertIn("entry-1", self.hass.data[DOMAIN])
        self._assert_follows(self.entities1, self.module1, "gateway1")

    async def test_live_hdl_uses_its_own_entry(self):
        flow2 = BusproOptionsFlow()
        flow2.hass = self.hass
        flow2.config_entry = self.entry2
        self.assertIs(
            flow2._live_hdl(),
            self.module2.hdl,
            "OptionsFlow for entry-2 must return gateway2's hdl",
        )

        flow1 = BusproOptionsFlow()
        flow1.hass = self.hass
        flow1.config_entry = self.entry1
        self.assertIs(
            flow1._live_hdl(),
            self.module1.hdl,
            "OptionsFlow for entry-1 must return gateway1's hdl",
        )

    async def test_diagnostics_reports_every_gateway(self):
        diag = await diagnostics_mod.async_get_config_entry_diagnostics(
            self.hass, self.entry1
        )
        self.assertIn(
            "gateways",
            diag,
            f"diagnostics must expose per-gateway data, got keys={sorted(diag)}",
        )
        self.assertEqual(set(diag["gateways"]), {"entry-1", "entry-2"})
        self.assertTrue(diag["gateways"]["entry-1"]["connected"])
        self.assertFalse(diag["gateways"]["entry-2"]["connected"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
