import asyncio
import logging

from .control import _ControlAcStatus, _ReadAcStatus
from .device import Device
from ..helpers.enums import *


DEFAULT_MODE_AND_FAN = 48
DEFAULT_TEMPERATURE = 22

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
        if len(payload) < 13:
            return
        # Filter messages for our AC unit only (when more than one unit is present)
        ac_number = payload[0]
        if self._ac_number is not None and ac_number != self._ac_number:
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
        self._sweep = payload[12]

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
        # Only apply an explicit power state change. Falling back to a hard
        # coded 0 when the status is unknown would send a full "OFF" frame
        # and switch the unit off on any unrelated control request.
        new_status = status
        if new_status is None:
            new_status = self._status
        if new_status is None:
            _LOGGER.warning(
                "AC '%s' power state unknown and no explicit status was requested; "
                "skipping control to avoid turning the unit off",
                self._name,
            )
            return
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
        if current_mode_temperature is None or mode_changed:
            current_mode_temperature = {
                AcMode.COOL.value: cooling,
                AcMode.HEAT.value: heating,
                AcMode.AUTO.value: auto,
                AcMode.DRY.value: dry,
                AcMode.FAN.value: 0,
            }.get(new_mode, DEFAULT_TEMPERATURE)

        cc = _ControlAcStatus(self._buspro)
        cc.subnet_id, cc.device_id = self._device_address
        cc.ac_number = self._ac_number if self._ac_number is not None else 1
        cc.temperature_type = self._temperature_type
        cc.current_temperature = self._current_temperature if self._current_temperature is not None else DEFAULT_TEMPERATURE
        cc.cooling_temperature = cooling
        cc.heating_temperature = heating
        cc.auto_temperature = auto
        cc.dry_temperature = dry
        cc.mode_and_fan = self._mode_and_fan
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
