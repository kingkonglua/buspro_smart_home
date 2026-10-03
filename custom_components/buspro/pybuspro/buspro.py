"""Top-level Buspro client object."""
from __future__ import annotations

import asyncio
import logging
import time
from collections import deque

from .helpers.enums import OperateCode
from .transport.network_interface import NetworkInterface
# Imported last: the devices package pulls in core.telegram/helpers, so it
# must load only after helpers has finished initialising (avoids a cycle).
from .devices.device import FrameFreshnessGuard

# Upper bound on how many distinct foreign source IPs are remembered as
# "dropped". Keeps the diagnostics set from growing without bound when an L2
# neighbour cycles through source addresses.
MAX_DROPPED_SOURCE_IPS = 64


class StateUpdater:
    """Periodic state refresher (optional)."""

    def __init__(self, buspro: "Buspro", sleep: int = 10) -> None:
        self.buspro = buspro
        self.run_forever = True
        self.run_task: asyncio.Task | None = None
        self.sleep = sleep

    async def start(self) -> None:
        """Start the periodic loop."""
        self.run_task = self.buspro.loop.create_task(self.run())

    async def run(self) -> None:
        """Run the periodic sync."""
        await asyncio.sleep(0)
        self.buspro.logger.info(
            "Starting StateUpdater with %s seconds interval", self.sleep
        )
        while True:
            await asyncio.sleep(self.sleep)
            await self.buspro.sync()


class Buspro:
    """Client for an HDL Buspro gateway."""

    def __init__(
        self,
        gateway_address_send_receive,
        loop_=None,
    ) -> None:
        """Initialize the Buspro client."""
        self.loop = loop_ or asyncio.get_event_loop()
        self.state_updater: StateUpdater | None = None
        self.started = False
        self.network_interface: NetworkInterface | None = None
        self.logger = logging.getLogger("buspro.log")
        self.telegram_logger = logging.getLogger("buspro.telegram")

        self.callback_all_messages = None
        self._telegram_received_cbs: list[dict] = []

        # BUG-7: drop late/reordered duplicates before they can write state.
        # See FrameFreshnessGuard for why arrival order + a local receive
        # window is the only ordering evidence HDL telegrams offer.
        self._frame_freshness = FrameFreshnessGuard()

        # Optional hook fired when the UDP transport is lost unexpectedly
        # (not on a clean stop()). Set by the integration's gateway wrapper.
        self.on_connection_lost = None

        # F-G1: liveness evidence. ``last_rx_monotonic`` is positive proof the
        # gateway answered (set on every inbound frame); ``_send_failures`` is
        # a weaker auxiliary signal bumped by the transport on send errors.
        self.last_rx_monotonic: float | None = None
        self._send_failures = 0

        # Source-IP allowlist for incoming UDP frames. When non-empty, the
        # network interface drops datagrams from any other IP, so telegrams
        # broadcast by *other* HDL gateways/software on the same L2 segment
        # cannot pollute this instance with phantom devices. Populated by the
        # integration's setup path after resolving the configured host.
        self.allowed_source_ips: set[str] = set()
        # Source IPs whose frames have been dropped by the filter above, so
        # each is reported once and can be surfaced in diagnostics. Bounded:
        # only the most recent MAX_DROPPED_SOURCE_IPS are remembered (see
        # _dropped_source_ip_order), otherwise an L2 neighbour that cycles
        # source addresses would grow this set forever.
        self.dropped_source_ips: set[str] = set()
        self._dropped_source_ip_order: deque[str] = deque()

        # The IP this client advertises inside every outbound telegram's
        # 4-byte header (see TelegramHelper.build_send_buffer). Left None the
        # helper falls back to the historical 192.168.1.15 constant, so this
        # is strictly an improvement when set.
        self.advertised_ip: str | None = None

        self.gateway_address_send_receive = gateway_address_send_receive

    def __del__(self):
        # Do NOT run coroutines from __del__: calling run_until_complete()
        # here can conflict with an already running/closed event loop.
        try:
            self.started = False
        except Exception:
            pass

    async def start(self, state_updater: bool = False) -> None:
        """Connect to the gateway and start listening for telegrams."""
        self.network_interface = NetworkInterface(
            self, self.gateway_address_send_receive
        )
        self.network_interface.register_callback(self._callback_all_messages)
        await self.network_interface.start()

        if state_updater:
            self.state_updater = StateUpdater(self)
            await self.state_updater.start()

        self.started = True

    async def stop(self) -> None:
        """Disconnect from the gateway."""
        await self._stop_network_interface()
        self.started = False

    def _callback_all_messages(self, telegram) -> None:
        """Invoke per-device callbacks for an incoming telegram."""
        if telegram is None:
            return
        # F-G1: any inbound frame is positive evidence the gateway is alive.
        self.last_rx_monotonic = time.monotonic()
        self.telegram_logger.debug(telegram)

        if self.callback_all_messages is not None:
            self.callback_all_messages(telegram)

        # Every per-device handler decodes a *Response / *Broadcast frame,
        # i.e. state the device reports about ITSELF, so only the sender's
        # handlers may see it. Matching the target address too meant a reply
        # sent TO a device was decoded as that device's own state.
        source = telegram.source_address
        if source is not None:
            source = tuple(source)

        # BUG-7 freshness guard: a frame that repeats the operate code of the
        # frame that immediately preceded it from this source, within the
        # reorder window, is a retransmit/reordered duplicate. Drop it so it
        # cannot overwrite newer state, and do not fire any device-updated
        # callback for it.  Payload values are deliberately NOT inspected.
        if self._frame_freshness.is_stale(source, telegram.operate_code):
            self.telegram_logger.debug(
                "Dropped stale/reordered frame from %s op=%s",
                source,
                telegram.operate_code,
            )
            return

        handled = False
        for cb in list(self._telegram_received_cbs):
            device_address = cb["device_address"]
            if device_address is not None and tuple(device_address) == source:
                if telegram.operate_code is not OperateCode.TIME_IF_FROM_LOGIC_OR_SECURITY:
                    postfix = cb.get("postfix")
                    try:
                        if postfix is not None:
                            cb["callback"](telegram, postfix)
                        else:
                            cb["callback"](telegram)
                        handled = True
                    except Exception as err:  # noqa: BLE001
                        self.logger.warning("Telegram callback error: %s", err)
        if handled:
            # Only a frame a device actually applied may arm the guard: a
            # frame that raised (e.g. payload=None) or matched no handler must
            # not suppress the next legitimate frame.
            self._frame_freshness.mark_handled(source, telegram.operate_code)

    def _notify_send_failure(self) -> None:
        """Record a transport send failure (auxiliary liveness signal)."""
        self._send_failures += 1

    def _notify_send_success(self) -> None:
        """Clear the send-failure counter after a successful send."""
        self._send_failures = 0

    def reset_send_failures(self) -> None:
        """Clear the send-failure counter (used after acting on it)."""
        self._send_failures = 0

    def _notify_connection_lost(self) -> None:
        """Called by the transport when the socket dies unexpectedly."""
        if self.on_connection_lost is None:
            return
        try:
            self.on_connection_lost()
        except Exception as err:  # noqa: BLE001
            self.logger.warning("on_connection_lost callback error: %s", err)

    def note_dropped_source_ip(self, ip: str) -> bool:
        """Record a dropped foreign source IP, evicting the oldest if full.

        Returns True when this is the first time this IP is seen, so the caller
        can log it exactly once.
        """
        if not ip or ip in self.dropped_source_ips:
            return False
        self.dropped_source_ips.add(ip)
        self._dropped_source_ip_order.append(ip)
        while len(self._dropped_source_ip_order) > MAX_DROPPED_SOURCE_IPS:
            oldest = self._dropped_source_ip_order.popleft()
            self.dropped_source_ips.discard(oldest)
        return True

    async def _stop_network_interface(self) -> None:
        if self.network_interface is not None:
            await self.network_interface.stop()
            self.network_interface = None

    def register_telegram_received_all_messages_cb(self, telegram_received_cb) -> None:
        """Register a global telegram callback."""
        self.callback_all_messages = telegram_received_cb

    def register_telegram_received_device_cb(
        self, telegram_received_cb, device_address, postfix=None
    ) -> None:
        """Register a per-device telegram callback."""
        self._telegram_received_cbs.append(
            {
                "callback": telegram_received_cb,
                "device_address": device_address,
                "postfix": postfix,
            }
        )

    def unregister_telegram_received_device_cb(
        self, telegram_received_cb, device_address, postfix=None
    ) -> None:
        """Unregister a per-device telegram callback."""
        try:
            self._telegram_received_cbs.remove(
                {
                    "callback": telegram_received_cb,
                    "device_address": device_address,
                    "postfix": postfix,
                }
            )
        except ValueError:
            pass

    @staticmethod
    async def sync():  # pragma: no cover - kept for API parity
        """Hook for the optional StateUpdater."""
        raise NotImplementedError
