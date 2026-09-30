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

from .const import gateway_scoped_unique_id


class BusproEntityMixin:
    """Mixin wiring an HA entity to a pybuspro device object.

    Provides:
      * ``should_poll = False`` (the device pushes updates);
      * an ``available`` that follows the gateway connection;
      * ``name`` derived from the device;
      * automatic (un)registration of the device-updated callback.
    """

    _attr_should_poll = False

    def _buspro_init_device(self, hass, device, module=None):
        """Store the device and register the update callback."""
        self._hass = hass
        self._module = module
        self._device = device
        identifier = getattr(device, "device_identifier", None)
        if identifier is not None:
            self._attr_unique_id = gateway_scoped_unique_id(module, identifier)
        self._buspro_register_device_updated_cb(self._async_device_updated)

    def _buspro_register_device_updated_cb(self, cb, device=None):
        """Register a device-updated callback and remember it for removal.

        The pair is tracked so :meth:`async_will_remove_from_hass` can detach
        exactly what was attached, even when the callback is a closure the
        subclass defined locally (light/switch/sensor/climate/cover). Without
        this the closure stays bound to the Device's ``device_updated_cbs``
        list, pinning the HA entity and hass forever after removal.
        """
        dev = device if device is not None else getattr(self, "_device", None)
        if dev is None:
            return
        if not hasattr(self, "_buspro_device_cbs"):
            self._buspro_device_cbs = []
        self._buspro_device_cbs.append((dev, cb))
        dev.register_device_updated_cb(cb)

    async def _async_device_updated(self, device):
        """Write state after the underlying device reported a change."""
        self._device = device
        self.async_write_ha_state()

    async def async_will_remove_from_hass(self) -> None:
        """Detach every callback this entity bound to its pybuspro device."""
        for dev, cb in getattr(self, "_buspro_device_cbs", None) or []:
            try:
                dev.unregister_device_updated_cb(cb)
            except (ValueError, AttributeError):
                pass
        self._buspro_device_cbs = []

        # Device teardown: drop the per-device telegram callbacks too, so the
        # Buspro._telegram_received_cbs list does not grow without bound.
        device = getattr(self, "_device", None)
        if device is not None:
            unregister_all = getattr(
                device, "unregister_all_telegram_received_cbs", None
            )
            if unregister_all is not None:
                unregister_all()

        await super().async_will_remove_from_hass()

    @property
    def available(self) -> bool:
        """Return True while this entity's own gateway connection is up."""
        return bool(self._module is not None and self._module.connected)

    @property
    def name(self):
        """Return the display name."""
        return self._device.name


class BusproEntity(BusproEntityMixin, Entity):
    """Standalone Buspro entity backed by a pybuspro device."""

    def __init__(self, hass, device, module=None):
        """Initialize the entity for a pybuspro device."""
        self._buspro_init_device(hass, device, module)
