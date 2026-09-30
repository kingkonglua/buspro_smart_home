#!/usr/bin/env python3
"""I18N step-key parity check.

Extracts the set of ``async_step_<name>`` handlers defined in
``config_flow.py`` and compares it against the ``config.step`` keys of each
translation JSON.  Fails if any configured step is missing a translation
(missing set) or if a translation exists for a step that no longer exists
(dead set).
"""
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CONFIG_FLOW = os.path.join(
    ROOT, "custom_components", "buspro", "config_flow.py"
)
JSON_FILES = [
    "custom_components/buspro/strings.json",
    "custom_components/buspro/translations/en.json",
    "custom_components/buspro/translations/no.json",
    "custom_components/buspro/translations/zh-Hans.json",
]


def main():
    with open(CONFIG_FLOW, encoding="utf-8") as fh:
        source = fh.read()
    flow_steps = set(re.findall(r"async_step_([a-z0-9_]+)", source))

    print("config_flow async_step_ steps (%d):" % len(flow_steps))
    for name in sorted(flow_steps):
        print("  %s" % name)

    failed = False
    for rel in JSON_FILES:
        path = os.path.join(ROOT, rel)
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        step_keys = set(data.get("config", {}).get("step", {}).keys())

        missing = flow_steps - step_keys
        dead = step_keys - flow_steps

        print("\n%s" % rel)
        print("  step keys: %d" % len(step_keys))
        if missing:
            failed = True
            print("  MISSING (in config_flow, absent from JSON): %s"
                  % sorted(missing))
        if dead:
            failed = True
            print("  DEAD (in JSON, absent from config_flow): %s"
                  % sorted(dead))
        if not missing and not dead:
            print("  OK: no missing, no dead keys")

    if failed:
        print("\nRESULT: FAIL")
        sys.exit(1)
    print("\nRESULT: PASS")
    sys.exit(0)


if __name__ == "__main__":
    main()
