#!/usr/bin/env python3
"""i18n regression test for the options-flow `init` action dropdown.

Asserts:
  1. config_flow.py no longer passes a hand-written {label: text} dict to
     vol.In(...) -- option labels must come from translations.
  2. config_flow.py contains no CJK characters at all (no hardcoded Chinese
     UI text that bypasses the translation files).
  3. All 4 JSON files expose options.step.init.data, with an identical key
     set that contains the four action options; every value is non-empty and
     mutually distinct.
  4. The recursive key sets of strings.json and the 3 translations agree,
     reusing test_i18n_parity.py's comparison logic.
  5. Every action option value in the vol.In([...]) list has a label in all
     4 files.

Exit code 0 = PASS, non-zero = FAIL.
"""

import os
import re
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import test_i18n_parity as parity  # noqa: E402 - reuse its comparison logic

COMPONENT = parity.COMPONENT
CONFIG_FLOW = parity.CONFIG_FLOW
STRINGS = parity.STRINGS
TRANSLATIONS = parity.TRANSLATIONS
FILES = [STRINGS] + TRANSLATIONS
ACTIONS = ["add", "scan_bus", "remove", "done"]

failures = []


def fail(msg):
    failures.append(msg)
    print("FAIL: " + msg)


def rel(path):
    return os.path.relpath(path, os.path.join(HERE, ".."))


def test_1_no_hardcoded_volin_dict(src):
    print("== 1. no hardcoded vol.In({...}) label dict in config_flow.py ==")
    found = False
    for match in re.finditer(r"vol\.In\s*\(", src):
        window = src[match.start():match.start() + 300]
        # "key": "value" form => hand-written label dict.
        if re.search(r'"[^"\n]+"\s*:\s*"', window):
            found = True
            line = src[:match.start()].count("\n") + 1
            fail(
                "1: vol.In( at config_flow.py:%d carries an inline "
                "key:value label dict: %r" % (line, window[:70].replace("\n", "\\n"))
            )
    if not found:
        print("  OK   no inline label dict passed to vol.In()")


def test_2_no_cjk(src):
    print("== 2. config_flow.py contains no CJK characters ==")
    hits = [
        (i, line)
        for i, line in enumerate(src.splitlines(), 1)
        if re.search(r"[\u4e00-\u9fff]", line)
    ]
    if hits:
        for i, line in hits:
            fail("2: CJK at config_flow.py:%d: %s" % (i, line.strip()))
    else:
        print("  OK   no CJK characters in config_flow.py")


def _init_data(data):
    return data.get("options", {}).get("step", {}).get("init", {}).get("data")


def test_3_json_action_labels(loaded):
    print("== 3. options.step.init.data present & consistent in all 4 JSON files ==")
    key_sets = {}
    for path in FILES:
        data = _init_data(loaded.get(path, {}))
        if not isinstance(data, dict):
            fail("3: %s missing options.step.init.data" % rel(path))
            continue
        key_sets[path] = set(data.keys())
        print("  %s keys=%s" % (rel(path), sorted(data.keys())))

    if len(key_sets) == len(FILES):
        base = key_sets[STRINGS]
        for path, keys in key_sets.items():
            if keys != base:
                fail(
                    "3: %s options.step.init.data key set differs from "
                    "strings.json: %s vs %s"
                    % (rel(path), sorted(keys), sorted(base))
                )
        missing = [a for a in ACTIONS if a not in base]
        if missing:
            fail("3: options.step.init.data missing action keys: %s" % missing)
        else:
            print("  OK   identical key set across all 4 files, incl. 4 action keys")

    for path in FILES:
        data = _init_data(loaded.get(path, {})) or {}
        vals = list(data.values())
        if any(not v for v in vals):
            fail("3: %s has an empty label: %s" % (rel(path), data))
        elif len(set(vals)) != len(vals):
            fail("3: %s labels are not mutually distinct: %s" % (rel(path), vals))
        else:
            print("  OK   %s every label non-empty & distinct: %s" % (rel(path), vals))


def test_4_recursive_parity(loaded):
    print("== 4. recursive key parity (reusing test_i18n_parity logic) ==")
    strings_paths = parity.collect_paths(loaded[STRINGS])
    strings_services = {
        p for p in strings_paths if p == "services" or p.startswith("services.")
    }
    for path in TRANSLATIONS:
        data_paths = parity.collect_paths(loaded[path])
        missing = sorted(strings_paths - data_paths)
        if missing:
            fail("4: %s missing strings.json paths: %s" % (rel(path), missing))
        else:
            print("  OK   %s has every strings.json path" % rel(path))
        data_services = {
            p for p in data_paths if p == "services" or p.startswith("services.")
        }
        if data_services != strings_services:
            fail(
                "4: %s services path mismatch; missing=%s extra=%s"
                % (
                    rel(path),
                    sorted(strings_services - data_services),
                    sorted(data_services - strings_services),
                )
            )
        else:
            print("  OK   %s services paths match strings.json" % rel(path))


def _action_options(src):
    match = re.search(
        r'vol\.Required\(\s*"action"\s*\)\s*:\s*vol\.In\(\s*\[(.*?)\]\s*\)',
        src,
        re.S,
    )
    if not match:
        return None
    return re.findall(r'"([^"]+)"', match.group(1))


def test_5_options_have_labels(src, loaded):
    print("== 5. every vol.In([...]) action option has a label in all 4 files ==")
    options = _action_options(src)
    if options is None:
        fail("5: could not find vol.In([...]) for the 'action' field")
        return
    print("  vol.In list options = %s" % options)
    if sorted(options) != sorted(ACTIONS):
        fail("5: action options are %s, expected %s" % (options, ACTIONS))
    for path in FILES:
        data = _init_data(loaded.get(path, {})) or {}
        for option in options:
            if not data.get(option):
                fail("5: %s has no label for option %r" % (rel(path), option))
    print("  OK   every option has a label in all 4 files")


def main():
    with open(CONFIG_FLOW, encoding="utf-8") as handle:
        src = handle.read()

    loaded = {}
    for path in FILES:
        try:
            loaded[path] = parity.load_json(path)
        except Exception as exc:  # noqa: BLE001
            fail("json.load failed for %s: %s" % (rel(path), exc))

    test_1_no_hardcoded_volin_dict(src)
    test_2_no_cjk(src)
    if len(loaded) == len(FILES):
        test_3_json_action_labels(loaded)
        test_4_recursive_parity(loaded)
    test_5_options_have_labels(src, loaded)

    if failures:
        print("\nRESULT: FAIL (%d failures)" % len(failures))
        return 1
    print("\nRESULT: PASS (options init action dropdown fully i18n'd)")
    return 0


# ---------------------------------------------------------------------------
# Pytest wiring
#
# Same fix as test_i18n_parity.py: the check functions take ``src`` /
# ``loaded`` as parameters, which pytest mistook for missing fixtures.  These
# fixtures feed them the exact same data ``main()`` used, and the autouse guard
# converts their ``failures`` log into an assertion so the PASS/FAIL verdict is
# identical under pytest and as a standalone script.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def src():
    with open(CONFIG_FLOW, encoding="utf-8") as handle:
        return handle.read()


@pytest.fixture(scope="module")
def loaded():
    data = {}
    for path in FILES:
        try:
            data[path] = parity.load_json(path)
        except Exception as exc:  # noqa: BLE001
            fail("json.load failed for %s: %s" % (rel(path), exc))
    return data


@pytest.fixture(scope="module", autouse=True)
def _action_labels_failures_guard():
    failures.clear()
    yield
    assert not failures, "i18n action-label failures: %s" % (failures,)


if __name__ == "__main__":
    sys.exit(main())
