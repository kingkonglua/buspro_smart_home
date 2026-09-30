"""
This component provides sensor support for Buspro.

For more details about this platform, please refer to the documentation at
https://home-assistant.io/components/...
"""

import logging

from homeassistant.components.sensor import SensorEntity, SensorStateClass, SensorDeviceClass
from homeassistant.const import (
    ILLUMINANCE,
    TEMPERATURE,
)
from homeassistant.core import callback
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.core import HomeAssistant

from .const import (
    DOMAIN,
    CONF_DEVICES,
    CONF_DEVICE_TYPE,
    CONF_SUBNET_ID,
    CONF_DEVICE_ID,
    CONF_SUBTYPE,
    DEVICE_TYPE_SENSOR,
    coerce_int,
    gateway_scoped_unique_id,
)

_LOGGER = logging.getLogger(__name__)

# M-10: humidity subtype. Kept as plain strings so the module imports cleanly
# against Home Assistant builds where these members are only exposed as
# SensorDeviceClass.HUMIDITY / PERCENTAGE (both str-valued).
CONF_HUMIDITY = "humidity"


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Buspro sensor devices from a config entry."""
    # noinspection PyUnresolvedReferences
    from .pybuspro.devices import Sensor

    buspro_module = hass.data[DOMAIN][config_entry.entry_id]
    hdl = buspro_module.hdl
    devices = config_entry.options.get(CONF_DEVICES, {})
    entities = []

    for device_key, device_config in devices.items():
        if device_config[CONF_DEVICE_TYPE] != DEVICE_TYPE_SENSOR:
            continue

        subnet_id = coerce_int(device_config[CONF_SUBNET_ID], CONF_SUBNET_ID)
        device_id = coerce_int(device_config[CONF_DEVICE_ID], CONF_DEVICE_ID)
        sensor_type = device_config.get(CONF_SUBTYPE, TEMPERATURE)
        name = device_config.get("name", f"Sensor {subnet_id}-{device_id}")
        device_address = (subnet_id, device_id)

        _LOGGER.debug(
            "Adding sensor '%s' with address %s, type '%s'",
            name, device_address, sensor_type
        )

        sensor = Sensor(hdl, device_address, name=name)
        entities.append(BusproSensor(hass, sensor, sensor_type, buspro_module))

    async_add_entities(entities)


# noinspection PyAbstractClass
class BusproSensor(SensorEntity):
    """Representation of a Buspro sensor."""

    def __init__(self, hass, device, sensor_type, module=None):
        self._hass = hass
        self._device = device
        self._sensor_type = sensor_type
        self._module = module
        self._temperature = None
        self._brightness = None
        self._humidity = None
        self._received = False
        self.async_register_callbacks()

    @callback
    def async_register_callbacks(self):
        """Register callbacks to update hass after device was changed."""

        # noinspection PyUnusedLocal
        async def after_update_callback(device):
            """Call after device was updated."""
            if self._hass is not None:
                self._temperature = self._device.temperature
                self._brightness = self._device.brightness
                self._humidity = self._device.humidity
                self._received = True
                self.async_write_ha_state()

        self._device.register_device_updated_cb(after_update_callback)

    @property
    def should_poll(self):
        """No polling needed within Buspro."""
        return False

    async def async_update(self):
        await self._device.read_sensor_status()

    @property
    def name(self):
        """Return the display name of this sensor."""
        return self._device.name

    @property
    def available(self):
        """Return True if entity is available."""
        connected = bool(self._module is not None and self._module.connected)
        if not connected:
            return False

        if self._sensor_type == TEMPERATURE:
            return self._temperature is not None

        if self._sensor_type == ILLUMINANCE:
            return self._brightness is not None

        if self._sensor_type == CONF_HUMIDITY:
            return self._humidity is not None

        # Other types (e.g. dry_contact): do not report as permanently
        # available just because the gateway is connected. Require that the
        # device has actually reported its status at least once, otherwise a
        # never-responded entity would always show as available.
        return self._received

    @property
    def native_value(self):
        """Return the state of the sensor."""
        if self._sensor_type == TEMPERATURE:
            return self._temperature

        if self._sensor_type == ILLUMINANCE:
            return self._brightness

        if self._sensor_type == CONF_HUMIDITY:
            return self._humidity

        return None

    @property
    def device_class(self):
        """Return the class of this sensor."""
        # BUGFIX: use SensorDeviceClass enum members instead of raw strings
        if self._sensor_type == TEMPERATURE:
            return SensorDeviceClass.TEMPERATURE
        if self._sensor_type == ILLUMINANCE:
            return SensorDeviceClass.ILLUMINANCE
        if self._sensor_type == CONF_HUMIDITY:
            # M-10: str-valued; valid even where the enum member is absent.
            return CONF_HUMIDITY
        return None

    @property
    def native_unit_of_measurement(self):
        """Return the unit this state is expressed in."""
        if self._sensor_type == TEMPERATURE:
            return "°C"
        if self._sensor_type == ILLUMINANCE:
            return "lux"
        if self._sensor_type == CONF_HUMIDITY:
            return "%"
        return None

    @property
    def state_class(self):
        """Return the state class."""
        return SensorStateClass.MEASUREMENT

    @property
    def unique_id(self):
        """Return the unique id, scoped to this entity's gateway."""
        return gateway_scoped_unique_id(
            self._module,
            f"{self._device.device_identifier}-{self._sensor_type}",
        )
