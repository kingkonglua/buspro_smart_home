"""Regression test: config.progress translations must cover the config flow.

HA resolves a progress card description with the key
``component.<domain>.config.progress.<progress_action>`` (frontend
show-dialog-config-flow.ts).  ``config_flow.py`` uses four progress entry
points (two ``progress_action=`` values and two ``next_step_id=`` values), so
all four keys must exist in every translation file, with the ``seconds_left``
placeholder defined for the countdown action.

Run with: python3 _work/test_progress_i18n.py
"""

import json
import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
COMPONENT = REPO / "custom_components" / "buspro"
CONFIG_FLOW = COMPONENT / "config_flow.py"

JSON_FILES = [
    COMPONENT / "strings.json",
    COMPONENT / "translations" / "zh-Hans.json",
    COMPONENT / "translations" / "en.json",
    COMPONENT / "translations" / "no.json",
]

PLACEHOLDER = "seconds_left"


def _load(path):
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def _string_values(value):
    """Yield every string leaf inside a translation entry (str or dict)."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for nested in value.values():
            yield from _string_values(nested)


def _progress_keys_from_source():
    """Extract the progress_action / next_step_id values used by the flow."""
    source = CONFIG_FLOW.read_text(encoding="utf-8")
    actions = set(re.findall(r'progress_action\s*=\s*"([^"]+)"', source))
    next_steps = set(re.findall(r'next_step_id\s*=\s*"([^"]+)"', source))
    return actions, next_steps


class TestConfigProgressTranslations(unittest.TestCase):
    def test_all_json_files_have_config_progress(self):
        for path in JSON_FILES:
            with self.subTest(path=path.name):
                data = _load(path)
                progress = data.get("config", {}).get("progress")
                self.assertIsInstance(
                    progress,
                    dict,
                    f"{path.name}: missing config.progress section",
                )
                self.assertTrue(
                    progress,
                    f"{path.name}: config.progress is empty",
                )

    def test_every_source_entry_has_a_translation(self):
        actions, next_steps = _progress_keys_from_source()
        expected = actions | next_steps
        self.assertTrue(expected, "no progress_action/next_step_id found in source")
        for path in JSON_FILES:
            with self.subTest(path=path.name):
                progress = _load(path).get("config", {}).get("progress", {})
                missing = expected - set(progress)
                self.assertFalse(
                    missing,
                    f"{path.name}: config.progress missing keys {sorted(missing)}",
                )

    def test_seconds_left_placeholder_is_defined(self):
        actions, _ = _progress_keys_from_source()
        self.assertIn(
            "bus_scan",
            actions,
            "config_flow.py has no progress_action tracking the countdown",
        )
        for path in JSON_FILES:
            with self.subTest(path=path.name):
                progress = _load(path).get("config", {}).get("progress", {})
                self.assertIn("bus_scan", progress, f"{path.name}: no bus_scan entry")
                texts = list(_string_values(progress["bus_scan"]))
                self.assertTrue(
                    any(f"{{{PLACEHOLDER}}}" in text for text in texts),
                    f"{path.name}: bus_scan entry does not define "
                    f"the {{{PLACEHOLDER}}} placeholder",
                )

    def test_progress_key_sets_are_identical(self):
        key_sets = {
            path.name: set(_load(path).get("config", {}).get("progress", {}))
            for path in JSON_FILES
        }
        reference_name, reference = next(iter(key_sets.items()))
        for name, keys in key_sets.items():
            with self.subTest(path=name):
                self.assertEqual(
                    keys,
                    reference,
                    f"{name} config.progress keys differ from {reference_name}",
                )


if __name__ == "__main__":
    unittest.main(verbosity=2)
