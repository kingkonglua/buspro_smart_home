"""HDL Buspro bus discovery.

The HDL bus has no "list yourself" command, but every telegram a device puts on
the wire carries its own source subnet, device id and device type in the header,
and reply telegrams carry useful detail (e.g. a channel-status reply states how
many channels the device has). So discovery here is *passive harvesting with
active provocation*:

1. Hook the catch-all telegram callback so we see everything on the bus.
2. Repeatedly broadcast a spread of read requests at 255.255 across the whole
   listening window, to make every class of quiet device reply at least once.
3. Record every unique (subnet, device) we hear, keeping the raw device-type
   code, the operate codes it answered with, and the channel count when a
   channel-status reply reveals it.

The raw type code and operate codes are captured even for hardware we don't
recognise, so unknown modules still surface and can be identified from what
they replied with (and mapped later).

Compared to the marsh4200 reference this file additionally recognises
air-conditioner modules (reply 0x1939 to our ReadAcStatus probe) and curtain
modules (reply 0xE3E3), which our integration drives with dedicated drivers
(pybuspro/devices/ac.py / curtain.py).
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

from .const import (
    DEVICE_TYPE_AC,
    DEVICE_TYPE_BINARY_SENSOR,
    DEVICE_TYPE_CLIMATE,
    DEVICE_TYPE_CURTAIN,
    DEVICE_TYPE_LIGHT,
    DEVICE_TYPE_SENSOR,
    DEVICE_TYPE_SWITCH,
)
from .pybuspro.buspro import Buspro
from .pybuspro.core.telegram import Telegram
from .pybuspro.helpers.enums import OperateCode

_LOGGER = logging.getLogger(__name__)

# Address every HDL device on the bus listens to.
_BROADCAST: tuple[int, int] = (255, 255)

# Source addresses we should never treat as a real device:
#   (200, 200) is pybuspro's own default sender id
#   (0, 0)     is a null/unset address
#   (255, 255) is the broadcast address itself
_IGNORED_SOURCES = {(200, 200), (0, 0), (255, 255)}

# Broadcast read requests used to provoke replies, spanning the common device
# classes. operate_code may be an OperateCode enum or a raw 2-byte sequence
# (build_send_buffer accepts both). A device that answers any of these is
# discovered; the operate code it answers with also tells us what it is:
#   0x0033 ReadStatusOfChannels    -> relays / dimmers reply 0x0034 (+ channel count)
#   0x1645 ReadSensorStatus        -> sensors reply 0x1646
#   0x1604 ReadSensorsInOneStatus  -> sensors-in-one reply 0x1605
#   0x1944 ReadFloorHeatingStatus  -> floor heating / DLP reply 0x1945
#   0x15CE ReadDryContactStatus    -> dry-contact modules reply 0x15CF
#   0xE018 ReadStatusOfUniversalSwitch -> universal-switch modules reply 0xE019
#   0xE3E2 ReadStatusOfCurtainSwitch   -> curtain modules reply 0xE3E3
#   0x1938 ReadAcStatus            -> AC modules reply 0x1939 (ours, marsh lacks this)
#   0x000E "read device info/type" -> canonical HDL discovery poke -> 0x000F
_PROVOCATIONS: tuple[tuple[object, list[int]], ...] = (
    (OperateCode.ReadStatusOfChannels, []),
    (OperateCode.ReadSensorStatus, []),
    (OperateCode.ReadSensorsInOneStatus, []),
    (OperateCode.ReadFloorHeatingStatus, []),
    (OperateCode.ReadDryContactStatus, [1, 1]),
    (OperateCode.ReadStatusOfUniversalSwitch, [1]),
    (OperateCode.ReadStatusOfCurtainSwitch, [1]),
    (OperateCode.ReadStatusOfCurtainSwitch, [2]),
    (OperateCode.ReadAcStatus, []),          # ← 新增：空调探测
    (OperateCode.ReadDeviceInfo, []),          # 通用设备探测
)

# Raw device-type bytes live at these offsets in the received UDP buffer.
_TYPE_HI = 19
_TYPE_LO = 20

# How often to re-send the provocation round while listening, in seconds.
_POLL_INTERVAL = 2.5
# Gap between individual frames within one round, in seconds.
_FRAME_GAP = 0.05
# Directed channel-read follow-up: rounds, and listen time after each round.
_DIRECTED_ROUNDS = 2
_DIRECTED_LISTEN = 2.0
# Hard ceiling for the whole directed phase, regardless of device count, so a
# large bus can't make the scan run away. Public so callers (e.g. the config
# flow's progress display) can size their own timeouts/estimates around it
# instead of hardcoding a second copy of this number.
DIRECTED_PHASE_MAX_SECONDS = 20.0
_DIRECTED_MAX_SECONDS = DIRECTED_PHASE_MAX_SECONDS
# Typical directed-phase duration on a normal-sized bus: two rounds, each
# followed by a fixed listen pause, regardless of how many devices answered.
DIRECTED_PHASE_TYPICAL_SECONDS = _DIRECTED_ROUNDS * _DIRECTED_LISTEN
# Extra time (beyond the user's listen duration) that scan() may need for the
# directed phase. The config flow's watchdog timeout must allow for this.
SCAN_TIMEOUT_MARGIN = DIRECTED_PHASE_MAX_SECONDS + 5.0

# Device classification by *reply* operate code (raw bytes), as specified by
# the task spec. 'curtain' and 'ac' are ours -- marsh cannot identify either.
# These are *classification* types: the config flow converts 'curtain' to a
# cover with subtype curtain_module and 'ac' to a climate with subtype ac.
DEVICE_TYPE_BY_RESPONSE: dict[bytes, str] = {
    b"\x00\x34": DEVICE_TYPE_SWITCH,        # 继电器/调光器通道
    b"\x16\x46": DEVICE_TYPE_SENSOR,        # 传感器
    b"\x16\x05": DEVICE_TYPE_SENSOR,        # 多功能传感器
    b"\x19\x45": DEVICE_TYPE_CLIMATE,       # 地暖/温控
    b"\x15\xCF": DEVICE_TYPE_BINARY_SENSOR, # 干接点
    b"\xE0\x19": DEVICE_TYPE_BINARY_SENSOR, # 万能开关
    b"\xE3\xE3": DEVICE_TYPE_CURTAIN,       # 窗帘 ← 我们的
    b"\x19\x39": DEVICE_TYPE_AC,            # 空调 ← 我们的
    b"\x00\x0F": "unknown",                 # 未知设备
}

# Friendly labels for the classification types (config-flow checklist).
SCAN_TYPE_LABELS: dict[str, str] = {
    DEVICE_TYPE_SWITCH: "继电器通道",
    DEVICE_TYPE_LIGHT: "调光器通道",
    DEVICE_TYPE_SENSOR: "传感器",
    DEVICE_TYPE_CLIMATE: "地暖/温控",
    DEVICE_TYPE_AC: "空调",
    DEVICE_TYPE_CURTAIN: "窗帘",
    DEVICE_TYPE_BINARY_SENSOR: "干接点/万能开关",
    "unknown": "未知设备",
}

# Same table keyed by friendly operate-code name, derived from the enum so the
# two can never drift apart. Discovered devices record op-code *names*, so the
# classifier looks entries up here first.
_OPERATE_CODE_NAMES = {oc.value: oc.name for oc in OperateCode}
DEVICE_TYPE_BY_RESPONSE_NAME: dict[str, str] = {
    _OPERATE_CODE_NAMES[code]: dtype
    for code, dtype in DEVICE_TYPE_BY_RESPONSE.items()
    if code in _OPERATE_CODE_NAMES
}

# Operate codes a keypad/panel *originates* when a button is pressed or when
# it broadcasts state. Hearing these from a source is keypad evidence.
_KEYPAD_COMMAND_OPS = {
    "SingleChannelControl",
    "SceneControl",
    "UniversalSwitchControl",
    "BroadcastStatusOfUniversalSwitch",
}

# Reply operate codes only a load/sensor device sends. Any of these from a
# source rules out "pure keypad".
_LOAD_RESPONSE_OPS = {
    "ReadStatusOfChannelsResponse",
    "SingleChannelControlResponse",
    "ReadSensorStatusResponse",
    "ReadSensorsInOneStatusResponse",
    "BroadcastSensorStatusResponse",
    "BroadcastSensorStatusAutoResponse",
    "ReadFloorHeatingStatusResponse",
    "ReadDryContactStatusResponse",
    "ReadStatusOfUniversalSwitchResponse",
    "ReadStatusOfCurtainSwitchResponse",
    "CurtainSwitchControlResponse",
    "ReadAcStatusResponse",
}


@dataclass
class DiscoveredDevice:
    """A single device heard on the bus during a scan."""

    subnet_id: int
    device_id: int
    type_code: str  # e.g. "0x026D"
    type_name: str  # friendly DeviceType name, or "Unknown"
    channel_count: int | None = None  # learned from a channel-status reply
    op_codes: set[str] = field(default_factory=set)  # operate codes heard from it
    # True once any channel-status reply carried an intermediate level.
    # Relays only ever report 0, 100 or 255 depending on firmware, so any
    # other value is hard evidence the module is a dimmer (works for both
    # 0-100 and 0-255 level scales).
    dimmer_evidence: bool = False

    @property
    def address(self) -> str:
        """Return the bus address as 'subnet.device'."""
        return f"{self.subnet_id}.{self.device_id}"

    @property
    def looks_like_keypad(self) -> bool:
        """Return True when the traffic pattern says 'wall panel / keypad'.

        Keypads originate control telegrams (single-channel, scene, universal
        switch) toward other devices but never answer a channel-status read
        themselves. So: command ops seen, no load-style replies, and no channel
        count learned even after the directed read phase.
        """
        return (
            self.channel_count is None
            and bool(self.op_codes & _KEYPAD_COMMAND_OPS)
            and not (self.op_codes & _LOAD_RESPONSE_OPS)
        )

    @property
    def key(self) -> str:
        """Return a stable selection key for the options-flow checklist."""
        return f"{self.subnet_id}-{self.device_id}"

    def summary(self) -> str:
        """One-line summary for logs."""
        chans = f"{self.channel_count}ch" if self.channel_count else "?ch"
        ops = ",".join(sorted(self.op_codes)) or "none"
        hints = []
        if self.dimmer_evidence:
            hints.append("dimmer")
        if self.looks_like_keypad:
            hints.append("keypad")
        hint = f" [{'/'.join(hints)}]" if hints else ""
        return (
            f"{self.address} type={self.type_code}({self.type_name}) "
            f"{chans}{hint} replied=[{ops}]"
        )


def infer_device_type(dev: DiscoveredDevice) -> str | None:
    """Classify a discovered device into a scan classification type.

    Reply operate codes are the most reliable signal (they work even when the
    raw type code isn't in any table); fall back to the raw type-code map.
    Returns None when the traffic pattern says 'keypad only' -- there is no
    controllable entity behind such a module worth importing.

    Classification types are our integration types except 'curtain' and 'ac'
    (the config flow maps those to cover/curtain_module and climate/ac).
    AC (0x1939) and curtain (0xE3E3) are checked before the generic channel
    branch: both kinds of hardware sometimes *also* answer ReadStatusOfChannels
    and would otherwise land on plain switch.
    """
    ops = dev.op_codes

    # 空调：回答了 ReadAcStatus 探测（marsh 不识别，我们的核心增量）。
    if "ReadAcStatusResponse" in ops:
        return DEVICE_TYPE_AC
    # 窗帘：回答窗帘状态读数或回声控制指令（marsh 有操作码但没有设备类）。
    if (
        "ReadStatusOfCurtainSwitchResponse" in ops
        or "CurtainSwitchControlResponse" in ops
    ):
        return DEVICE_TYPE_CURTAIN
    if (
        "ReadSensorStatusResponse" in ops
        or "ReadSensorsInOneStatusResponse" in ops
    ):
        return DEVICE_TYPE_SENSOR
    if "ReadFloorHeatingStatusResponse" in ops:
        return DEVICE_TYPE_CLIMATE
    if "ReadDryContactStatusResponse" in ops:
        return DEVICE_TYPE_BINARY_SENSOR
    if "ReadStatusOfUniversalSwitchResponse" in ops:
        return DEVICE_TYPE_BINARY_SENSOR
    if dev.looks_like_keypad:
        return None
    if (
        "ReadStatusOfChannelsResponse" in ops
        or "SingleChannelControlResponse" in ops
    ):
        # Channel device. Intermediate brightness levels are dimmer evidence;
        # otherwise treat as a plain switch (user can flip the type later).
        return DEVICE_TYPE_LIGHT if dev.dimmer_evidence else DEVICE_TYPE_SWITCH
    # Nothing conclusive from replies: treat as a plain switch so the module
    # still surfaces and the user can flip its type afterwards.
    return DEVICE_TYPE_SWITCH


class BusScanner:
    """Harvest devices from an HDL Buspro bus using a live Buspro client."""

    def __init__(self, buspro: Buspro) -> None:
        """Initialize the scanner around an already-connected Buspro client."""
        self._buspro = buspro
        self._found: dict[tuple[int, int], DiscoveredDevice] = {}

    # ----- telegram harvesting --------------------------------------------
    def _on_telegram(self, telegram) -> None:
        """Record the source and detail of any telegram seen during the scan."""
        try:
            if telegram is None:
                return

            # Only harvest telegrams that arrived from the configured gateway.
            # Older revisions of this library don't track allowed source IPs;
            # getattr keeps us compatible either way.
            allowed = getattr(self._buspro, "allowed_source_ips", None)
            udp_addr = getattr(telegram, "udp_address", None)
            if allowed and udp_addr and udp_addr[0] not in allowed:
                _LOGGER.debug(
                    "Scan ignoring telegram from foreign gateway %s", udp_addr[0]
                )
                return

            src = getattr(telegram, "source_address", None)
            if not src:
                return
            subnet, device = int(src[0]), int(src[1])
            if (subnet, device) in _IGNORED_SOURCES:
                return

            type_code, type_name = self._extract_type(telegram)
            dev = self._found.get((subnet, device))
            if dev is None:
                dev = DiscoveredDevice(
                    subnet_id=subnet,
                    device_id=device,
                    type_code=type_code,
                    type_name=type_name,
                )
                self._found[(subnet, device)] = dev
            elif dev.type_name == "Unknown" and type_name != "Unknown":
                # Upgrade if we previously only had an unknown type.
                dev.type_code = type_code
                dev.type_name = type_name

            op_name = self._op_name(telegram)
            if op_name:
                dev.op_codes.add(op_name)

            payload = getattr(telegram, "payload", None) or []
            # A channel-status reply tells us how many channels the device has:
            # payload[0] = channel count, payload[1..N] = per-channel levels.
            if op_name == "ReadStatusOfChannelsResponse":
                if payload:
                    count = int(payload[0])
                    if 1 <= count <= 64:
                        dev.channel_count = max(dev.channel_count or 0, count)
                    # payload[1..N] are per-channel levels. Relays report only
                    # 0/100 (255 on some firmware); an intermediate value can
                    # only come from a dimmer, so remember it.
                    for raw in payload[1 : 1 + min(count, 64)]:
                        try:
                            level = int(raw)
                        except (TypeError, ValueError):
                            continue
                        if 1 <= level <= 254 and level != 100:
                            dev.dimmer_evidence = True
                            break
            # A SingleChannelControlResponse also carries the resulting level
            # (payload: channel, success, level) - harvest it for the same
            # dimmer evidence, since it fires whenever a keypad dims a light.
            elif op_name == "SingleChannelControlResponse":
                if len(payload) >= 3:
                    try:
                        level = int(payload[2])
                    except (TypeError, ValueError):
                        level = -1
                    if 1 <= level <= 254 and level != 100:
                        dev.dimmer_evidence = True
            # Curtain replies (payload: curtain_number, status). The highest
            # curtain number that answers tells us how many curtains the
            # module drives, so import can split them into separate covers.
            elif op_name in (
                "ReadStatusOfCurtainSwitchResponse",
                "CurtainSwitchControlResponse",
            ):
                if payload:
                    try:
                        curtain_no = int(payload[0])
                    except (TypeError, ValueError):
                        curtain_no = 0
                    if 1 <= curtain_no <= 32:
                        dev.channel_count = max(
                            dev.channel_count or 0, curtain_no
                        )
            # AC replies (payload[0] = AC unit number, then 12 status bytes).
            # The highest unit number that answers tells us how many AC units
            # the module controls, so import can create one climate entity per
            # unit (our AC driver keys state off this very field).
            elif op_name == "ReadAcStatusResponse":
                if payload:
                    try:
                        ac_number = int(payload[0])
                    except (TypeError, ValueError):
                        ac_number = 0
                    if 1 <= ac_number <= 32:
                        dev.channel_count = max(
                            dev.channel_count or 0, ac_number
                        )
        except Exception as err:  # noqa: BLE001 - never let parsing kill a scan
            _LOGGER.debug("Scan telegram parse error: %s", err)

    @staticmethod
    def _extract_type(telegram) -> tuple[str, str]:
        """Pull the raw type code and a friendly name from a telegram."""
        type_code = "0x0000"
        udp = getattr(telegram, "udp_data", None)
        if udp is not None and len(udp) > _TYPE_LO:
            type_code = f"0x{udp[_TYPE_HI]:02X}{udp[_TYPE_LO]:02X}"
        source_device_type = getattr(telegram, "source_device_type", None)
        type_name = getattr(source_device_type, "name", None) or "Unknown"
        return type_code, type_name

    @staticmethod
    def _op_name(telegram) -> str | None:
        """Return the friendly operate-code name, or a raw hex string."""
        oc = getattr(telegram, "operate_code", None)
        name = getattr(oc, "name", None)
        if name:
            return name
        udp = getattr(telegram, "udp_data", None)
        if udp is not None and len(udp) > 22:
            return f"0x{udp[21]:02X}{udp[22]:02X}"
        return None

    # ----- scan orchestration ---------------------------------------------
    async def scan(self, duration: float = 15.0) -> list[DiscoveredDevice]:
        """Run a discovery scan and return the devices found, sorted by address."""
        buspro = self._buspro
        if buspro is None or buspro.network_interface is None:
            raise RuntimeError("gateway_unavailable")

        duration = max(float(duration), 1.0)
        previous_cb = buspro.callback_all_messages
        buspro.register_telegram_received_all_messages_cb(self._on_telegram)
        try:
            # Phase 1 - broadcast discovery: poke the bus repeatedly across the
            # whole window so every class of device replies or is overheard.
            elapsed = 0.0
            while elapsed < duration:
                await self._broadcast_provocations()
                step = min(_POLL_INTERVAL, duration - elapsed)
                if step <= 0:
                    break
                await asyncio.sleep(step)
                elapsed += step
            # Phase 2 - directed channel reads: many relay/dimmer modules ignore
            # a broadcast channel-status read and only answer one addressed to
            # them directly. Ask each device we found for its channel status so
            # the channel count comes back.
            await self._directed_channel_reads()
        finally:
            # Restore the previous catch-all so normal operation is untouched.
            buspro.register_telegram_received_all_messages_cb(previous_cb)

        found = sorted(
            self._found.values(), key=lambda d: (d.subnet_id, d.device_id)
        )
        self._log_summary(found, duration)
        return found

    async def _broadcast_provocations(self) -> None:
        """Send each provocation read to the broadcast address."""
        ni = self._buspro.network_interface
        if ni is None:
            return
        for operate_code, payload in _PROVOCATIONS:
            telegram = Telegram()
            telegram.target_address = _BROADCAST
            telegram.operate_code = operate_code
            telegram.payload = list(payload)
            try:
                await ni.send_telegram(telegram)
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug(
                    "Provocation send failed (%s): %s", operate_code, err
                )
            await asyncio.sleep(_FRAME_GAP)

    async def _directed_channel_reads(self) -> None:
        """Ask each discovered device directly for its channel status.

        Sent to the device's own address (not broadcast) because most relay and
        dimmer modules only answer a directed ReadStatusOfChannels. The 0x0034
        replies are harvested by the catch-all and populate channel counts.
        """
        ni = self._buspro.network_interface
        if ni is None or not self._found:
            return
        addresses = [
            (d.subnet_id, d.device_id) for d in list(self._found.values())
        ]
        deadline = time.monotonic() + _DIRECTED_MAX_SECONDS
        for _round in range(_DIRECTED_ROUNDS):
            if time.monotonic() >= deadline:
                break
            for subnet, device in addresses:
                if time.monotonic() >= deadline:
                    break
                telegram = Telegram()
                telegram.target_address = (subnet, device)
                telegram.operate_code = OperateCode.ReadStatusOfChannels
                telegram.payload = []
                try:
                    await ni.send_telegram(telegram)
                except Exception as err:  # noqa: BLE001
                    _LOGGER.debug(
                        "Directed read to %s.%s failed: %s", subnet, device, err
                    )
                await asyncio.sleep(_FRAME_GAP)
            # Give the modules time to answer before the next round / finishing.
            await asyncio.sleep(
                min(_DIRECTED_LISTEN, max(deadline - time.monotonic(), 0.0))
            )

    def _log_summary(self, found: list[DiscoveredDevice], duration: float) -> None:
        """Log a human-readable summary of the scan at INFO level."""
        if not found:
            _LOGGER.info(
                "Buspro bus scan (%.0fs): no devices responded. Check that the "
                "gateway IP is correct and Home Assistant can reach UDP port "
                "6000.",
                duration,
            )
            return
        _LOGGER.info(
            "Buspro bus scan (%.0fs): %d device(s) found:", duration, len(found)
        )
        for dev in found:
            _LOGGER.info("  %s", dev.summary())
