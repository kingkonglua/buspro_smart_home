#!/usr/bin/env python3
"""i18n parity regression test for custom_components/buspro.

Asserts:
  A. All 4 JSON files (strings.json + 3 translations) load with json.load.
  B. Each translation's top-level key set is exactly equal to strings.json.
  C. Recursive: every path present in strings.json also exists in the
     translation (no missing key). Extra translation-only paths are allowed,
     EXCEPT under `services`, where the path set must match strings.json
     exactly (neither missing nor extra).
  D. config.abort's key set is a superset of every async_abort(reason="...")
     reason extracted from config_flow.py.
  E. config.step covers every step in config_flow.py that calls
     async_show_form / async_show_progress_done.

Exit code 0 = PASS, non-zero = FAIL.
"""

import json
import os
import re
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
COMPONENT = os.path.join(HERE, "..", "custom_components", "buspro")
STRINGS = os.path.join(COMPONENT, "strings.json")
TRANSLATIONS = [
    os.path.join(COMPONENT, "translations", "zh-Hans.json"),
    os.path.join(COMPONENT, "translations", "en.json"),
    os.path.join(COMPONENT, "translations", "no.json"),
]
CONFIG_FLOW = os.path.join(COMPONENT, "config_flow.py")

failures = []


def fail(msg):
    failures.append(msg)
    print("FAIL: " + msg)


def load_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def collect_paths(obj, prefix=""):
    """Return the set of dotted paths for a nested dict."""
    paths = set()
    if isinstance(obj, dict):
        for key, value in obj.items():
            path = f"{prefix}.{key}" if prefix else key
            paths.add(path)
            paths |= collect_paths(value, path)
    return paths


def rel(path):
    return os.path.relpath(path, os.path.join(HERE, ".."))


def _load_all():
    print("== A. json.load all 4 files ==")
    loaded = {}
    for path in [STRINGS] + TRANSLATIONS:
        try:
            loaded[path] = load_json(path)
            print("  OK   %s" % rel(path))
        except Exception as exc:  # noqa: BLE001
            fail("A: %s failed json.load: %s" % (rel(path), exc))
    return loaded


def test_a_load():
    _load_all()


def test_b_top_keys(strings, translations_data):
    print("== B. top-level key set parity ==")
    expected = set(strings.keys())
    for path, data in translations_data.items():
        actual = set(data.keys())
        if actual != expected:
            fail(
                "B: %s top keys mismatch; missing=%s extra=%s"
                % (rel(path), sorted(expected - actual), sorted(actual - expected))
            )
        else:
            print("  OK   %s top keys == %s" % (rel(path), sorted(expected)))


def test_c_recursive(strings, translations_data):
    print("== C. recursive path parity ==")
    strings_paths = collect_paths(strings)
    strings_services = {p for p in strings_paths if p == "services" or p.startswith("services.")}
    for path, data in translations_data.items():
        data_paths = collect_paths(data)
        missing = sorted(strings_paths - data_paths)
        if missing:
            for p in missing:
                fail("C: %s missing path: %s" % (rel(path), p))
        else:
            print("  OK   %s has every strings.json path" % rel(path))

        data_services = {p for p in data_paths if p == "services" or p.startswith("services.")}
        only_in_translation = sorted(data_services - strings_services)
        only_in_strings = sorted(strings_services - data_services)
        if only_in_translation or only_in_strings:
            fail(
                "C: %s services path mismatch; missing=%s extra=%s"
                % (rel(path), only_in_strings, only_in_translation)
            )
        else:
            print("  OK   %s services paths == strings.json" % rel(path))


def parse_config_flow():
    with open(CONFIG_FLOW, encoding="utf-8") as fh:
        return fh.read()


def test_d_abort_reasons(strings, source):
    print("== D. config.abort vs async_abort reasons ==")
    reasons = set(re.findall(r'async_abort\(\s*reason\s*=\s*["\']([^"\']+)["\']', source))
    expected = set(strings.get("config", {}).get("abort", {}).keys())
    missing = sorted(reasons - expected)
    if missing:
        fail("D: config.abort missing reasons: %s" % missing)
    else:
        print("  OK   config.abort covers %s" % sorted(reasons))


def test_e_form_steps(strings, source):
    print("== E. config.step covers async_show_form/progress_done steps ==")
    # Split source into async_step_* method bodies.
    matches = list(re.finditer(r"^(\s*)async def async_step_(\w+)\s*\(", source, re.M))
    required = set()
    for idx, match in enumerate(matches):
        name = match.group(2)
        start = match.start()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(source)
        body = source[start:end]
        if "async_show_form" in body or "async_show_progress_done" in body:
            required.add(name)
    expected = set(strings.get("config", {}).get("step", {}).keys())
    missing = sorted(required - expected)
    if missing:
        fail("E: config.step missing steps: %s" % missing)
    else:
        print("  OK   config.step covers %s" % sorted(required))


def main():
    loaded = _load_all()
    if STRINGS not in loaded:
        print("\nRESULT: FAIL (%d failures)" % len(failures))
        return 1
    strings = loaded[STRINGS]
    translations_data = {p: d for p, d in loaded.items() if p != STRINGS}

    test_b_top_keys(strings, translations_data)
    test_c_recursive(strings, translations_data)
    source = parse_config_flow()
    test_d_abort_reasons(strings, source)
    test_e_form_steps(strings, source)

    if failures:
        print("\nRESULT: FAIL (%d failures)" % len(failures))
        return 1
    print("\nRESULT: PASS (all parity assertions satisfied)")
    return 0


# ---------------------------------------------------------------------------
# Pytest wiring
#
# The check functions below request ``strings`` / ``translations_data`` /
# ``source``.  Originally they were plain module globals fed by ``main()``, so
# pytest read the parameter names as missing fixtures.  These fixtures provide
# the identical data, and the autouse guard turns the functions' ``failures``
# log into a genuine assertion -- the FAIL/PASS judgement is unchanged.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def strings():
    return load_json(STRINGS)


@pytest.fixture(scope="module")
def translations_data():
    return {path: load_json(path) for path in TRANSLATIONS}


@pytest.fixture(scope="module")
def source():
    return parse_config_flow()


@pytest.fixture(scope="module", autouse=True)
def _parity_failures_guard():
    failures.clear()
    yield
    assert not failures, "i18n parity failures: %s" % (failures,)


if __name__ == "__main__":
    sys.exit(main())
