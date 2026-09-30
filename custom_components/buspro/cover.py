"""
This component provides cover (curtain) support for Buspro.

Two types of hardware are supported:
  - Curtain module (e.g. HDL-MW02.431): open / close / stop only, no position.
  - Buspro curtain / blinds motor (e.g. HDL-MWC1-BP.10, HDL-MVSM35B.20):
    open / close / stop plus percentage position. The position is estimated
    from the configured travel time because the HDL bus protocol only reports
    opening / closing / stopped states.

For more details about this platform, please refer to the documentation at
https://home-assistant.io/components/...
"""

import asyncio
import logging
import time

from homeassistant.components.cover import (
    CoverEntity,
    CoverEntityFeature,
    ATTR_POSITION,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    STATE_OPEN,
    STATE_CLOSED,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DOMAIN,
    CONF_DEVICES,
    CONF_DEVICE_TYPE,
    CONF_SUBNET_ID,
    CONF_DEVICE_ID,
    CONF_CHANNEL,
    CONF_SUBTYPE,
    CONF_TRAVEL_TIME,
    DEVICE_TYPE_COVER,
    coerce_int,
)

_LOGGER = logging.getLogger(__name__)

COVER_SUBTYPE_CURTAIN_MODULE = "curtain_module"
COVER_SUBTYPE_BUS_MOTOR = "bus_motor"

# CurtainAction values as reported by the bus
CURTAIN_STOP = 0   # stopped / parked in a middle position
CURTAIN_OPEN = 1   # curtain physically open (at open limit)
CURTAIN_CLOSE = 2  # curtain physically closed (at closed limit)


def _coerce_travel_time(value):
    """Normalise a cover ``travel_time`` config value.

    Numeric values pass through (floats are truncated).  ``None`` and values
    ``<= 0`` disable travel-time position estimation.  Anything else -- e.g. a
    string typed by the user -- is rejected with a descriptive error instead of
    raising a raw TypeError like ``'>' not supported between str and int``.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        value = int(value)
    if isinstance(value, (int, float)):
        try:
            seconds = int(value)
        except (ValueError, OverflowError) as err:
            raise ValueError(
                f"travel_time must be a finite number, got {value!r}"
            ) from err
        return seconds if seconds > 0 else None
    raise ValueError(
        f"travel_time must be a number, got {type(value).__name__} {value!r}"
    )


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Buspro cover devices from a config entry."""
    # noinspection PyUnresolvedReferences
    from .pybuspro.devices import Curtain

    buspro_module = hass.data[DOMAIN][config_entry.entry_id]
    hdl = buspro_module.hdl
    devices = config_entry.options.get(CONF_DEVICES, {})
    entities = []

    for device_key, device_config in devices.items():
        if device_config[CONF_DEVICE_TYPE] != DEVICE_TYPE_COVER:
            continue

        subnet_id = coerce_int(device_config[CONF_SUBNET_ID], CONF_SUBNET_ID)
        device_id = coerce_int(device_config[CONF_DEVICE_ID], CONF_DEVICE_ID)
        channel = coerce_int(device_config[CONF_CHANNEL], CONF_CHANNEL)
        subtype = device_config.get(CONF_SUBTYPE, COVER_SUBTYPE_CURTAIN_MODULE)
        travel_time = _coerce_travel_time(
            device_config.get(CONF_TRAVEL_TIME, 15)
        )
        name = device_config.get("name", f"Cover {subnet_id}-{device_id}-{channel}")
        device_address = (subnet_id, device_id)

        _LOGGER.debug(
            "Adding cover '%s' with address %s, channel %s, subtype %s, "
            "travel_time %s",
            name, device_address, channel, subtype, travel_time
        )

        curtain = Curtain(hdl, device_address, channel, name)

        entities.append(
            BusproCover(
                hass, curtain, subtype, travel_time, buspro_module
            )
        )

    async_add_entities(entities)


# noinspection PyAbstractClass
class BusproCover(CoverEntity):
    """Representation of a Buspro cover (curtain) device."""

    def __init__(self, hass, device, subtype, travel_time, module=None):
        self._hass = hass
        self._device = device
        self._module = module
        self._subtype = subtype
        # Normalise again here so a directly-constructed entity (and any future
        # caller) can never compare a str against an int.
        self._travel_time = _coerce_travel_time(travel_time)

        # Position tracking (travel time estimation), only for bus motors
        self._supports_position = (
            self._subtype == COVER_SUBTYPE_BUS_MOTOR and self._travel_time is not None
        )
        self._fixed_position = None   # position when not moving
        self._direction = None        # 1 opening, -1 closing, None stopped
        self._position_at_start = None
        self._target_position = None  # 0/100 target of the active movement
        self._start_time = None
        self._stop_task = None

        if self._supports_position:
            self._attr_supported_features = (
                CoverEntityFeature.OPEN
                | CoverEntityFeature.CLOSE
                | CoverEntityFeature.STOP
                | CoverEntityFeature.SET_POSITION
            )
        else:
            self._attr_supported_features = (
                CoverEntityFeature.OPEN
                | CoverEntityFeature.CLOSE
                | CoverEntityFeature.STOP
            )

        self._attr_is_opening = False
        self._attr_is_closing = False
        self._attr_is_closed = None

        self.async_register_callbacks()

    @callback
    def async_register_callbacks(self):
        """Register callbacks to update hass after device was changed."""

        # noinspection PyUnusedLocal
        async def after_update_callback(device):
            """Call after device was updated."""
            self._device = device
            self._sync_from_bus_status()

            _LOGGER.debug(
                "Cover '%s', Status: %s, Level: %s, Position: %s",
                self._device.name, self._device.status, self._device.level,
                self._estimate_position()
            )

            if self._hass is not None:
                self.async_write_ha_state()

        self._device.register_device_updated_cb(after_update_callback)

    def _sync_from_bus_status(self):
        """Sync local movement state with the status reported by the bus.

        State machine (M-5): a movement flag is only ever true while a tracked
        movement is actually in progress, i.e. while the target position differs
        from the current position.  The bus reports *end states*, not a generic
        "moving" state:

            status == 1 (CURTAIN_OPEN)  -> at the open limit,   position 100
            status == 2 (CURTAIN_CLOSE) -> at the closed limit, position 0
            status == 0 (CURTAIN_STOP)  -> stopped mid-way, pin the estimate
        """
        status = self._device.status

        if status == CURTAIN_OPEN or status == CURTAIN_CLOSE:
            # Absolute ground truth: snap to the limit and clear every movement
            # flag.  The motor is already parked, so drop the scheduled stop too.
            position = 100 if status == CURTAIN_OPEN else 0
            self._snap_to_limit(position, cancel_stop=True)
        elif status == CURTAIN_STOP:
            # STOP: the current estimate freezes as the new stationary position
            # and all movement flags are cleared.
            self._fix_position()
        elif self._direction is None:
            # Unknown status and no tracked movement: nothing is moving.
            self._set_moving_flags(False, False)

        self._refresh_is_closed()

    def _set_moving_flags(self, opening, closing):
        """Set the optimistic opening/closing flags."""
        self._attr_is_opening = bool(opening)
        self._attr_is_closing = bool(closing)

    def _refresh_is_closed(self):
        """Keep ``is_closed`` consistent with the pinned position."""
        if self._supports_position:
            if self._direction is not None:
                # While a movement is tracked the state is opening/closing, so
                # leave is_closed untouched to avoid a transient "closed" flap.
                return
            self._attr_is_closed = (
                self._fixed_position is not None and self._fixed_position <= 0
            )
        else:
            self._attr_is_closed = self._device.is_closed

    def _start_movement(self, direction):
        """Record the start of a movement for travel time estimation.

        Returns ``True`` when a movement is now being tracked and ``False`` when
        the curtain is already at the requested limit - in that case no movement
        flag may be raised (M-5).
        """
        current = self._estimate_position()
        if current is None:
            # Never track a "no position" start - assume fully closed.
            current = 0
        target = 100 if direction > 0 else 0

        if self._direction is None and abs(target - current) < 1:
            # A stationary curtain already at the requested limit has nothing to
            # do: never raise a movement flag for it (M-5).  A reversal while a
            # movement is already in progress is a real movement even if the
            # coarse estimate still equals the new target.
            self._snap_to_limit(target, cancel_stop=True)
            return False

        self._fixed_position = current
        self._direction = direction
        self._position_at_start = float(current)
        self._target_position = target
        self._start_time = time.monotonic()
        # Optimistic flags: the bus may not confirm the movement, but HA should
        # immediately show "opening"/"closing" after the user pressed the button.
        self._set_moving_flags(direction > 0, direction < 0)
        return True

    def _estimate_position(self):
        """Estimate the current position (0 closed, 100 open) from travel time."""
        if not self._supports_position:
            return None
        if self._fixed_position is None:
            # Unknown position, fall back to 0 so an estimate is always numeric.
            return 0
        if self._direction is None or self._start_time is None:
            return self._fixed_position
        elapsed = time.monotonic() - self._start_time
        delta = self._direction * (elapsed / self._travel_time * 100.0)
        position = self._position_at_start + delta
        # Only snap when travel reaches the limit it is heading for.  Snapping
        # on the generic ``<=0``/``>=100`` boundary would clear the flags at the
        # *start* of a movement (e.g. opening from 0 with zero elapsed time).
        # M-5: reaching a limit must clear every movement flag; the scheduled
        # stop is deliberately kept so the motor is still told to stop.
        if self._direction > 0 and position >= 100:
            self._snap_to_limit(100)
            return 100
        if self._direction < 0 and position <= 0:
            self._snap_to_limit(0)
            return 0
        return position

    def _snap_to_limit(self, value, cancel_stop=False):
        """Pin the tracked position to a limit and drop the movement baseline.

        The bus only reports moving/stopped, so once the travel-time estimate
        reaches (or overshoots) a limit the position must become a fixed
        boundary value. Otherwise a later status snapshot would fight a stale
        baseline and make the reported position jump.  All movement flags are
        cleared here - a curtain at 0 or 100 is by definition not moving (M-5).
        """
        if cancel_stop and self._stop_task is not None:
            self._stop_task.cancel()
            self._stop_task = None
        self._fixed_position = value
        self._direction = None
        self._start_time = None
        self._position_at_start = None
        self._target_position = None
        self._set_moving_flags(False, False)

    def _fix_position(self):
        """Stop movement tracking and pin the position at the current estimate.

        Used when an explicit STOP arrives: the estimated position freezes as the
        new stationary position and every movement flag is cleared (M-5,
        BUG-C1).
        """
        if self._stop_task is not None:
            self._stop_task.cancel()
            self._stop_task = None
        position = self._estimate_position()
        if position is not None:
            self._fixed_position = position
        self._direction = None
        self._start_time = None
        self._position_at_start = None
        self._target_position = None
        self._set_moving_flags(False, False)
        self._refresh_is_closed()

    def _schedule_stop(self, delay):
        """Schedule a stop command after the given delay."""
        if self._stop_task is not None:
            self._stop_task.cancel()

        async def delayed_stop():
            await asyncio.sleep(delay)
            await self._device.stop()
            self._fix_position()
            if self._hass is not None:
                self.async_write_ha_state()

        # M-7: create the task through HA so its lifecycle is tracked and it is
        # cancelled if the entry/entity is unloaded before the delay elapses.
        self._stop_task = self._hass.async_create_task(delayed_stop())

    async def async_will_remove_from_hass(self):
        """Cancel any pending delayed-stop task (M-7)."""
        if self._stop_task is not None:
            self._stop_task.cancel()
            self._stop_task = None

    @property
    def should_poll(self):
        """No polling needed within Buspro."""
        return False

    @property
    def name(self):
        """Return the display name of this cover device."""
        return self._device.name

    @property
    def available(self):
        """Return True if entity is available."""
        return bool(self._module is not None and self._module.connected)

    @property
    def unique_id(self):
        """Return the unique id."""
        return self._device.device_identifier

    @property
    def current_cover_position(self):
        """Return current position of cover (0 is closed, 100 is open)."""
        return self._estimate_position()

    @property
    def is_closed(self):
        """Return true if cover is closed."""
        return self._attr_is_closed

    @property
    def state(self):
        """Return the state of the cover."""
        if self.is_opening:
            return "opening"
        if self.is_closing:
            return "closing"
        if self.is_closed:
            return STATE_CLOSED
        return STATE_OPEN

    async def async_open_cover(self, **kwargs):
        """Open the cover."""
        _LOGGER.debug("Opening cover '%s'", self._device.name)
        if self._supports_position and self._start_movement(1):
            self._schedule_stop(self._travel_time)
        await self._device.open()

    async def async_close_cover(self, **kwargs):
        """Close the cover."""
        _LOGGER.debug("Closing cover '%s'", self._device.name)
        if self._supports_position and self._start_movement(-1):
            self._schedule_stop(self._travel_time)
        await self._device.close()

    async def async_stop_cover(self, **kwargs):
        """Stop the cover."""
        _LOGGER.debug("Stopping cover '%s'", self._device.name)
        self._fix_position()
        await self._device.stop()
        if self._hass is not None:
            self.async_write_ha_state()

    async def async_set_cover_position(self, **kwargs):
        """Set the cover position (percentage)."""
        position = kwargs.get(ATTR_POSITION)
        if position is None or not self._supports_position:
            return

        position = int(position)
        current = self._estimate_position()
        if current is None:
            current = self._fixed_position if self._fixed_position is not None else 0
        current = int(current)

        _LOGGER.debug(
            "Setting cover '%s' position to %s (current %s)",
            self._device.name, position, current
        )

        delta = position - current
        if abs(delta) < 1:
            return

        if delta > 0:
            started = self._start_movement(1)
            await self._device.open()
        else:
            started = self._start_movement(-1)
            await self._device.close()

        if started:
            run_time = abs(delta) / 100.0 * self._travel_time
            self._schedule_stop(run_time)
