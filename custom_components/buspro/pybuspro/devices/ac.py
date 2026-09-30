import asyncio
import logging

from .control import _ControlAcStatus, _ReadAcStatus
from .device import Device
from ..helpers.enums import *


DEFAULT_MODE_AND_FAN = 48
DEFAULT_TEMPERATURE = 22

# BUGFIX: build mode_and_fan dynamically instead of hardcoding 0x30.
# Mapping of HDL AC mode / fan values to their nibble inside the mode_and_fan byte.
MODE_BYTE_MAP = {
    AcMode.COOL.value: 0,
    AcMode.HEAT.value: 1,
    AcMode.FAN.value: 2,
    AcMode.AUTO.value: 3,
    AcMode.DRY.value: 4,
}
FAN_BYTE_MAP = {
    AcFanSpeed.AUTO.value: 0,
    AcFanSpeed.HIGH.value: 1,
    AcFanSpeed.MEDIUM.value: 2,
    AcFanSpeed.LOW.value: 3,
}

_LOGGER = logging.getLogger(__name__)


class AC(Device):
    """HDL Buspro air conditioner controller module (operate codes 0x1938/0x193A).

    The module must be sent the full 13 byte status on every control request,
    so the class keeps the last known state and merges partial updates on top of it.
    """

    def __init__(self, buspro, device_address, ac_number, name=""):
        super().__init__(buspro, device_address, name)
        # device_address = (subnet_id, device_id)

        self._buspro = buspro
        self._device_address = device_address
        self._ac_number = ac_number

        self._status = None                 # 1 on / 0 off
        self._available = False             # True once a real status frame seen
        self._mode = None                   # 0 cool, 1 heat, 2 fan, 3 auto, 4 dry
        self._fan_speed = None              # 0 auto, 1 high, 2 medium, 3 low
        self._current_temperature = None    # temperature measured by the unit / DLP
        self._cooling_temperature = None
        self._heating_temperature = None
        self._auto_temperature = None
        self._dry_temperature = None
        self._current_mode_temperature = None
        self._sweep = None                  # 0 off, 1 on
        self._temperature_type = 0
        self._mode_and_fan = DEFAULT_MODE_AND_FAN

        self.register_telegram_received_cb(self._telegram_received_cb)
        self._call_read_current_status(run_from_init=True)

    def _telegram_received_cb(self, telegram):
        if telegram.operate_code == OperateCode.ReadAcStatusResponse or \
                telegram.operate_code == OperateCode.ControlAcStatusResponse:
            self._apply_status_payload(telegram.payload)
            self._call_device_updated()

    def _apply_status_payload(self, payload):
        # A missing (None) / empty frame must never raise: the gateway drops
        # packets and devices go offline. Keep the last known state instead.
        #
        # M-9: some HDL AC modules answer with a legal short frame. Upstream
        # v5.0.7 (AirConditioner._telegram_received_cb) accepts anything from
        # 12 bytes up; the previous `< 13` check silently discarded those
        # frames. Accept the same 12-byte minimum, but only read the fields
        # that are actually present so a short frame can never index past the
        # end (payload[12] does not exist in a 12-byte frame).
        if not payload or len(payload) < 12:
            return
        # Filter messages for our AC unit only (when more than one unit is present)
        ac_number = payload[0]
        if self._ac_number is not None and ac_number != self._ac_number:
            return

        # M-6: an all-0xFF status region is HDL's "no AC unit wired to this
        # slot" sentinel. Treating it as real state would make the entity look
        # available with fake values and replay 0xFF onto the bus on control.
        if all(b == 255 for b in payload[8:13]):
            self._available = False
            return

        self._temperature_type = payload[1]
        self._current_temperature = payload[2]
        self._cooling_temperature = payload[3]
        self._heating_temperature = payload[4]
        self._auto_temperature = payload[5]
        self._dry_temperature = payload[6]
        self._mode_and_fan = payload[7]
        self._status = payload[8]
        self._mode = payload[9]
        self._fan_speed = payload[10]
        self._current_mode_temperature = payload[11]
        # A 12-byte frame stops before the sweep byte; keep the last known
        # value instead of raising IndexError.
        if len(payload) >= 13:
            self._sweep = payload[12]
        self._available = True

    async def read_status(self):
        rasc = _ReadAcStatus(self._buspro)
        rasc.subnet_id, rasc.device_id = self._device_address
        await rasc.send()

    async def turn_on(self):
        await self.control(status=1)

    async def turn_off(self):
        await self.control(status=0)

    async def set_mode(self, mode):
        await self.control(mode=mode)

    async def set_fan_speed(self, fan_speed):
        await self.control(fan_speed=fan_speed)

    async def set_temperature(self, temperature):
        await self.control(temperature=temperature)

    async def set_sweep(self, sweep):
        await self.control(sweep=sweep)

    async def control(self, status=None, mode=None, fan_speed=None, temperature=None, sweep=None):
        """Send a full status to the AC module, merging the given partial update."""
        # Apply the explicit power state when given; otherwise reuse the last
        # known state. If even that is unknown, we still send the command
        # (defaulting to ON) instead of silently dropping it.
        new_status = status
        if new_status is None:
            new_status = self._status
        if new_status is None:
            # BUGFIX: do not silently drop the command when power state is
            # unknown. HA upper layer already provides idempotency; default to
            # ON so partial updates (set_mode/set_temperature) are still sent.
            new_status = 1
        new_mode = self._mode if mode is None else mode
        new_fan_speed = self._fan_speed if fan_speed is None else fan_speed
        new_sweep = self._sweep if sweep is None else sweep

        cooling = self._cooling_temperature
        heating = self._heating_temperature
        auto = self._auto_temperature
        dry = self._dry_temperature

        if temperature is not None:
            temperature = int(temperature)
            if new_mode == AcMode.COOL.value:
                cooling = temperature
            elif new_mode == AcMode.HEAT.value:
                heating = temperature
            elif new_mode == AcMode.AUTO.value:
                auto = temperature
            elif new_mode == AcMode.DRY.value:
                dry = temperature
            elif new_mode == AcMode.FAN.value:
                pass

        if cooling is None:
            cooling = DEFAULT_TEMPERATURE
        if heating is None:
            heating = DEFAULT_TEMPERATURE
        if auto is None:
            auto = DEFAULT_TEMPERATURE
        if dry is None:
            dry = DEFAULT_TEMPERATURE
        if new_mode is None:
            new_mode = AcMode.COOL.value
        if new_fan_speed is None:
            new_fan_speed = AcFanSpeed.AUTO.value
        if new_sweep is None:
            new_sweep = 0

        # Refresh the active mode temperature when the mode changed, otherwise
        # the cached value from the previous mode would be sent along.
        mode_changed = (
            self._mode is not None
            and new_mode is not None
            and self._mode != new_mode
        )
        current_mode_temperature = self._current_mode_temperature
        if new_mode == AcMode.FAN.value:
            # FAN mode has no target temperature; keep the previously
            # known value instead of sending 0 (which the AC would
            # interpret as an actual setpoint).
            if current_mode_temperature is None:
                current_mode_temperature = DEFAULT_TEMPERATURE
        elif temperature is not None:
            # F-1: an explicit temperature request must also drive byte 11
            # (HDL "Setup Temperature"), which is what actually moves the
            # unit -- writing only the per-mode memory slot left the unit on
            # its old setpoint.
            current_mode_temperature = temperature
        elif current_mode_temperature is None or mode_changed:
            current_mode_temperature = {
                AcMode.COOL.value: cooling,
                AcMode.HEAT.value: heating,
                AcMode.AUTO.value: auto,
                AcMode.DRY.value: dry,
            }.get(new_mode, DEFAULT_TEMPERATURE)

        cc = _ControlAcStatus(self._buspro)
        cc.subnet_id, cc.device_id = self._device_address
        cc.ac_number = self._ac_number if self._ac_number is not None else 1
        cc.temperature_type = self._temperature_type
        # M-6: do not fabricate a plausible room reading when it was never
        # observed; send 0 instead of the old DEFAULT_TEMPERATURE (22).
        cc.current_temperature = (
            self._current_temperature
            if self._current_temperature is not None
            else 0
        )
        cc.cooling_temperature = cooling
        cc.heating_temperature = heating
        cc.auto_temperature = auto
        cc.dry_temperature = dry
        # BUGFIX: derive mode_and_fan from the resolved mode/fan, not the
        # hardcoded DEFAULT_MODE_AND_FAN (0x30) that was used before.
        mode_byte = MODE_BYTE_MAP.get(new_mode, 0)
        fan_byte = FAN_BYTE_MAP.get(new_fan_speed, 0)
        mode_and_fan = (mode_byte << 4) | fan_byte
        cc.mode_and_fan = mode_and_fan
        cc.status = new_status
        cc.mode = new_mode
        cc.fan_speed = new_fan_speed
        cc.current_mode_temperature = current_mode_temperature
        cc.sweep = new_sweep

        # Optimistically update our cached state
        self._status = new_status
        self._mode = new_mode
        self._fan_speed = new_fan_speed
        self._sweep = new_sweep
        self._mode_and_fan = mode_and_fan
        self._cooling_temperature = cooling
        self._heating_temperature = heating
        self._auto_temperature = auto
        self._dry_temperature = dry
        self._current_mode_temperature = current_mode_temperature

        await cc.send()
        self._call_device_updated()

    def _call_read_current_status(self, run_from_init=False):

        async def read_current_status():
            if run_from_init:
                await asyncio.sleep(3)
            await self.read_status()

        asyncio.create_task(read_current_status())

    @property
    def is_on(self):
        return self._status == 1

    @property
    def available(self):
        """True once a real (non-sentinel) status frame has been observed."""
        return self._available

    @property
    def mode(self):
        return self._mode

    @property
    def fan_speed(self):
        return self._fan_speed

    @property
    def sweep(self):
        return self._sweep

    @property
    def current_temperature(self):
        return self._current_temperature

    @property
    def target_temperature(self):
        return self._current_mode_temperature

    @property
    def cooling_temperature(self):
        return self._cooling_temperature

    @property
    def heating_temperature(self):
        return self._heating_temperature

    @property
    def auto_temperature(self):
        return self._auto_temperature

    @property
    def dry_temperature(self):
        return self._dry_temperature

    @property
    def device_identifier(self):
        return f"{self._device_address}-{self._ac_number}"
