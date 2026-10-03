import asyncio

from .control import _CurtainControl, _ReadStatusOfCurtainSwitch
from .device import Device
from ..helpers.enums import *


class Curtain(Device):
    """HDL Buspro curtain / roller blind (Curtain Switch, operate code 0xE3E0)."""

    def __init__(self, buspro, device_address, curtain_number, name=""):
        super().__init__(buspro, device_address, name)
        # device_address = (subnet_id, device_id)

        self._buspro = buspro
        self._device_address = device_address
        self._curtain_number = curtain_number

        self._status = None       # 0=stopped, 1=opening, 2=closing (from module)
        self._level = None        # 0-100 position when reported by the module
        self._last_action = None  # action we requested last
        self._read_task = None    # F-C5/F-A4: strong ref to the startup read task

        # F-C1: No.=17 is the V1.113 position virtual channel. It is the only
        # channel whose Status byte is a 0-100 position; channels 1-16 report a
        # 0/1/2 discrete state.
        self._position_channel = (self._curtain_number == 17)
        self._position = None       # 0-100, only meaningful on the position channel
        self._status_source = None  # "control" / "read" / "broadcast"

        self.register_telegram_received_cb(self._telegram_received_cb)
        self._call_read_current_status(run_from_init=True)

    def _apply_reported_value(self, value):
        """Route a single reported value to the position or the discrete state.

        On the position channel 0xEE means "controller is measuring the travel,
        position unknown" and must be ignored; otherwise a 0-100 value is the
        position. On a normal channel the value is the 0/1/2 status.
        """
        if self._position_channel:
            if value == 0xEE:
                return
            if 0 <= value <= 100:
                self._position = value
        else:
            self._status = value

    def _telegram_received_cb(self, telegram):
        if telegram.payload is None:
            return

        if telegram.operate_code == OperateCode.CurtainSwitchControlResponse:
            # F-C1: this is the echo of OUR OWN control request, not proof the
            # motor reached a limit. Tag it so cover.py does not treat it as an
            # end state.
            if len(telegram.payload) >= 2:
                curtain_number = telegram.payload[0]
                if curtain_number == self._curtain_number:
                    self._status_source = "control"
                    self._apply_reported_value(telegram.payload[1])
                    self._call_device_updated()

        elif telegram.operate_code == OperateCode.ReadStatusOfCurtainSwitchResponse:
            if len(telegram.payload) >= 2:
                curtain_number = telegram.payload[0]
                if curtain_number == self._curtain_number:
                    self._status_source = "read"
                    self._apply_reported_value(telegram.payload[1])
                    self._call_device_updated()

        elif telegram.operate_code == OperateCode.BroadcastStatusOfCurtainSwitch:
            payload = telegram.payload
            length = len(payload)
            if length < 2:
                return
            size = length // 2
            levels = payload[:size]
            statuses = payload[size:]
            self._status_source = "broadcast"
            n = self._curtain_number
            # F-C6: bounds-check before indexing (previously [-1] on n=0, and
            # an unguarded read on a short/mis-sized broadcast).
            if 1 <= n <= len(levels):
                self._level = levels[n - 1]
            # F-C1 NC-4: a broadcast status is only ever 0/1/2; never feed it to
            # a position channel's 0-100 slot.
            if 1 <= n <= len(statuses) and not self._position_channel:
                self._status = statuses[n - 1]
            self._call_device_updated()

    async def set_position(self, level):
        """Directly set a 0-100 position on the No.=17 position channel.

        The second payload byte IS the percentage, so there is no separate stop
        action (0x00 would mean 0% = fully closed).
        """
        level = max(0, min(100, int(level)))
        cc = _CurtainControl(self._buspro)
        cc.subnet_id, cc.device_id = self._device_address
        cc.curtain_number = self._curtain_number
        cc.action = level
        await cc.send()

    async def open(self):
        await self._control(CurtainAction.OPEN)

    async def close(self):
        await self._control(CurtainAction.CLOSE)

    async def stop(self):
        await self._control(CurtainAction.STOP)

    async def read_status(self):
        rsoc = _ReadStatusOfCurtainSwitch(self._buspro)
        rsoc.subnet_id, rsoc.device_id = self._device_address
        rsoc.curtain_number = self._curtain_number
        await rsoc.send()

    async def _control(self, action):
        self._last_action = action
        cc = _CurtainControl(self._buspro)
        cc.subnet_id, cc.device_id = self._device_address
        cc.curtain_number = self._curtain_number
        cc.action = action
        await cc.send()
        # F-C5: same G8 ACK watch the light/switch platforms use. STOP/OPEN/
        # CLOSE are absolute actions, so a dropped frame is safely resent.
        self._start_ack_watch(cc)

    def _call_read_current_status(self, run_from_init=False):
        # F-C5: never schedule a coroutine without a running loop (setup /
        # teardown / reconnect), same guard as device.py.
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return

        async def read_current_status():
            try:
                if run_from_init:
                    await asyncio.sleep(3)
                for attempt in range(4):
                    if self._got_initial_status:
                        return
                    await self.read_status()
                    if self._got_initial_status:
                        return
                    await asyncio.sleep(min(1.0 * (2 ** attempt), 4.0))
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                self._buspro.logger.debug("Curtain startup read failed")

        self._read_task = asyncio.create_task(read_current_status())

    @property
    def is_open(self):
        return self._status == CurtainAction.OPEN.value

    @property
    def is_closed(self):
        return self._status == CurtainAction.CLOSE.value

    @property
    def is_moving(self):
        return self._status is not None and self._status != CurtainAction.STOP.value

    @property
    def status(self):
        return self._status

    @property
    def status_source(self):
        # F-C1: cover.py uses this to distinguish our own control echo from a
        # genuine end state reported by the wall switch / bus.
        return self._status_source

    @property
    def is_position_channel(self):
        return self._position_channel

    @property
    def position(self):
        return self._position

    @property
    def level(self):
        return self._level

    @property
    def last_action(self):
        return self._last_action

    @property
    def device_identifier(self):
        return f"{self._device_address}-{self._curtain_number}"
