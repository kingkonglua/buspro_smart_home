#!/usr/bin/env python3
"""BUG-7 red/green test: device-type / device-list copy must be localized.

Covers the five defects filed as BUG-7:
  D-1 discovery.SCAN_TYPE_LABELS held 8 Chinese literals (scan checklist).
  D-2 _get_device_display_name injected the internal type key
      (switch/light/binary_sensor/...) into translated placeholders.
  D-3 the empty device list rendered the English literal "  (none)".
  D-4 "No devices to remove." was a hardcoded English placeholder value.
  D-5 config.device_type.* translation keys did not exist at all.

Assertions (RED-1 .. RED-5), run with the real HA interpreter
(/tmp/opencode/haenv/bin/python):

  RED-1  discovery.py contains no CJK in code (comments exempt).
  RED-2  config.device_type.* key set identical across all 4 JSON files and
         the 8 scan type values are non-empty.
  RED-3  _get_device_display_name (real HA interpreter, stub hass/entry) does
         not leak internal type-key literals such as "(switch" / "(light".
  RED-4  config_flow.py no longer contains "  (none)" or
         "No devices to remove." literals.
  RED-5  cross-language render: the same display logic, driven by en vs
         zh-Hans, yields *different* localized type names (not just a
         different surrounding description).

Exit code 0 = PASS, non-zero = FAIL. Final line: TOTAL PASS=N FAIL=M.
"""

import io
import json
import os
import re
import sys
import tokenize
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
COMPONENT = REPO / "custom_components" / "buspro"
DISCOVERY = COMPONENT / "discovery.py"
CONFIG_FLOW = COMPONENT / "config_flow.py"

FILES = {
    "strings": COMPONENT / "strings.json",
    "zh-Hans": COMPONENT / "translations" / "zh-Hans.json",
    "en": COMPONENT / "translations" / "en.json",
    "no": COMPONENT / "translations" / "no.json",
}

# The scan classification types exposed by SCAN_TYPE_LABELS (7 + unknown).
REQUIRED_TYPE_KEYS = [
    "switch",
    "light",
    "sensor",
    "climate",
    "ac",
    "curtain",
    "binary_sensor",
    "unknown",
]

CJK = re.compile(r"[\u4e00-\u9fff]")

PASS = 0
FAIL = 0
FAILURES = []


def check(name, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print("PASS: %s" % name)
    else:
        FAIL += 1
        FAILURES.append(name)
        print("FAIL: %s%s" % (name, (" -- " + detail) if detail else ""))


def load_json(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def source_without_comments(src):
    """Return *src* with every comment token blanked out (comments exempt)."""
    src_lines = src.splitlines(keepends=True)
    offsets = [0]
    for line in src_lines:
        offsets.append(offsets[-1] + len(line))

    def pos_to_index(row, col):
        return offsets[row - 1] + col

    chars = list(src)
    try:
        tokens = tokenize.generate_tokens(io.StringIO(src).readline)
        for tok in tokens:
            if tok.type == tokenize.COMMENT:
                for idx in range(pos_to_index(*tok.start), pos_to_index(*tok.end)):
                    chars[idx] = " "
    except (tokenize.TokenError, IndentationError):
        # A tokenizer failure must not silently hide CJK.
        return src
    return "".join(chars)


def import_options_flow():
    if str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))
    from custom_components.buspro.config_flow import BusproOptionsFlow

    return BusproOptionsFlow


def make_flow(language):
    """Instantiate BusproOptionsFlow with a minimal stub hass/entry."""
    flow_cls = import_options_flow()

    class ConfigEntries:
        def async_get_known_entry(self, entry_id):
            return SimpleNamespace(
                entry_id=entry_id,
                domain="buspro",
                options={},
            )

    class Hass:
        pass

    hass = Hass()
    hass.config = SimpleNamespace(language=language)
    hass.config_entries = ConfigEntries()

    flow = flow_cls()
    flow.hass = hass
    flow.handler = "bug7-test-entry"
    return flow


def device_config(device_type, name="Living room lamp"):
    return {
        "name": name,
        "device_type": device_type,
        "subnet_id": 1,
        "device_id": 2,
        "channel": 3,
    }


def test_red_1_no_cjk_in_discovery():
    print("== RED-1: discovery.py has no CJK in code (comments exempt) ==")
    src = DISCOVERY.read_text(encoding="utf-8")
    code = source_without_comments(src)
    hits = [
        (i, line.strip())
        for i, line in enumerate(code.splitlines(), 1)
        if CJK.search(line)
    ]
    for lineno, text in hits:
        print("  discovery.py:%d: %s" % (lineno, text))
    check(
        "RED-1 discovery.py no CJK code/literals (found %d)" % len(hits),
        not hits,
        "%d CJK hit(s): %s" % (len(hits), hits[:3]),
    )


def _device_type_maps():
    maps = {}
    for lang, path in FILES.items():
        data = load_json(path)
        node = data.get("config", {}).get("device_type")
        if not isinstance(node, dict):
            return lang, None
        maps[lang] = node
    return None, maps


def test_red_2_device_type_keys():
    print("== RED-2: config.device_type.* parity + non-empty in 4 JSON files ==")
    missing_lang, maps = _device_type_maps()
    if maps is None:
        check(
            "RED-2 config.device_type present in all 4 JSON files",
            False,
            "%s: missing config.device_type section" % missing_lang,
        )
        return
    key_sets = {lang: set(node) for lang, node in maps.items()}
    ref_lang = "en"
    ref = key_sets[ref_lang]
    for lang, keys in key_sets.items():
        check(
            "RED-2 %s config.device_type key set == %s" % (lang, ref_lang),
            keys == ref,
            "missing=%s extra=%s" % (sorted(ref - keys), sorted(keys - ref)),
        )
    for key in REQUIRED_TYPE_KEYS:
        bad = [
            lang
            for lang, node in maps.items()
            if not node.get(key)
        ]
        check(
            "RED-2 required key config.device_type.%s non-empty in all 4" % key,
            not bad,
            "empty/missing in %s" % bad,
        )


def test_red_3_no_internal_key_leak():
    print("== RED-3: _get_device_display_name does not leak internal type keys ==")
    for internal in ("switch", "light", "binary_sensor"):
        for language in ("en", "zh-Hans"):
            try:
                flow = make_flow(language)
                rendered = flow._get_device_display_name(
                    "%s_1_2_3" % internal,
                    device_config(internal),
                )
            except Exception as exc:  # noqa: BLE001
                check(
                    "RED-3 %s display name renders (%s)" % (internal, language),
                    False,
                    "%s: %s" % (type(exc).__name__, exc),
                )
                continue
            pattern = "(%s" % internal
            check(
                "RED-3 %s/%s no %r leak" % (internal, language, pattern),
                pattern not in rendered,
                "rendered=%r" % rendered,
            )


def test_red_4_no_english_literals():
    print("== RED-4: no hardcoded empty-list / remove literals in config_flow.py ==")
    src = CONFIG_FLOW.read_text(encoding="utf-8")
    check(
        'RED-4 config_flow.py has no "  (none)" literal',
        '"  (none)"' not in src,
    )
    check(
        'RED-4 config_flow.py has no "No devices to remove." literal',
        '"No devices to remove."' not in src,
    )


def test_red_5_cross_language_render():
    print("== RED-5: en vs zh-Hans render different localized type names ==")
    try:
        en = load_json(FILES["en"])
        zh = load_json(FILES["zh-Hans"])
        en_type = en["config"]["device_type"]["light"]
        zh_type = zh["config"]["device_type"]["light"]
    except (KeyError, FileNotFoundError, json.JSONDecodeError) as exc:
        check(
            "RED-5 device_type.light present in en + zh-Hans",
            False,
            "%s: %s" % (type(exc).__name__, exc),
        )
        return
    print("  en device_type.light=%r  zh-Hans device_type.light=%r" % (en_type, zh_type))
    check(
        "RED-5 en/zh-Hans device_type.light differ",
        en_type != zh_type,
        "en=%r zh=%r" % (en_type, zh_type),
    )

    try:
        cfg = device_config("light", name="Living room lamp")
        en_flow = make_flow("en")
        zh_flow = make_flow("zh-Hans")
        en_dev = en_flow._get_device_display_name("light_1_2_3", cfg)
        zh_dev = zh_flow._get_device_display_name("light_1_2_3", cfg)
    except Exception as exc:  # noqa: BLE001
        check(
            "RED-5 display name renders in both languages",
            False,
            "%s: %s" % (type(exc).__name__, exc),
        )
        return

    en_tmpl = en["config"]["step"]["init"]["description"]
    zh_tmpl = zh["config"]["step"]["init"]["description"]
    en_rendered = en_tmpl.replace("{devices}", "  - " + en_dev)
    zh_rendered = zh_tmpl.replace("{devices}", "  - " + zh_dev)
    print("  en render: %r" % en_rendered)
    print("  zh render: %r" % zh_rendered)

    check(
        "RED-5 en device string carries the English type name",
        en_type in en_dev,
        "en_dev=%r expected to contain %r" % (en_dev, en_type),
    )
    check(
        "RED-5 zh-Hans device string carries the Chinese type name",
        zh_type in zh_dev,
        "zh_dev=%r expected to contain %r" % (zh_dev, zh_type),
    )
    check(
        "RED-5 same device renders differently per language",
        en_dev != zh_dev,
        "en_dev=%r zh_dev=%r" % (en_dev, zh_dev),
    )


def main():
    test_red_1_no_cjk_in_discovery()
    test_red_2_device_type_keys()
    test_red_3_no_internal_key_leak()
    test_red_4_no_english_literals()
    test_red_5_cross_language_render()

    print()
    print("TOTAL PASS=%d FAIL=%d" % (PASS, FAIL))
    if FAILURES:
        print("FAILED CHECKS: %s" % FAILURES)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
