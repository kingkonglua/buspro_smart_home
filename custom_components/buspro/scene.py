"""
Buspro scene platform.

An HDL "scene" is an (area_number, scene_number) pair sent to a device on the
bus; activating the scene runs the device's stored behaviour. This platform
exposes each configured scene as a Home Assistant scene entity, complementing
the existing ``buspro.activate_scene`` service.

For more details, see pybuspro/devices/scene.py.
"""

import logging

from homeassistant.components.scene import SceneEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DOMAIN,
    CONF_DEVICES,
    CONF_DEVICE_TYPE,
    CONF_SUBNET_ID,
    CONF_DEVICE_ID,
    CONF_AREA_NUMBER,
    CONF_SCENE_NUMBER,
    DEVICE_TYPE_SCENE,
    gateway_scoped_unique_id,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Buspro scene entities from a config entry."""
    # noinspection PyUnresolvedReferences
    from .pybuspro.devices.scene import Scene

    buspro_module = hass.data[DOMAIN][config_entry.entry_id]
    hdl = buspro_module.hdl
    devices = config_entry.options.get(CONF_DEVICES, {})
    entities = []

    for device_key, device_config in devices.items():
        if device_config.get(CONF_DEVICE_TYPE) != DEVICE_TYPE_SCENE:
            continue

        subnet_id = device_config[CONF_SUBNET_ID]
        device_id = device_config[CONF_DEVICE_ID]
        area_number = device_config.get(CONF_AREA_NUMBER, 0)
        scene_number = device_config.get(CONF_SCENE_NUMBER, 0)
        name = device_config.get(
            "name", f"Scene {subnet_id}-{device_id} {area_number}.{scene_number}"
        )

        device_address = (subnet_id, device_id)
        scene_address = (area_number, scene_number)
        pybuspro_scene = Scene(hdl, device_address, scene_address, name)

        _LOGGER.debug(
            "Adding scene '%s' at %s.%s on %s",
            name, area_number, scene_number, device_address,
        )
        entities.append(
            BusproScene(hass, pybuspro_scene, name, device_key, buspro_module)
        )

    async_add_entities(entities)


class BusproScene(SceneEntity):
    """Representation of an HDL Buspro scene."""

    def __init__(self, hass, scene, name, unique_key, module=None):
        self._hass = hass
        self._scene = scene
        self._attr_name = name
        self._attr_unique_id = gateway_scoped_unique_id(
            module, f"buspro_{unique_key}"
        )
        self._module = module

    @property
    def name(self):
        """Return the display name of this scene entity."""
        return self._attr_name

    @property
    def available(self) -> bool:
        """Return True while this scene's own gateway connection is up."""
        return bool(self._module is not None and self._module.connected)

    async def async_activate(self, **kwargs) -> None:
        """Activate the HDL scene."""
        await self._scene.run()
