"""Verification test for R7 (diagnostics IP redaction) and R5 (unique_id scoping).

Run:  python3 _work/test_r7_r5_verify.py

R7  diagnostics must not export any plaintext internal IP (allowed/dropped
    source-IP lists, the advertised/peer address, or the entry unique_id which
    is ``host:port``).  ``async_redact_data`` only blanks whole dict keys and
    never rewrites a list's members, so the source-IP lists are redacted
    element-wise while keeping their sequence shape.

R5  entity ``unique_id`` must include the owning gateway's config-entry id so
    two gateways with the same device (subnet, device, channel) do not collide
    in HA's entity registry, and so a reload of one gateway is idempotent.

The script uses the hand-written ``_work/ha_stub`` (no real Home Assistant
installed) and mocks only the pybuspro background reads; every platform
``async_setup_entry`` and ``diagnostics.async_get_config_entry_diagnostics``
is the real code path.
"""

import asyncio
import logging
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(HERE, "ha_stub"))
sys.path.insert(0, os.path.join(HERE, "pylibs"))
sys.path.insert(0, ROOT)

import custom_components.buspro as bp  # noqa: E402
from custom_components.buspro import BusproData, DOMAIN  # noqa: E402
from custom_components.buspro import (  # noqa: E402
    binary_sensor as binary_sensor_mod,
    button as button_mod,
    climate as climate_mod,
    cover as cover_mod,
    diagnostics as diagnostics_mod,
    light as light_mod,
    scene as scene_mod,
    sensor as sensor_mod,
    switch as switch_mod,
)
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

logging.basicConfig(level=logging.CRITICAL)

# Keep pybuspro's fire-and-forget status reads out of the test.
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

IPV4_RE = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")

FAILURES = []


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"{status}: {name}{'' if cond else '  ' + detail}")
    if not cond:
        FAILURES.append(name)


# --------------------------------------------------------------------------- #
# mocks
# --------------------------------------------------------------------------- #
class FakeNetworkInterface:
    def __init__(self):
        self.sent = []

    async def send_telegram(self, telegram):
        self.sent.append(telegram)


class FakeBus:
    """Just enough pybuspro bus for the device constructors + diagnostics."""

    def __init__(self, advertised_ip=None):
        self.logger = logging.getLogger("test.bus")
        self.network_interface = FakeNetworkInterface()
        self.loop = None
        self.advertised_ip = advertised_ip
        # The internal source-IP lists R7 is about.
        self.allowed_source_ips = {"192.168.1.15", "10.0.0.7"}
        self.dropped_source_ips = {"192.168.1.99"}
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

    def __init__(self, entry_id, connected, host="192.168.1.15"):
        self.entry_id = entry_id
        self.connected = connected
        self.hdl = FakeBus(advertised_ip=host)
        self.gateway_address_send_receive = ((host, 6000), ("", 6000))

    async def start(self):
        self.connected = True

    async def stop(self, event=None):
        self.connected = False


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
    def __init__(self, entry_id, devices, host="192.168.1.15"):
        self.entry_id = entry_id
        self.data = {"host": host, "port": 6000}
        self.options = {"devices": devices}
        self.title = entry_id
        self.version = 1
        # Real integration stores f"{host}:{port}" here, i.e. the gateway IP.
        self.unique_id = f"{host}:{6000}"
        self._unload_callbacks = []

    def async_on_unload(self, cb):
        self._unload_callbacks.append(cb)

    def add_update_listener(self, cb):
        return cb


def all_devices():
    return {
        "light_1_10_1": {
            CONF_DEVICE_TYPE: DEVICE_TYPE_LIGHT,
            CONF_SUBNET_ID: 1, CONF_DEVICE_ID: 10, CONF_CHANNEL: 1, "name": "L1",
        },
        "switch_1_11_1": {
            CONF_DEVICE_TYPE: DEVICE_TYPE_SWITCH,
            CONF_SUBNET_ID: 1, CONF_DEVICE_ID: 11, CONF_CHANNEL: 1, "name": "S1",
        },
        "binary_sensor_1_12_1_motion": {
            CONF_DEVICE_TYPE: DEVICE_TYPE_BINARY_SENSOR,
            CONF_SUBNET_ID: 1, CONF_DEVICE_ID: 12, CONF_CHANNEL: 1,
            CONF_SUBTYPE: "motion", "name": "BS1",
        },
        "sensor_1_13_temperature": {
            CONF_DEVICE_TYPE: DEVICE_TYPE_SENSOR,
            CONF_SUBNET_ID: 1, CONF_DEVICE_ID: 13,
            CONF_SUBTYPE: "temperature", "name": "T1",
        },
        "cover_1_14_1_bus_motor": {
            CONF_DEVICE_TYPE: DEVICE_TYPE_COVER,
            CONF_SUBNET_ID: 1, CONF_DEVICE_ID: 14, CONF_CHANNEL: 1,
            CONF_SUBTYPE: "bus_motor", CONF_TRAVEL_TIME: 10, "name": "C1",
        },
        "button_1_15_3": {
            CONF_DEVICE_TYPE: DEVICE_TYPE_BUTTON,
            CONF_SUBNET_ID: 1, CONF_DEVICE_ID: 15, CONF_CHANNEL: 3, "name": "B1",
        },
        "scene_1_16_1_1": {
            CONF_DEVICE_TYPE: DEVICE_TYPE_SCENE,
            CONF_SUBNET_ID: 1, CONF_DEVICE_ID: 16,
            CONF_AREA_NUMBER: 1, CONF_SCENE_NUMBER: 1, "name": "SC1",
        },
        "climate_1_17_floor_heating_1": {
            CONF_DEVICE_TYPE: DEVICE_TYPE_CLIMATE,
            CONF_SUBNET_ID: 1, CONF_DEVICE_ID: 17,
            CONF_SUBTYPE: "floor_heating", CONF_AC_NUMBER: 1, "name": "FH1",
        },
        "climate_1_18_ac_1": {
            CONF_DEVICE_TYPE: DEVICE_TYPE_CLIMATE,
            CONF_SUBNET_ID: 1, CONF_DEVICE_ID: 18,
            CONF_SUBTYPE: "ac", CONF_AC_NUMBER: 1, "name": "AC1",
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
    entities = []

    def add(ents):
        entities.extend(ents)

    for setup in PLATFORM_SETUPS:
        await setup(hass, entry, add)
    return entities


def entity_uid(entity):
    uid = getattr(entity, "unique_id", None)
    if uid is None:
        uid = getattr(entity, "_attr_unique_id", None)
    return uid


# --------------------------------------------------------------------------- #
# helpers for R7 assertions
# --------------------------------------------------------------------------- #
def find_key(node, key):
    """Collect every value stored under ``key`` anywhere in ``node``."""
    found = []
    if isinstance(node, dict):
        for k, v in node.items():
            if k == key:
                found.append(v)
            found.extend(find_key(v, key))
    elif isinstance(node, (list, tuple)):
        for item in node:
            found.extend(find_key(item, key))
    return found


def contains_ip(node):
    if isinstance(node, str):
        return bool(IPV4_RE.search(node))
    if isinstance(node, dict):
        return any(contains_ip(v) for v in node.values())
    if isinstance(node, (list, tuple, set, frozenset)):
        return any(contains_ip(v) for v in node)
    return False


async def main():
    print("== R7: diagnostics must not leak internal IPs ==")
    hass = FakeHass()
    hass.data[DOMAIN] = BusproData()
    module_a = FakeModule("entry-A", connected=True, host="192.168.1.15")
    module_b = FakeModule("entry-B", connected=False, host="10.0.0.8")
    hass.data[DOMAIN]["entry-A"] = module_a
    hass.data[DOMAIN]["entry-B"] = module_b

    entry_a = FakeEntry("entry-A", all_devices(), host="192.168.1.15")
    diag = await diagnostics_mod.async_get_config_entry_diagnostics(hass, entry_a)

    allowed_vals = find_key(diag, "allowed_source_ips")
    dropped_vals = find_key(diag, "dropped_source_ips")

    check(
        "diagnostics_redacts_allowed_source_ips",
        bool(allowed_vals) and not contains_ip(diag),
        f"allowed={allowed_vals}",
    )
    check(
        "diagnostics_redacts_dropped_source_ips",
        bool(dropped_vals)
        and all(not contains_ip(v) for v in dropped_vals),
        f"dropped={dropped_vals}",
    )
    check(
        "diagnostics_redacts_dropped_source_ips_is_sequence",
        bool(dropped_vals)
        and all(isinstance(v, (list, tuple)) for v in dropped_vals),
        f"dropped={dropped_vals}",
    )

    print("== R5: unique_id is gateway-scoped ==")
    hass5 = FakeHass()
    hass5.data[DOMAIN] = BusproData()
    mod_a = FakeModule("entry-A", connected=True, host="10.0.0.1")
    mod_b = FakeModule("entry-B", connected=True, host="10.0.0.2")
    hass5.data[DOMAIN]["entry-A"] = mod_a
    hass5.data[DOMAIN]["entry-B"] = mod_b
    ent_a = FakeEntry("entry-A", all_devices(), host="10.0.0.1")
    ent_b = FakeEntry("entry-B", all_devices(), host="10.0.0.2")

    entities_a = await setup_platforms(hass5, ent_a)
    entities_b = await setup_platforms(hass5, ent_b)
    entities_a2 = await setup_platforms(hass5, ent_a)

    uids_a = [entity_uid(e) for e in entities_a]
    uids_b = [entity_uid(e) for e in entities_b]
    uids_a2 = [entity_uid(e) for e in entities_a2]

    pairing_ok = (
        len(entities_a) == len(entities_b) == len(entities_a2) > 0
        and all(type(x) is type(y) for x, y in zip(entities_a, entities_b))
    )
    distinct = pairing_ok and all(
        ua is not None and ub is not None and ua != ub
        for ua, ub in zip(uids_a, uids_b)
    )
    check(
        "unique_id_differs_across_gateways",
        distinct,
        f"A={sorted(u for u in uids_a if u)} B={sorted(u for u in uids_b if u)}",
    )
    stable = pairing_ok and all(
        ua is not None and ua2 is not None and ua == ua2
        for ua, ua2 in zip(uids_a, uids_a2)
    )
    check(
        "unique_id_stable_within_one_gateway",
        stable,
        f"A={sorted(u for u in uids_a if u)} A2={sorted(u for u in uids_a2 if u)}",
    )

    print()
    if FAILURES:
        print(f"RESULT: {len(FAILURES)} FAILURE(S): {', '.join(FAILURES)}")
        return 1
    print("RESULT: ALL_PASS")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
