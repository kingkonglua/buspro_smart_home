import asyncio
import time

from .control import _ReadStatusOfChannels


# HDL Buspro UDP telegrams carry no monotonic sequence number: the header is
# only (advertised IP, "HDLMIRACLE", 0xAAAA), then length/source/device-type/
# operate-code/target/payload/CRC.  There is therefore no in-frame way to tell
# which of two frames was *sent* first.  The only ordering evidence a receiver
# has is arrival order plus its own receive clock, which is what this guard
# uses.
#
# A frame is treated as a retransmit / late reordered duplicate -- and dropped
# -- only when it repeats the operate code of the frame that *immediately*
# preceded it from the same source, and arrives within this window.  Two
# things make this safe to apply to every device:
#   * it is keyed on source address + operate code, never on payload values
#     (a temperature reading is allowed to fall, a switch is allowed to flip);
#   * any frame with a *different* operate code in between proves time moved
#     forward, so the next same-code frame is still accepted.
# 50 ms is far below the interval between two genuine HDL status updates but
# far above LAN reordering jitter, so normal traffic is untouched.
FRAME_REORDER_WINDOW_SECONDS = 0.05


class FrameFreshnessGuard:
    """Generic BUG-7 guard against stale/reordered frames overwriting state.

    Kept in the device base module so every pybuspro client can share one
    implementation; the dispatcher owns an instance because it is the single
    choke point every inbound telegram passes through (and the point where a
    frame is accepted *only if a device callback actually handled it* -- a
    frame that merely raised or was ignored must not arm the guard).
    """

    def __init__(self, window=FRAME_REORDER_WINDOW_SECONDS, clock=None):
        self._window = window
        self._clock = clock or time.monotonic
        self._last_op = {}
        self._last_time = {}

    def is_stale(self, source, operate_code):
        """True if this frame is a late duplicate of the preceding one."""
        if source is None or operate_code is None:
            return False
        last_op = self._last_op.get(source)
        last_time = self._last_time.get(source)
        if last_op is None or last_time is None or last_op != operate_code:
            return False
        return (self._clock() - last_time) < self._window

    def mark_handled(self, source, operate_code):
        """Remember the frame that was actually applied to device state."""
        if source is None:
            return
        self._last_op[source] = operate_code
        self._last_time[source] = self._clock()

    def reset(self):
        self._last_op.clear()
        self._last_time.clear()


class Device(object):
    def __init__(self, buspro, device_address, name=""):
        # device_address = (subnet_id, device_id, ...)

        self._device_address = device_address
        self._buspro = buspro
        self._name = name
        self.device_updated_cbs = []
        # Strong references to the telegram callbacks this device registered
        # with its Buspro client, so device teardown can detach exactly those
        # (otherwise Buspro._telegram_received_cbs grows without bound and
        # pins every Device object it holds).
        self._telegram_received_cbs = []
        # M-7: keep a strong reference so the fire-and-forget update task is
        # not garbage-collected before it runs.
        self._update_task = None

    @property
    def name(self):
        return self._name

    def register_telegram_received_cb(self, telegram_received_cb, postfix=None):
        self._buspro.register_telegram_received_device_cb(telegram_received_cb, self._device_address, postfix)
        entry = (telegram_received_cb, postfix)
        if entry not in self._telegram_received_cbs:
            self._telegram_received_cbs.append(entry)

    def unregister_telegram_received_cb(self, telegram_received_cb, postfix=None):
        self._buspro.unregister_telegram_received_device_cb(telegram_received_cb, self._device_address, postfix)
        try:
            self._telegram_received_cbs.remove((telegram_received_cb, postfix))
        except ValueError:
            pass

    def unregister_all_telegram_received_cbs(self):
        """Detach every telegram callback this device registered on the bus.

        Called on device teardown (entity removal) so the owning Buspro
        client's ``_telegram_received_cbs`` list stays bounded by the number of
        live devices instead of every device ever created.
        """
        for telegram_received_cb, postfix in list(self._telegram_received_cbs):
            self._buspro.unregister_telegram_received_device_cb(
                telegram_received_cb, self._device_address, postfix
            )
        self._telegram_received_cbs.clear()

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
        # BUG-6: device callbacks can fire before the event loop is running (or
        # after it has closed) during setup / teardown / reconnect. Scheduling a
        # coroutine then raises RuntimeError, which used to bubble out of the
        # telegram dispatch path and silently drop the state update. Degrade
        # safely instead of breaking frame distribution.
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            self._buspro.logger.debug(
                "No running event loop; skipping device-updated broadcast"
            )
            return
        self._update_task = asyncio.ensure_future(self._device_updated())

    def _call_read_current_status_of_channels(self, run_from_init=False):
        # BUG-6: same guard as _call_device_updated. This is called from
        # Light/Switch __init__ (run_from_init=True) and from the scene-callback
        # dispatch path, both of which can run without an active loop.
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            self._buspro.logger.debug(
                "No running event loop; skipping channel status read"
            )
            return

        async def read_current_state_of_channels():
            if run_from_init:
                await asyncio.sleep(3)

            read_status_of_channels = _ReadStatusOfChannels(self._buspro)
            read_status_of_channels.subnet_id, read_status_of_channels.device_id = self._device_address

            await read_status_of_channels.send()

        asyncio.create_task(read_current_state_of_channels())
