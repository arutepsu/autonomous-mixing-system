"""Local contract checks for the FL host script. Run with standard Python: python scripts/fl_studio/test_gain_trial.py

The simulated host modules expose only the documented MIDI-scripting names the script needs (Image-Line FL Studio API
stubs), so a call to an undocumented name fails here instead of inside FL Studio.
"""

import ast
import contextlib
import io
import runpy
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

SCRIPT = Path(__file__).with_name("device_gain_trial.py")
# Documented calls (module -> names) the trial may use; checked against the script's source below.
DOCUMENTED = {
    "general": {"getVersion", "safeToEdit"},
    "ui": {"getVersion", "getProgTitle"},
    "mixer": {"trackCount", "getTrackName", "getTrackVolume", "setTrackVolume"},
    "transport": {"isPlaying"},
}
TITLE = "gain-trial-copy.flp - FL Studio 2026"
BASE = dict(PROJECT_FILENAME="gain-trial-copy.flp", INSERT_INDEX=3, INSERT_NAME="Bass")
APPLY = dict(MODE="apply", REFERENCE=(0.8, 0.0), INTENDED_DB_CHANGE=-0.5, TARGET_NORMALIZED=0.78)


class TrialTest(unittest.TestCase):
    def setUp(self):
        self.level = (0.8, 0.0)
        self.writes = []
        self.api = 38

        def set_volume(index, value):
            self.writes.append((index, value))
            self.level = (value, -0.52)

        self.host = {
            "general": SimpleNamespace(getVersion=lambda: self.api, safeToEdit=lambda: True),
            "ui": SimpleNamespace(getVersion=lambda _: "26.1.7.5654", getProgTitle=lambda: TITLE),
            "mixer": SimpleNamespace(trackCount=lambda: 127, getTrackName=lambda _: "Bass",
                                     getTrackVolume=lambda _, mode: self.level[mode],
                                     setTrackVolume=set_volume),
            "transport": SimpleNamespace(isPlaying=lambda: False),
        }  # fmt: skip

    def run_script(self, **config):
        with patch.dict(sys.modules, self.host):
            trial = runpy.run_path(str(SCRIPT))
        callback = trial["OnInit"]
        callback.__globals__.update(BASE)
        callback.__globals__.update(config)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            callback()
        return output.getvalue()

    def test_script_calls_only_documented_names(self):
        used = {}
        for node in ast.walk(ast.parse(SCRIPT.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id in DOCUMENTED:
                used.setdefault(node.value.id, set()).add(node.attr)
        for module, names in used.items():
            self.assertLessEqual(names, DOCUMENTED[module], module)
        self.assertNotIn("getTrackCount", used.get("mixer", set()))  # undocumented; the API has trackCount()

    def test_inspect_records_identity_and_gain(self):
        output = self.run_script()
        for line in ("fl_studio=26.1.7.5654", "scripting_api=38", "insert=3", "insert_name='Bass'",
                     "current_normalized=0.800000", "current_db=0.000000"):  # fmt: skip
            self.assertIn(f"RESULT {line}", output)
        self.assertEqual(self.writes, [])

    def test_apply_records_intent_separately_from_measurement_and_prevents_second_write(self):
        output = self.run_script(**APPLY)
        for line in ("intended_db_change=-0.500000", "sent_normalized=0.780000", "candidate_normalized=0.780000",
                     "candidate_db=-0.520000", "measured_db_change=-0.520000",
                     "intended_minus_measured_db=+0.020000"):  # fmt: skip
            self.assertIn(f"RESULT {line}", output)
        self.assertIn("intended dB change reached within tolerance", output)
        self.assertIn("APPLIED", output)
        with self.assertRaises(RuntimeError):
            self.run_script(**APPLY)  # the fader no longer matches the baseline reference
        self.assertEqual(self.writes, [(3, 0.78)])

    def test_apply_labels_a_bounded_normalized_change_when_intent_is_missed(self):
        output = self.run_script(**(APPLY | {"INTENDED_DB_CHANGE": -1.0}))
        self.assertIn("RESULT intended_minus_measured_db=-0.480000", output)
        self.assertIn("bounded normalized-fader change", output)
        self.assertIn("APPLIED", output)

    def test_reject_unsafe_target_states_before_write(self):
        for overrides in (
            {"PROJECT_FILENAME": "original.flp"},
            {"PROJECT_FILENAME": ""},
            {"INSERT_INDEX": 0},  # Master
            {"INSERT_INDEX": 126},  # the "current" track
            {"INSERT_NAME": "Kick"},
            {"TARGET_NORMALIZED": 0.84},  # a raise
            {"TARGET_NORMALIZED": 0.7},  # cut beyond 0.03
            {"TARGET_NORMALIZED": 0.799},  # cut within the read-back tolerance
            {"INTENDED_DB_CHANGE": None},
            {"INTENDED_DB_CHANGE": 0.5},
            {"INTENDED_DB_CHANGE": -3.5},
            {"REFERENCE": (0.8, -1.0)},
            {"MODE": "write"},
        ):
            with self.subTest(overrides=overrides), self.assertRaises(RuntimeError):
                self.run_script(**(APPLY | overrides))
        self.assertEqual(self.writes, [])

    def test_reject_old_api_and_playing_transport(self):
        self.api = 28
        with self.assertRaises(RuntimeError):
            self.run_script(**APPLY)
        self.api = 38
        self.host["transport"].isPlaying = lambda: True
        with self.assertRaises(RuntimeError):
            self.run_script(**APPLY)
        self.assertEqual(self.writes, [])

    def test_noop_readback_fails_without_retry(self):
        def ignore(index, value):
            self.writes.append((index, value))

        self.host["mixer"].setTrackVolume = ignore
        with self.assertRaises(RuntimeError):
            self.run_script(**APPLY)
        self.assertEqual(self.writes, [(3, 0.78)])

    def test_reopened_db_tolerance(self):
        self.level = (0.78, -0.54)
        self.assertIn("VERIFIED", self.run_script(MODE="verify", REFERENCE=(0.78, -0.5)))
        self.level = (0.78, -0.56)
        with self.assertRaises(RuntimeError):
            self.run_script(MODE="verify", REFERENCE=(0.78, -0.5))
        self.assertEqual(self.writes, [])


if __name__ == "__main__":
    unittest.main()
