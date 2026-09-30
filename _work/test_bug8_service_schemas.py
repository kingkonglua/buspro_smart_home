#!/usr/bin/env python3
"""BUG-8 focused checks: service schema normalisation + compatibility.

Run: python3 _work/test_bug8_service_schemas.py
     (self-contained: loads voluptuous + homeassistant from _work/pylibs and
      _work/ha_stub; the repo venv works too, e.g.
      /tmp/hdl_venv/bin/python _work/test_bug8_service_schemas.py)
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(HERE, "ha_stub"))
sys.path.insert(0, os.path.join(HERE, "pylibs"))
sys.path.insert(0, ROOT)

import voluptuous as vol  # noqa: E402
import homeassistant.helpers.config_validation as cv  # noqa: E402

from custom_components.buspro import (  # noqa: E402
    SERVICE_BUSPRO_ACTIVATE_SCENE_SCHEMA as S_SCENE,
    SERVICE_BUSPRO_SEND_MESSAGE_SCHEMA as S_MSG,
    SERVICE_BUSPRO_UNIVERSAL_SWITCH_SCHEMA as S_SW,
)

FAILS = []


def check(name, ok, detail=""):
    print(("PASS  " if ok else "FAIL  ") + name + (f"  -> {detail}" if detail and not ok else ""))
    if not ok:
        FAILS.append(name)


def expect_invalid(name, call):
    try:
        call()
        check(name, False, "no exception raised")
    except vol.Invalid as err:
        check(name, True)
        print(f"        (rejected as expected: {err})")


# 1. backwards compatibility: list form still passes, values unchanged. -------
for label, val in (("list [1]", [1]), ("list [1,74]", [1, 74]), ("list [1,74,0]", [1, 74, 0])):
    out = S_SCENE({"address": val, "scene_address": [1, 1]})
    check(f"compat address {label}", out["address"] == val, repr(out.get("address")))

out = S_MSG({"address": [1, 74], "operate_code": [4, 12], "payload": [1, 75, 0, 3]})
check("compat payload [1,75,0,3] preserved", out["payload"] == [1, 75, 0, 3], repr(out.get("payload")))

# 2. dict normalisation -------------------------------------------------------
out = S_SCENE({"address": {"0": 1, "1": 100}, "scene_address": {}})
check("dict numeric -> [1,100]", out["address"] == [1, 100], repr(out.get("address")))
check("empty dict -> []", out["scene_address"] == [], repr(out.get("scene_address")))
out = S_SCENE({"address": {"subnet_id": 1, "device_id": 42}, "scene_address": {"1": 1, "0": 3}})
check("dict named -> [1,42]", out["address"] == [1, 42], repr(out.get("address")))
check("dict numeric unordered -> [3,1]", out["scene_address"] == [3, 1], repr(out.get("scene_address")))
out = S_SCENE({"address": {"device_id": 42, "subnet_id": 1}, "scene_address": [1, 1]})
check("dict named reordered -> [1,42]", out["address"] == [1, 42], repr(out.get("address")))

# 3. stringified documented example ------------------------------------------
out = S_MSG({"address": "[1, 42]", "operate_code": "[4, 12]", "payload": "[1, 75]"})
check("string list parsed", out["payload"] == [1, 75], repr(out.get("payload")))

# 4. scalar unwrap for switch_number / status ---------------------------------
out = S_SW({"address": {"0": 1, "1": 100}, "switch_number": [1], "status": [1]})
check("scalar [1] unwrapped", out["switch_number"] == 1 and out["status"] == 1, repr(out))
out = S_SW({"address": [1, 100], "switch_number": 5, "status": 0})
check("scalar int preserved", out["switch_number"] == 5 and out["status"] == 0, repr(out))

# 5. invalid inputs still rejected -------------------------------------------
expect_invalid("rejects address 5",
               lambda: S_SCENE({"address": 5, "scene_address": [1, 1]}))
expect_invalid("rejects address {'a':1}",
               lambda: S_SCENE({"address": {"a": 1}, "scene_address": [1, 1]}))
expect_invalid("rejects scalar {'a':1}",
               lambda: S_SW({"address": [1, 100], "switch_number": {"a": 1}, "status": 1}))
expect_invalid("rejects scalar [1,2]",
               lambda: S_SW({"address": [1, 100], "switch_number": [1, 2], "status": 1}))

# 6. the original vol.Any([cv.positive_int]) construct is untouched -----------
inner = vol.Any([cv.positive_int])
for v in ([1], [1, 74], [1, 74, 0]):
    check(f"inner accepts {v}", inner(v) == v)
for v in (5, {"a": 1}, "[1]"):
    expect_invalid(f"inner rejects {v!r}", lambda v=v: inner(v))

print()
print(f"TOTAL FAIL={len(FAILS)}  ({', '.join(FAILS) if FAILS else 'none'})")
sys.exit(1 if FAILS else 0)
