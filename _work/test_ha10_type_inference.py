#!/usr/bin/env python3
"""HA10 red/green test: scan-import type inference gaps.

Two suspected gaps are put under test before any code is touched:

  T1 (gap A)  A module that never answers a probe (op_codes empty, not a
              keypad) is currently guessed as a plain switch even when its
              hardware type code identifies it (dry contact / dimmer /
              sensor / curtain).  infer_device_type() must use the type-code
              fallback instead of the generic switch guess.
  T2 (regr.)  Reply operate codes must keep priority over the type-code table
              (AC / curtain / sensor / dry contact / universal switch / floor
              heating), and a channel reply without dimmer evidence must
              still yield a switch even if the type code says "dimmer".
  T3 (gap B)  A multi-zone dry-contact module must import as one entity per
              zone (channel = zone number), not a single entity.  The
              binary_sensor platform supports this (subtypes dry_contact_1 /
              dry_contact_2 and per-channel dry_contact).

Run:  /tmp/hdl_venv/bin/python _work/test_ha10_type_inference.py ; echo "rc=$?"
Exit code 0 = PASS, non-zero = FAIL.  Final line: TOTAL PASS=n FAIL=m.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
# Self-contained like the other _work tests: stub homeassistant + voluptuous.
sys.path.insert(0, os.path.join(HERE, "ha_stub"))
sys.path.insert(0, os.path.join(HERE, "pylibs"))
sys.path.insert(0, ROOT)

from custom_components.buspro.const import (  # noqa: E402
    CONF_CHANNEL,
    DEVICE_TYPE_AC,
    DEVICE_TYPE_BINARY_SENSOR,
    DEVICE_TYPE_CLIMATE,
    DEVICE_TYPE_CURTAIN,
    DEVICE_TYPE_LIGHT,
    DEVICE_TYPE_SENSOR,
    DEVICE_TYPE_SWITCH,
)
from custom_components.buspro.discovery import (  # noqa: E402
    DiscoveredDevice,
    infer_device_type,
)

PASS = 0
FAIL = 0
FAILURES = []


def check(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print("PASS  " + name)
    else:
        FAIL += 1
        FAILURES.append(name)
        print("FAIL  " + name + (("  -> " + detail) if detail else ""))


def dev(type_code, type_name="Unknown", ops=(), channel_count=None):
    d = DiscoveredDevice(
        subnet_id=1,
        device_id=10,
        type_code=type_code,
        type_name=type_name,
        channel_count=channel_count,
    )
    d.op_codes = set(ops)
    return d


# ---------------------------------------------------------------------------
# T1 -- gap A: silent modules must not be guessed as plain switches
# ---------------------------------------------------------------------------
print("== T1: type-code fallback for silent modules (gap A) ==")

silent_dry = dev("0x0077", "SB_DRY_4Z")
check("T1a silent dry-contact is not a keypad", silent_dry.looks_like_keypad is False)
got = infer_device_type(silent_dry)
check("T1b silent dry-contact not guessed as switch", got != DEVICE_TYPE_SWITCH, f"got={got!r}")
check(
    "T1c silent dry-contact classified as binary_sensor",
    got == DEVICE_TYPE_BINARY_SENSOR,
    f"got={got!r}",
)

check(
    "T1d silent 6ch dimmer (0x026D) -> light",
    infer_device_type(dev("0x026D", "HDL_MDT0601")) == DEVICE_TYPE_LIGHT,
)
check(
    "T1e silent sensors-in-one (0x0150) -> sensor",
    infer_device_type(dev("0x0150", "HDL_MSP07M")) == DEVICE_TYPE_SENSOR,
)
check(
    "T1f silent curtain module (0x25E5) -> curtain",
    infer_device_type(dev("0x25E5", "Unknown")) == DEVICE_TYPE_CURTAIN,
)
check(
    "T1g silent relay (0x01AC) -> switch is now a *known* answer",
    infer_device_type(dev("0x01AC", "SB_DN_R0816")) == DEVICE_TYPE_SWITCH,
)
check(
    "T1h unknown silent type still falls back to switch",
    infer_device_type(dev("0x0000", "Unknown")) == DEVICE_TYPE_SWITCH,
)

# ---------------------------------------------------------------------------
# T2 -- regression: reply operate codes keep priority over the type table
# ---------------------------------------------------------------------------
print("== T2: reply operate codes keep priority (regression) ==")

check(
    "T2a ReadAcStatusResponse -> ac",
    infer_device_type(dev("0x0000", ops={"ReadAcStatusResponse"})) == DEVICE_TYPE_AC,
)
check(
    "T2b ReadStatusOfCurtainSwitchResponse -> curtain",
    infer_device_type(dev("0x0000", ops={"ReadStatusOfCurtainSwitchResponse"}))
    == DEVICE_TYPE_CURTAIN,
)
check(
    "T2c ReadSensorsInOneStatusResponse -> sensor",
    infer_device_type(dev("0x0000", ops={"ReadSensorsInOneStatusResponse"}))
    == DEVICE_TYPE_SENSOR,
)
check(
    "T2d ReadDryContactStatusResponse -> binary_sensor",
    infer_device_type(dev("0x0000", ops={"ReadDryContactStatusResponse"}))
    == DEVICE_TYPE_BINARY_SENSOR,
)
check(
    "T2e ReadStatusOfUniversalSwitchResponse -> binary_sensor",
    infer_device_type(dev("0x0000", ops={"ReadStatusOfUniversalSwitchResponse"}))
    == DEVICE_TYPE_BINARY_SENSOR,
)
check(
    "T2f ReadFloorHeatingStatusResponse -> climate",
    infer_device_type(dev("0x0000", ops={"ReadFloorHeatingStatusResponse"}))
    == DEVICE_TYPE_CLIMATE,
)
check(
    "T2g reply wins over a conflicting type code (0x0077 + AC reply)",
    infer_device_type(dev("0x0077", "SB_DRY_4Z", ops={"ReadAcStatusResponse"}))
    == DEVICE_TYPE_AC,
)
check(
    "T2h channel reply without dimmer evidence -> switch even if type is dimmer",
    infer_device_type(dev("0x026D", "HDL_MDT0601", ops={"ReadStatusOfChannelsResponse"}))
    == DEVICE_TYPE_SWITCH,
)

# ---------------------------------------------------------------------------
# T3 -- gap B: multi-zone dry-contact import
# ---------------------------------------------------------------------------
print("== T3: multi-zone dry-contact import (gap B) ==")

from custom_components.buspro.config_flow import BusproOptionsFlow  # noqa: E402

two_zone = dev(
    "0x0000", "Unknown", ops={"ReadDryContactStatusResponse"}, channel_count=2
)
cfgs = BusproOptionsFlow._import_discovered(None, two_zone, DEVICE_TYPE_BINARY_SENSOR)
check(
    "T3a 2-zone dry contact imports 2 entities",
    len(cfgs) == 2,
    f"got {len(cfgs)}: {[k for k, _ in cfgs]}",
)
channels = sorted(cfg.get(CONF_CHANNEL) for _, cfg in cfgs)
check("T3b zones map to channels 1 and 2", channels == [1, 2], f"channels={channels}")

# Known 4-zone SB_DRY_4Z, silent (classified via the type-code fallback).
known4 = dev("0x0077", "SB_DRY_4Z")
cfgs4 = BusproOptionsFlow._import_discovered(None, known4, DEVICE_TYPE_BINARY_SENSOR)
check(
    "T3c SB_DRY_4Z (0x0077) imports 4 entities",
    len(cfgs4) == 4,
    f"got {len(cfgs4)}: {[k for k, _ in cfgs4]}",
)

# Universal-switch modules must not be caught by the dry-contact split.
usw = dev("0x0517", "Unknown", ops={"ReadStatusOfUniversalSwitchResponse"})
cfgs_usw = BusproOptionsFlow._import_discovered(
    None, usw, DEVICE_TYPE_BINARY_SENSOR
)
check(
    "T3d universal switch stays a single entity",
    len(cfgs_usw) == 1,
    f"got {len(cfgs_usw)}: {[k for k, _ in cfgs_usw]}",
)

def test_ha10_type_inference():
    """Pytest entry point: same judgement as the standalone script below."""
    assert FAIL == 0, "HA10 checks failed: " + ", ".join(FAILURES)


if __name__ == "__main__":
    print()
    print(f"TOTAL PASS={PASS} FAIL={FAIL}")
    if FAILURES:
        print("FAILED: " + ", ".join(FAILURES))
    sys.exit(0 if FAIL == 0 else 1)
