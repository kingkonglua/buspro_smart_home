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

        self.register_telegram_received_cb(self._telegram_received_cb)
        self._call_read_current_status(run_from_init=True)

    def _telegram_received_cb(self, telegram):
        if telegram.operate_code == OperateCode.CurtainSwitchControlResponse:
            if len(telegram.payload) >= 2:
                curtain_number = telegram.payload[0]
                status = telegram.payload[1]
                if curtain_number == self._curtain_number:
                    self._status = status
                    self._call_device_updated()

        elif telegram.operate_code == OperateCode.ReadStatusOfCurtainSwitchResponse:
            if len(telegram.payload) >= 2:
                curtain_number = telegram.payload[0]
                status = telegram.payload[1]
                if curtain_number == self._curtain_number:
                    self._status = status
                    self._call_device_updated()

        elif telegram.operate_code == OperateCode.BroadcastStatusOfCurtainSwitch:
            payload = telegram.payload
            length = len(payload)
            if length < 2:
                return
            size = length // 2
            levels = payload[:size]
            statuses = payload[size:]
            if self._curtain_number - 1 < len(levels):
                self._level = levels[self._curtain_number - 1]
            if self._curtain_number - 1 < len(statuses):
                self._status = statuses[self._curtain_number - 1]
            self._call_device_updated()

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

    def _call_read_current_status(self, run_from_init=False):

        async def read_current_status():
            if run_from_init:
                await asyncio.sleep(3)
            await self.read_status()

        asyncio.create_task(read_current_status())

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
    def level(self):
        return self._level

    @property
    def last_action(self):
        return self._last_action

    @property
    def device_identifier(self):
        return f"{self._device_address}-{self._curtain_number}"
