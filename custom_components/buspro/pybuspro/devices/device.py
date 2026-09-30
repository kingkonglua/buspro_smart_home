import asyncio

from .control import _ReadStatusOfChannels


class Device(object):
    def __init__(self, buspro, device_address, name=""):
        # device_address = (subnet_id, device_id, ...)

        self._device_address = device_address
        self._buspro = buspro
        self._name = name
        self.device_updated_cbs = []
        # M-7: keep a strong reference so the fire-and-forget update task is
        # not garbage-collected before it runs.
        self._update_task = None

    @property
    def name(self):
        return self._name

    def register_telegram_received_cb(self, telegram_received_cb, postfix=None):
        self._buspro.register_telegram_received_device_cb(telegram_received_cb, self._device_address, postfix)

    def unregister_telegram_received_cb(self, telegram_received_cb, postfix=None):
        self._buspro.unregister_telegram_received_device_cb(telegram_received_cb, self._device_address, postfix)

    def register_device_updated_cb(self, device_updated_cb):
        """Register device updated callback."""
        self.device_updated_cbs.append(device_updated_cb)

    def unregister_device_updated_cb(self, device_updated_cb):
        """Unregister device updated callback."""
        if device_updated_cb in self.device_updated_cbs:
            self.device_updated_cbs.remove(device_updated_cb)

    async def _device_updated(self):
        for device_updated_cb in list(self.device_updated_cbs):
            try:
                await device_updated_cb(self)
            except Exception:  # noqa: BLE001
                self._buspro.logger.exception("Device-updated callback failed")

    async def _send_telegram(self, telegram):
        if self._buspro.network_interface is None:
            return
        await self._buspro.network_interface.send_telegram(telegram)

    # async def _send_control(self, control):
    #     await self._buspro.network_interface.send_control(control)

    def _call_device_updated(self):
        self._update_task = asyncio.ensure_future(self._device_updated())

    def _call_read_current_status_of_channels(self, run_from_init=False):

        async def read_current_state_of_channels():
            if run_from_init:
                await asyncio.sleep(3)

            read_status_of_channels = _ReadStatusOfChannels(self._buspro)
            read_status_of_channels.subnet_id, read_status_of_channels.device_id = self._device_address

            await read_status_of_channels.send()

        asyncio.create_task(read_current_state_of_channels())
