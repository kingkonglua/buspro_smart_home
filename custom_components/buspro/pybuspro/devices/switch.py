from .control import _ReadStatusOfChannels, _SingleChannelControl
from .device import Device
from ..helpers.enums import *
from ..helpers.generics import Generics


class Switch(Device):
    def __init__(self, buspro, device_address, channel_number, name="", delay_read_current_state_seconds=0):
        super().__init__(buspro, device_address, name)
        # device_address = (subnet_id, device_id, channel_number)

        self._buspro = buspro
        self._device_address = device_address
        self._channel = channel_number
        self._brightness = 0
        self.register_telegram_received_cb(self._telegram_received_cb)
        self._call_read_current_status_of_channels(run_from_init=True)

    def _telegram_received_cb(self, telegram):
        if telegram.operate_code == OperateCode.SingleChannelControlResponse:
            channel = telegram.payload[0]
            # success = telegram.payload[1]
            brightness = telegram.payload[2]
            if channel == self._channel:
                self._brightness = brightness
                self._call_device_updated()
        elif telegram.operate_code == OperateCode.ReadStatusOfChannelsResponse:
            if self._channel <= telegram.payload[0]:
                self._brightness = telegram.payload[self._channel]
                self._call_device_updated()
        elif telegram.operate_code == OperateCode.SceneControlResponse:
            self._call_read_current_status_of_channels()

    async def set_on(self):
        intensity = 100
        await self._set(intensity, 0)

    async def set_off(self):
        intensity = 0
        await self._set(intensity, 0)

    async def read_status(self):
        """Request a fresh read of this channel's current status.

        A single on-demand 0xE0 ReadStatusOfChannels request -- the same
        control ``Device._call_read_current_status_of_channels()`` uses, but
        awaited directly so the post-reconnect resync can await it and catch
        failures (no fire-and-forget task, no startup delay).
        """
        reader = _ReadStatusOfChannels(self._buspro)
        reader.subnet_id, reader.device_id = self._device_address
        await reader.send()

    @property
    def supports_brightness(self):
        return False

    @property
    def is_on(self):
        if self._brightness == 0:
            return False
        else:
            return True

    @property
    def device_identifier(self):
        return f"{self._device_address}-{self._channel}"

    async def _set(self, intensity, running_time_seconds):
        self._brightness = intensity

        generics = Generics()
        (minutes, seconds) = generics.calculate_minutes_seconds(running_time_seconds)

        scc = _SingleChannelControl(self._buspro)
        scc.subnet_id, scc.device_id = self._device_address
        scc.channel_number = self._channel
        scc.channel_level = intensity
        scc.running_time_minutes = minutes
        scc.running_time_seconds = seconds
        await scc.send()
        # G8: resend once if the applied SingleChannelControlResponse is lost.
        self._start_ack_watch(scc)
