"""
Shared entity base for the Buspro integration.

Existing platforms (light/switch/sensor/binary_sensor/cover/button) predate
this base class and keep their own wiring to avoid any behavioural regression;
new platforms should use :class:`BusproEntity` (plain entities) or mix in
:class:`BusproEntityMixin` alongside another HA entity class (e.g. ClimateEntity)
to pick up device callbacks, availability and identity handling.
"""

from __future__ import annotations

from homeassistant.helpers.entity import Entity

from . import DATA_BUSPRO


class BusproEntityMixin:
    """Mixin wiring an HA entity to a pybuspro device object.

    Provides:
      * ``should_poll = False`` (the device pushes updates);
      * an ``available`` that follows the gateway connection;
      * ``name`` derived from the device;
      * automatic (un)registration of the device-updated callback.
    """

    _attr_should_poll = False

    def _buspro_init_device(self, hass, device):
        """Store the device and register the update callback."""
        self._hass = hass
        self._device = device
        identifier = getattr(device, "device_identifier", None)
        if identifier is not None:
            self._attr_unique_id = identifier
        device.register_device_updated_cb(self._async_device_updated)

    async def _async_device_updated(self, device):
        """Write state after the underlying device reported a change."""
        self._device = device
        self.async_write_ha_state()

    async def async_will_remove_from_hass(self) -> None:
        """Detach the device-updated callback."""
        try:
            self._device.unregister_device_updated_cb(self._async_device_updated)
        except ValueError:
            pass
        await super().async_will_remove_from_hass()

    @property
    def available(self) -> bool:
        """Return True while the gateway connection is up."""
        module = self._hass.data.get(DATA_BUSPRO)
        return bool(module and module.connected)

    @property
    def name(self):
        """Return the display name."""
        return self._device.name


class BusproEntity(BusproEntityMixin, Entity):
    """Standalone Buspro entity backed by a pybuspro device."""

    def __init__(self, hass, device):
        """Initialize the entity for a pybuspro device."""
        self._buspro_init_device(hass, device)
