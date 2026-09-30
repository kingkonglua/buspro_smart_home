"""
This component provides climate support for Buspro.

Supports floor heating modules and directly connected AC (air conditioner) modules.

For more details about this platform, please refer to the documentation at
https://home-assistant.io/components/...
"""

import logging
from typing import Optional, List

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACMode,
    HVACAction,
)
from homeassistant.const import (
    ATTR_TEMPERATURE,
    UnitOfTemperature,
)
from homeassistant.core import callback
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.core import HomeAssistant

# noinspection PyUnresolvedReferences
from .pybuspro.devices.climate import ControlFloorHeatingStatus
# noinspection PyUnresolvedReferences
from .pybuspro.helpers.enums import OnOffStatus, AcMode, AcFanSpeed

from . import DATA_BUSPRO
from .const import (
    DOMAIN,
    CONF_DEVICES,
    CONF_DEVICE_TYPE,
    CONF_SUBNET_ID,
    CONF_DEVICE_ID,
    CONF_SUBTYPE,
    CONF_AC_NUMBER,
    DEVICE_TYPE_CLIMATE,
)

_LOGGER = logging.getLogger(__name__)

CLIMATE_SUBTYPE_FLOOR_HEATING = "floor_heating"
CLIMATE_SUBTYPE_AC = "ac"

PRESET_NONE = "none"
PRESET_AWAY = "away"
PRESET_HOME = "home"
PRESET_SLEEP = "sleep"

HA_PRESET_TO_HDL = {
    PRESET_NONE: 1,     # Normal
    PRESET_HOME: 2,     # Day
    PRESET_SLEEP: 3,    # Night
    PRESET_AWAY: 4,     # Away
}
HDL_TO_HA_PRESET = {
    1: PRESET_NONE,     # Normal
    2: PRESET_HOME,     # Day
    3: PRESET_SLEEP,    # Night
    4: PRESET_AWAY,     # Away
}

# AC mode mapping
HA_HVAC_TO_AC = {
    HVACMode.COOL: AcMode.COOL.value,       # 0
    HVACMode.HEAT: AcMode.HEAT.value,       # 1
    HVACMode.FAN_ONLY: AcMode.FAN.value,    # 2
    HVACMode.AUTO: AcMode.AUTO.value,       # 3
    HVACMode.DRY: AcMode.DRY.value,         # 4
}
AC_TO_HA_HVAC = {
    AcMode.COOL.value: HVACMode.COOL,
    AcMode.HEAT.value: HVACMode.HEAT,
    AcMode.FAN.value: HVACMode.FAN_ONLY,
    AcMode.AUTO.value: HVACMode.AUTO,
    AcMode.DRY.value: HVACMode.DRY,
}

HA_FAN_TO_AC = {
    "auto": AcFanSpeed.AUTO.value,       # 0
    "high": AcFanSpeed.HIGH.value,       # 1
    "medium": AcFanSpeed.MEDIUM.value,   # 2
    "low": AcFanSpeed.LOW.value,         # 3
}
AC_TO_HA_FAN = {
    AcFanSpeed.AUTO.value: "auto",
    AcFanSpeed.HIGH.value: "high",
    AcFanSpeed.MEDIUM.value: "medium",
    AcFanSpeed.LOW.value: "low",
}

SWING_OFF = "off"
SWING_ON = "on"


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Buspro climate devices from a config entry."""
    # noinspection PyUnresolvedReferences
    from .pybuspro.devices import Climate, AC

    buspro_module = hass.data[DOMAIN]
    hdl = buspro_module.hdl
    devices = config_entry.options.get(CONF_DEVICES, {})
    entities = []

    for device_key, device_config in devices.items():
        if device_config[CONF_DEVICE_TYPE] != DEVICE_TYPE_CLIMATE:
            continue

        subnet_id = device_config[CONF_SUBNET_ID]
        device_id = device_config[CONF_DEVICE_ID]
        subtype = device_config.get(CONF_SUBTYPE, CLIMATE_SUBTYPE_FLOOR_HEATING)
        name = device_config.get("name", f"Climate {subnet_id}-{device_id}")
        device_address = (subnet_id, device_id)

        if subtype == CLIMATE_SUBTYPE_AC:
            ac_number = device_config.get(CONF_AC_NUMBER, 1)

            _LOGGER.debug(
                "Adding AC climate '%s' with address %s, ac_number %s",
                name, device_address, ac_number
            )

            ac = AC(hdl, device_address, ac_number, name)
            entities.append(BusproACClimate(hass, ac))
        else:
            _LOGGER.debug(
                "Adding floor heating climate '%s' with address %s",
                name, device_address
            )

            climate = Climate(hdl, device_address, name)

            # Default preset modes
            preset_modes = [PRESET_HOME, PRESET_SLEEP, PRESET_AWAY]

            entities.append(BusproClimate(hass, climate, preset_modes, None))

    async_add_entities(entities)


# noinspection PyAbstractClass
class BusproClimate(ClimateEntity):
    """Representation of a Buspro floor heating climate device."""

    def __init__(self, hass, device, preset_modes, relay_sensor):
        self._hass = hass
        self._device = device
        self._target_temperature = self._device.target_temperature
        self._is_on = self._device.is_on
        self._preset_modes = preset_modes
        self._mode = self._device.mode  # 1/3/4

        self._relay_sensor = relay_sensor
        self._relay_sensor_is_on = None
        if self._relay_sensor is not None:
            self._relay_sensor_is_on = self._relay_sensor.single_channel_is_on

        self._enable_turn_on_off_backwards_compatibility = False
        self._attr_supported_features = (
            ClimateEntityFeature.TARGET_TEMPERATURE
            | ClimateEntityFeature.PRESET_MODE
            | ClimateEntityFeature.TURN_OFF
            | ClimateEntityFeature.TURN_ON
        )

        self.async_register_callbacks()

    async def async_turn_off(self) -> None:
        await self.async_set_hvac_mode(HVACMode.OFF)

    async def async_turn_on(self) -> None:
        await self.async_set_hvac_mode(HVACMode.HEAT)

    @callback
    def async_register_callbacks(self):
        """Register callbacks to update hass after device was changed."""

        # noinspection PyUnusedLocal
        async def after_update_callback(device):
            """Call after device was updated."""
            self._device = device
            self._target_temperature = device.target_temperature
            self._is_on = device.is_on
            self._mode = device.mode

            _LOGGER.debug(
                "Device '%s', IsOn: %s, Mode: %s, TargetTemp: %s",
                self._device.name, self._is_on, self._device.mode,
                self._device.target_temperature
            )

            if self._hass is not None:
                self.async_write_ha_state()

        async def after_relay_sensor_update_callback(device):
            """Call after relay sensor device was updated."""
            self._relay_sensor_is_on = device.single_channel_is_on
            self.async_write_ha_state()

        self._device.register_device_updated_cb(after_update_callback)

        if self._relay_sensor is not None:
            self._relay_sensor.register_device_updated_cb(after_relay_sensor_update_callback)

    @property
    def should_poll(self):
        """No polling needed within Buspro."""
        return False

    @property
    def name(self):
        """Return the display name of this climate device."""
        return self._device.name

    @property
    def available(self):
        """Return True if entity is available."""
        return self._hass.data[DATA_BUSPRO].connected

    @property
    def temperature_unit(self):
        """Return the unit of measurement."""
        return UnitOfTemperature.CELSIUS

    @property
    def current_temperature(self):
        """Return the current temperature."""
        return self._device.temperature

    @property
    def target_temperature(self):
        """Return the temperature we try to reach."""
        target_temperature = self._target_temperature
        if target_temperature is None:
            target_temperature = self._device.target_temperature
        return target_temperature

    @property
    def preset_mode(self) -> Optional[str]:
        """Return the current preset mode."""
        # BUGFIX: use .get() to avoid KeyError when _mode is None on first load
        if self._mode not in list(HDL_TO_HA_PRESET):
            return PRESET_NONE
        return HDL_TO_HA_PRESET.get(self._mode, PRESET_NONE)

    @property
    def preset_modes(self) -> Optional[List[str]]:
        """Return a list of available preset modes."""
        if len(self._preset_modes) == 0:
            return None

        keys = HA_PRESET_TO_HDL.keys() & self._preset_modes
        ha_preset_to_hdl_configured = {k: HA_PRESET_TO_HDL[k] for k in keys}
        return list(ha_preset_to_hdl_configured)

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Set new preset mode."""
        if preset_mode not in list(HA_PRESET_TO_HDL):
            preset_mode = PRESET_NONE
        mode = HA_PRESET_TO_HDL[preset_mode]

        _LOGGER.debug(
            "Setting preset mode to '%s' (%s) for device '%s'",
            preset_mode, mode, self._device.name
        )

        climate_control = ControlFloorHeatingStatus()
        climate_control.mode = mode

        await self._device.control_heating_status(climate_control)
        self.async_write_ha_state()

    @property
    def hvac_action(self) -> Optional[str]:
        """Return current action ie. heating, idle, off."""
        if self._is_on:
            if self._relay_sensor_is_on is None:
                # BUGFIX: HVACAction has no HEAT member; floor heating action is HEATING
                return HVACAction.HEATING
            else:
                if self._relay_sensor_is_on:
                    return HVACAction.HEATING
                else:
                    return HVACAction.IDLE
        else:
            return HVACAction.OFF

    @property
    def hvac_mode(self) -> Optional[str]:
        """Return current operation ie. heat, off."""
        if self._is_on:
            return HVACMode.HEAT
        else:
            return HVACMode.OFF

    @property
    def hvac_modes(self) -> Optional[List[str]]:
        """Return the list of available operation modes."""
        return [HVACMode.HEAT, HVACMode.OFF]

    async def async_set_hvac_mode(self, hvac_mode: str) -> None:
        """Set operation mode."""
        if hvac_mode == HVACMode.OFF:
            climate_control = ControlFloorHeatingStatus()
            climate_control.status = OnOffStatus.OFF.value
            await self._device.control_heating_status(climate_control)
            self.async_write_ha_state()
        elif hvac_mode == HVACMode.HEAT:
            climate_control = ControlFloorHeatingStatus()
            climate_control.status = OnOffStatus.ON.value
            await self._device.control_heating_status(climate_control)
            self.async_write_ha_state()
        else:
            _LOGGER.error("Unrecognized hvac mode: %s", hvac_mode)
            return

    @property
    def target_temperature_step(self):
        """Return the supported step of target temperature."""
        return 1

    @property
    def unique_id(self):
        """Return the unique id."""
        return self._device.device_identifier

    async def async_set_temperature(self, **kwargs):
        """Set new target temperature."""
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is None:
            return

        climate_control = ControlFloorHeatingStatus()
        # BUGFIX: use .get() to avoid KeyError when _mode is None on first load
        preset = HDL_TO_HA_PRESET.get(self._mode, PRESET_NONE)
        target_temperature = int(temperature)

        _LOGGER.debug(
            "Setting '%s' temperature to %s",
            preset, target_temperature
        )

        if preset == PRESET_NONE:
            climate_control.normal_temperature = target_temperature
        elif preset == PRESET_HOME:
            climate_control.day_temperature = target_temperature
        elif preset == PRESET_SLEEP:
            climate_control.night_temperature = target_temperature
        elif preset == PRESET_AWAY:
            climate_control.away_temperature = target_temperature
        else:
            climate_control.normal_temperature = target_temperature

        await self._device.control_heating_status(climate_control)
        self.async_write_ha_state()


# noinspection PyAbstractClass
class BusproACClimate(ClimateEntity):
    """Representation of a Buspro directly connected AC climate device."""

    def __init__(self, hass, device):
        self._hass = hass
        self._device = device

        self._enable_turn_on_off_backwards_compatibility = False
        self._attr_supported_features = (
            ClimateEntityFeature.TARGET_TEMPERATURE
            | ClimateEntityFeature.FAN_MODE
            | ClimateEntityFeature.SWING_MODE
            | ClimateEntityFeature.TURN_OFF
            | ClimateEntityFeature.TURN_ON
        )

        self._attr_hvac_modes = [
            HVACMode.OFF,
            HVACMode.COOL,
            HVACMode.HEAT,
            HVACMode.DRY,
            HVACMode.FAN_ONLY,
            HVACMode.AUTO,
        ]
        self._attr_fan_modes = ["auto", "high", "medium", "low"]
        self._attr_swing_modes = [SWING_OFF, SWING_ON]
        self._attr_min_temp = 16
        self._attr_max_temp = 30
        self._attr_target_temperature_step = 1

        self.async_register_callbacks()

    async def async_turn_off(self) -> None:
        await self._device.turn_off()

    async def async_turn_on(self) -> None:
        await self._device.turn_on()

    @callback
    def async_register_callbacks(self):
        """Register callbacks to update hass after device was changed."""

        # noinspection PyUnusedLocal
        async def after_update_callback(device):
            """Call after device was updated."""
            self._device = device

            _LOGGER.debug(
                "AC '%s', IsOn: %s, Mode: %s, FanSpeed: %s, "
                "CurrentTemp: %s, TargetTemp: %s, Sweep: %s",
                self._device.name, self._device.is_on, self._device.mode,
                self._device.fan_speed, self._device.current_temperature,
                self._device.target_temperature, self._device.sweep
            )

            if self._hass is not None:
                self.async_write_ha_state()

        self._device.register_device_updated_cb(after_update_callback)

    @property
    def should_poll(self):
        """No polling needed within Buspro."""
        return False

    @property
    def name(self):
        """Return the display name of this climate device."""
        return self._device.name

    @property
    def available(self):
        """Return True if entity is available."""
        return self._hass.data[DATA_BUSPRO].connected

    @property
    def unique_id(self):
        """Return the unique id."""
        return self._device.device_identifier

    @property
    def temperature_unit(self):
        """Return the unit of measurement."""
        return UnitOfTemperature.CELSIUS

    @property
    def current_temperature(self):
        """Return the current temperature."""
        return self._device.current_temperature

    @property
    def target_temperature(self):
        """Return the temperature we try to reach."""
        return self._device.target_temperature

    @property
    def hvac_mode(self) -> Optional[str]:
        """Return current operation."""
        if not self._device.is_on:
            return HVACMode.OFF
        mode = self._device.mode
        if mode is None:
            return HVACMode.OFF
        return AC_TO_HA_HVAC.get(mode, HVACMode.COOL)

    @property
    def hvac_action(self) -> Optional[str]:
        """Return current action."""
        if not self._device.is_on:
            return HVACAction.OFF
        mode = self._device.mode
        if mode == AcMode.COOL.value or mode == AcMode.DRY.value:
            return HVACAction.COOLING
        elif mode == AcMode.HEAT.value:
            return HVACAction.HEATING
        elif mode == AcMode.FAN.value:
            return HVACAction.FAN
        return HVACAction.IDLE

    @property
    def fan_mode(self) -> Optional[str]:
        """Return current fan speed."""
        fan_speed = self._device.fan_speed
        if fan_speed is None:
            return None
        return AC_TO_HA_FAN.get(fan_speed, "auto")

    @property
    def swing_mode(self) -> Optional[str]:
        """Return current swing setting."""
        sweep = self._device.sweep
        if sweep is None:
            return None
        return SWING_ON if sweep == 1 else SWING_OFF

    async def async_set_hvac_mode(self, hvac_mode: str) -> None:
        """Set operation mode."""
        if hvac_mode == HVACMode.OFF:
            await self._device.turn_off()
        else:
            ac_mode = HA_HVAC_TO_AC.get(hvac_mode)
            if ac_mode is None:
                _LOGGER.error("Unrecognized hvac mode: %s", hvac_mode)
                return
            # set_mode() sends a full control command that already sets
            # status=ON (control() defaults new_status to 1), so an extra
            # turn_on() would be a redundant second command. Rely on set_mode.
            await self._device.set_mode(ac_mode)

    async def async_set_temperature(self, **kwargs):
        """Set new target temperature."""
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is None:
            return
        await self._device.set_temperature(temperature)

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        """Set fan speed."""
        ac_fan = HA_FAN_TO_AC.get(fan_mode)
        if ac_fan is None:
            _LOGGER.error("Unrecognized fan mode: %s", fan_mode)
            return
        await self._device.set_fan_speed(ac_fan)

    async def async_set_swing_mode(self, swing_mode: str) -> None:
        """Set swing (sweep) mode."""
        await self._device.set_sweep(1 if swing_mode == SWING_ON else 0)
