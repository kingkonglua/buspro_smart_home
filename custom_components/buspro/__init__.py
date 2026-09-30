"""
Support for Buspro devices.

For more details about this component, please refer to the documentation at
https://home-assistant.io/...
"""

import logging

import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from homeassistant.const import (
    CONF_HOST,
    CONF_PORT,
)
from homeassistant.const import (
    EVENT_HOMEASSISTANT_STOP,
)
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry

from .const import (
    DOMAIN,
    CONF_DEVICES,
)

_LOGGER = logging.getLogger(__name__)

DATA_BUSPRO = "buspro"
DEPENDENCIES = []


class BusproData(dict):
    """Per-entry BusproModule registry.

    Stored under ``hass.data[DOMAIN]`` keyed by config-entry id so that more
    than one gateway can coexist. Attribute access is proxied to the first
    registered module for backward compatibility with the older single-entry
    access pattern (``hass.data[DOMAIN].connected`` etc.).
    """

    def __getattr__(self, item):
        for module in self.values():
            return getattr(module, item)
        raise AttributeError(item)


def get_buspro_module(hass, entry_id=None):
    """Return the BusproModule for entry_id, or the first known one."""
    bucket = hass.data.get(DOMAIN)
    if isinstance(bucket, dict):
        if entry_id is not None:
            return bucket.get(entry_id)
        for module in bucket.values():
            return module
        return None
    return bucket

DEFAULT_SCENE_NAME = "BUSPRO SCENE"
DEFAULT_SEND_MESSAGE_NAME = "BUSPRO MESSAGE"

SERVICE_BUSPRO_SEND_MESSAGE = "send_message"
SERVICE_BUSPRO_ACTIVATE_SCENE = "activate_scene"
SERVICE_BUSPRO_UNIVERSAL_SWITCH = "set_universal_switch"

SERVICE_BUSPRO_ATTR_OPERATE_CODE = "operate_code"
SERVICE_BUSPRO_ATTR_ADDRESS = "address"
SERVICE_BUSPRO_ATTR_PAYLOAD = "payload"
SERVICE_BUSPRO_ATTR_SCENE_ADDRESS = "scene_address"
SERVICE_BUSPRO_ATTR_SWITCH_NUMBER = "switch_number"
SERVICE_BUSPRO_ATTR_STATUS = "status"

"""{ "address": [1,74], "scene_address": [3,5] }"""
SERVICE_BUSPRO_ACTIVATE_SCENE_SCHEMA = vol.Schema({
    vol.Required(SERVICE_BUSPRO_ATTR_ADDRESS): vol.Any([cv.positive_int]),
    vol.Required(SERVICE_BUSPRO_ATTR_SCENE_ADDRESS): vol.Any([cv.positive_int]),
})

"""{ "address": [1,74], "operate_code": [4,12], "payload": [1,75,0,3] }"""
SERVICE_BUSPRO_SEND_MESSAGE_SCHEMA = vol.Schema({
    vol.Required(SERVICE_BUSPRO_ATTR_ADDRESS): vol.Any([cv.positive_int]),
    vol.Required(SERVICE_BUSPRO_ATTR_OPERATE_CODE): vol.Any([cv.positive_int]),
    vol.Required(SERVICE_BUSPRO_ATTR_PAYLOAD): vol.Any([cv.positive_int]),
})

"""{ "address": [1,100], "switch_number": 100, "status": 1 }"""
SERVICE_BUSPRO_UNIVERSAL_SWITCH_SCHEMA = vol.Schema({
    vol.Required(SERVICE_BUSPRO_ATTR_ADDRESS): vol.Any([cv.positive_int]),
    vol.Required(SERVICE_BUSPRO_ATTR_SWITCH_NUMBER): vol.Any(cv.positive_int),
    vol.Required(SERVICE_BUSPRO_ATTR_STATUS): vol.Any(cv.positive_int),
})

PLATFORMS = ["light", "switch", "binary_sensor", "sensor", "climate", "cover", "button", "scene"]


def _get_first_gateway(hass: HomeAssistant):
    """Return any registered Buspro gateway (services are global today)."""
    return get_buspro_module(hass)


def _register_services(hass: HomeAssistant) -> None:
    """Register HDL Buspro services once, idempotently.

    The handlers resolve the gateway lazily from ``hass.data`` so this only
    has to run once even when several config entries (gateways) are loaded.
    ``has_service`` makes reloads/unload-setup cycles safe.
    """
    if hass.services.has_service(DOMAIN, SERVICE_BUSPRO_ACTIVATE_SCENE):
        return

    async def _activate_scene(call):
        """Service for activating a scene."""
        # noinspection PyUnresolvedReferences
        from .pybuspro.devices.scene import Scene

        buspro_module = _get_first_gateway(hass)
        if buspro_module is None:
            _LOGGER.error("No Buspro gateway available for %s",
                          SERVICE_BUSPRO_ACTIVATE_SCENE)
            return
        attr_address = call.data.get(SERVICE_BUSPRO_ATTR_ADDRESS)
        attr_scene_address = call.data.get(SERVICE_BUSPRO_ATTR_SCENE_ADDRESS)
        scene = Scene(buspro_module.hdl, attr_address, attr_scene_address,
                      DEFAULT_SCENE_NAME)
        await scene.run()

    async def _send_message(call):
        """Service for sending an arbitrary message."""
        # noinspection PyUnresolvedReferences
        from .pybuspro.devices.generic import Generic

        buspro_module = _get_first_gateway(hass)
        if buspro_module is None:
            _LOGGER.error("No Buspro gateway available for %s",
                          SERVICE_BUSPRO_SEND_MESSAGE)
            return
        attr_address = call.data.get(SERVICE_BUSPRO_ATTR_ADDRESS)
        attr_payload = call.data.get(SERVICE_BUSPRO_ATTR_PAYLOAD)
        attr_operate_code = call.data.get(SERVICE_BUSPRO_ATTR_OPERATE_CODE)
        generic = Generic(buspro_module.hdl, attr_address, attr_payload,
                          attr_operate_code, DEFAULT_SEND_MESSAGE_NAME)
        await generic.run()

    async def _set_universal_switch(call):
        """Service for setting a universal switch."""
        # noinspection PyUnresolvedReferences
        from .pybuspro.devices.universal_switch import UniversalSwitch

        buspro_module = _get_first_gateway(hass)
        if buspro_module is None:
            _LOGGER.error("No Buspro gateway available for %s",
                          SERVICE_BUSPRO_UNIVERSAL_SWITCH)
            return
        attr_address = call.data.get(SERVICE_BUSPRO_ATTR_ADDRESS)
        attr_switch_number = call.data.get(SERVICE_BUSPRO_ATTR_SWITCH_NUMBER)
        universal_switch = UniversalSwitch(buspro_module.hdl, attr_address,
                                           attr_switch_number)
        status = call.data.get(SERVICE_BUSPRO_ATTR_STATUS)
        if status == 1:
            await universal_switch.set_on()
        else:
            await universal_switch.set_off()

    hass.services.async_register(
        DOMAIN, SERVICE_BUSPRO_ACTIVATE_SCENE, _activate_scene,
        schema=SERVICE_BUSPRO_ACTIVATE_SCENE_SCHEMA)
    hass.services.async_register(
        DOMAIN, SERVICE_BUSPRO_SEND_MESSAGE, _send_message,
        schema=SERVICE_BUSPRO_SEND_MESSAGE_SCHEMA)
    hass.services.async_register(
        DOMAIN, SERVICE_BUSPRO_UNIVERSAL_SWITCH, _set_universal_switch,
        schema=SERVICE_BUSPRO_UNIVERSAL_SWITCH_SCHEMA)


async def async_setup(hass: HomeAssistant, config) -> bool:
    """Set up the Buspro component (once, before any config entry)."""
    if not isinstance(hass.data.get(DOMAIN), BusproData):
        hass.data[DOMAIN] = BusproData()
    _register_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Set up the Buspro component from a config entry."""
    host = config_entry.data.get(CONF_HOST, "")
    port = config_entry.data.get(CONF_PORT, 6000)  # BUGFIX: default port was 1, should be 6000

    # M-8: key modules by entry id so a second gateway does not replace the
    # first. Migrate a legacy single-module value if present.
    if not isinstance(hass.data.get(DOMAIN), BusproData):
        hass.data[DOMAIN] = BusproData()

    # M-8: services live at the integration level. Registering here as well as
    # in async_setup is harmless (guarded by has_service) and recovers the
    # services if an entry is re-added after the last one was unloaded.
    _register_services(hass)

    buspro_module = BusproModule(hass, host, port)
    await buspro_module.start()
    hass.data[DOMAIN][config_entry.entry_id] = buspro_module

    # Forward setup to all platforms
    await hass.config_entries.async_forward_entry_setups(config_entry, PLATFORMS)

    # Listen for options updates (device add/remove)
    config_entry.async_on_unload(
        config_entry.add_update_listener(_async_update_options)
    )

    return True


async def _async_update_options(hass: HomeAssistant, config_entry: ConfigEntry):
    """Handle options update — reload the integration."""
    await hass.config_entries.async_reload(config_entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Unload the Buspro config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(
        config_entry, PLATFORMS
    )

    if unload_ok:
        # M-8: remove only this entry's module; keep the others (and their
        # services) alive. Drop the services once the last entry is gone.
        bucket = hass.data.get(DOMAIN)
        if isinstance(bucket, dict):
            buspro_module = bucket.pop(config_entry.entry_id, None)
        else:
            buspro_module = bucket
            hass.data.pop(DOMAIN, None)

        if buspro_module:
            await buspro_module.stop()

        if not hass.data.get(DOMAIN):
            for service in (
                SERVICE_BUSPRO_ACTIVATE_SCENE,
                SERVICE_BUSPRO_SEND_MESSAGE,
                SERVICE_BUSPRO_UNIVERSAL_SWITCH,
            ):
                if hass.services.has_service(DOMAIN, service):
                    hass.services.async_remove(DOMAIN, service)

    return unload_ok


class BusproModule:
    """Representation of Buspro Object."""

    def __init__(self, hass, host, port):
        """Initialize of Buspro module."""
        self.hass = hass
        self.connected = False
        self.hdl = None
        self.gateway_address_send_receive = ((host, port), ('', port))
        self.init_hdl()

    def init_hdl(self):
        """Initialize of Buspro object."""
        # noinspection PyUnresolvedReferences
        from .pybuspro.buspro import Buspro
        self.hdl = Buspro(self.gateway_address_send_receive, self.hass.loop)

    async def start(self):
        """Start Buspro object. Connect to tunneling device."""
        await self.hdl.start(state_updater=False)
        self.hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, self.stop)
        self.connected = True

    # noinspection PyUnusedLocal
    async def stop(self, event=None):
        """Stop Buspro object. Disconnect from tunneling device."""
        self.connected = False
        await self.hdl.stop()

    def register_services(self):
        """Register HDL Buspro services (module-level, idempotent)."""
        _register_services(self.hass)
