"""
This component provides button support for Buspro.

Buttons are used for momentary (pulse) universal switch outputs, e.g. an IR
emitter that sends one IR code per press to control an air conditioner.

For more details about this platform, please refer to the documentation at
https://home-assistant.io/components/...
"""

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import DATA_BUSPRO
from .const import (
    DOMAIN,
    CONF_DEVICES,
    CONF_DEVICE_TYPE,
    CONF_SUBNET_ID,
    CONF_DEVICE_ID,
    CONF_CHANNEL,
    DEVICE_TYPE_BUTTON,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Buspro button devices from a config entry."""
    buspro_module = hass.data[DOMAIN]
    hdl = buspro_module.hdl
    devices = config_entry.options.get(CONF_DEVICES, {})
    entities = []

    for device_key, device_config in devices.items():
        if device_config[CONF_DEVICE_TYPE] != DEVICE_TYPE_BUTTON:
            continue

        subnet_id = device_config[CONF_SUBNET_ID]
        device_id = device_config[CONF_DEVICE_ID]
        switch_number = device_config[CONF_CHANNEL]
        name = device_config.get("name", f"Button {subnet_id}-{device_id}-{switch_number}")
        device_address = (subnet_id, device_id)

        _LOGGER.debug(
            "Adding button '%s' with address %s and switch number %s",
            name, device_address, switch_number
        )

        entities.append(
            BusproButton(
                hass, hdl, device_address, switch_number, name, device_key
            )
        )

    async_add_entities(entities)


# noinspection PyAbstractClass
class BusproButton(ButtonEntity):
    """Representation of a Buspro momentary universal switch button."""

    def __init__(self, hass, hdl, device_address, switch_number, name, unique_id):
        self._hass = hass
        self._hdl = hdl
        self._device_address = device_address
        self._switch_number = switch_number
        self._attr_name = name
        self._attr_unique_id = unique_id
        self._attr_should_poll = False

    @property
    def available(self):
        """Return True if entity is available."""
        return self._hass.data[DATA_BUSPRO].connected

    async def async_press(self):
        """Send one momentary pulse to the universal switch output."""
        # noinspection PyUnresolvedReferences
        from .pybuspro.devices.control import _UniversalSwitch
        # noinspection PyUnresolvedReferences
        from .pybuspro.helpers.enums import SwitchStatusOnOff

        _LOGGER.debug(
            "Pressing button '%s' (address %s, switch %s)",
            self.name, self._device_address, self._switch_number
        )

        us = _UniversalSwitch(self._hdl)
        us.subnet_id, us.device_id = self._device_address
        us.switch_number = self._switch_number
        us.switch_status = SwitchStatusOnOff.ON
        await us.send()
